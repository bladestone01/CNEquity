"""EastMoney datacenter pagination helper."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from urllib.parse import quote

from ashare_lake.adapters.eastmoney.common import DATACENTER_BASE
from ashare_lake.adapters.eastmoney.em_auth import EastMoneyClient

logger = logging.getLogger(__name__)

_EMPTY_RESULT_MESSAGES = frozenset({"返回数据为空"})

# The server refusing *this moment*, not the request. Reported the same way a
# broken schema is — success=false with a message — so without singling it out
# a busy server surfaced as "rejected schema" and, worse, was raised outside
# the retry loop and never retried. Hit while sweeping the F10 shareholder
# reports, which are the heaviest datacenter consumers here (~110 pages each).
_BUSY_MESSAGES = ("服务器繁忙", "系统繁忙", "请求过于频繁")

# EastMoney datacenter caps pageSize at 500; requesting more returns only 500
# rows and — because the short page looks like the last one — silently
# truncates every high-volume report. Clamp so pagination stays correct.
_MAX_PAGE_SIZE = 500


class EastMoneyDatacenterError(RuntimeError):
    """Raised when datacenter pagination fails after partial or zero results."""


class _TransientEmptyPage(Exception):
    """Empty response on a page the first page's `pages` field says must exist."""


class _ServerBusy(Exception):
    """Datacenter said it was busy — retry, do not report a schema break."""


def _is_busy(message: str) -> bool:
    return any(token in message for token in _BUSY_MESSAGES)


def fetch_datacenter(
    client: EastMoneyClient,
    report: str,
    columns: str,
    *,
    filter_expr: str = "",
    page_size: int = 5000,
    sort_columns: str | None = None,
    sort_types: str | None = None,
    max_retries: int = 3,
    retry_backoff_seconds: float = 5.0,
    stop_after: Callable[[list[dict]], bool] | None = None,
) -> list[dict]:
    """Page a datacenter report to exhaustion, or until *stop_after* says stop.

    ``stop_after`` receives each page's rows and returns True when no later page
    can contain anything the caller wants. It only makes sense on a sorted
    report, and it deliberately suppresses the completeness guards below — a
    short read is the point, so the declared ``count`` will not match.
    """
    page_size = min(page_size, _MAX_PAGE_SIZE)
    stopped_early = False
    rows: list[dict] = []
    # First page's result.pages/count; a transient "返回数据为空" on a later
    # page looks exactly like end-of-data and silently truncates at a 500×k
    # boundary (margin_trading 2026-07-02/03), so verify against these.
    expected_pages: int | None = None
    expected_count: int | None = None
    page = 1
    while True:
        params = (
            f"reportName={report}"
            f"&columns={quote(columns, safe=',')}"
            f"&pageSize={page_size}"
            f"&pageNumber={page}"
        )
        if filter_expr:
            params += f"&filter={quote(filter_expr, safe='()>=<=')}"
        if sort_columns:
            params += f"&sortColumns={sort_columns}&sortTypes={sort_types or '-1'}"
        url = f"{DATACENTER_BASE}?{params}"

        last_exc: Exception | None = None
        payload = None
        for attempt in range(max_retries):
            try:
                resp = client.get(url)
                resp.raise_for_status()
                payload = resp.json()
                if payload.get("success") is False:
                    message = str(payload.get("message") or "")
                    if _is_busy(message):
                        raise _ServerBusy(f"{message} on page {page}")
                    if (
                        message in _EMPTY_RESULT_MESSAGES
                        and expected_pages is not None
                        and page <= expected_pages
                    ):
                        raise _TransientEmptyPage(f"empty response on page {page}/{expected_pages}")
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                from ashare_lake.adapters.eastmoney.em_auth import (
                    is_transport_fail_fast,
                )

                if is_transport_fail_fast(exc):
                    break
                if attempt + 1 < max_retries:
                    time.sleep(retry_backoff_seconds * (attempt + 1))
        if last_exc is not None:
            if isinstance(last_exc, _ServerBusy):
                raise EastMoneyDatacenterError(
                    f"EastMoney datacenter {report} still busy after {max_retries} attempts "
                    f"({last_exc}); this is throttling, not a schema break — retry later or "
                    "raise [sources.eastmoney].min_interval_seconds"
                ) from last_exc
            if isinstance(last_exc, _TransientEmptyPage):
                raise EastMoneyDatacenterError(
                    f"EastMoney datacenter {report} truncated: {last_exc} persisted after "
                    f"{max_retries} attempts; got {len(rows)} of {expected_count} rows"
                ) from last_exc
            raise EastMoneyDatacenterError(
                f"EastMoney datacenter {report} page {page} failed after {max_retries} attempts"
            ) from last_exc

        if payload.get("success") is False:
            msg = str(payload.get("message") or "")
            if msg not in _EMPTY_RESULT_MESSAGES:
                code = payload.get("code")
                # Column renames land here as code=9501 ("X列不存在"). Name the
                # report so ops can jump to datacenter_contracts / the adapter.
                raise EastMoneyDatacenterError(
                    f"EastMoney datacenter {report} rejected schema: {msg}"
                    + (f" (code={code})" if code is not None else "")
                )
            break

        result = payload.get("result") or {}
        if expected_pages is None:
            raw_pages = result.get("pages")
            if isinstance(raw_pages, int) and raw_pages > 0:
                expected_pages = raw_pages
            raw_count = result.get("count")
            if isinstance(raw_count, int):
                expected_count = raw_count
        batch = result.get("data") or []
        rows.extend(batch)
        if stop_after is not None and stop_after(batch):
            stopped_early = True
            break
        if expected_pages is not None:
            if page >= expected_pages:
                break
            if len(batch) < page_size:
                raise EastMoneyDatacenterError(
                    f"EastMoney datacenter {report} page {page}/{expected_pages} returned "
                    f"{len(batch)} rows (expected {page_size}); refusing truncated result"
                )
        elif len(batch) < page_size:
            break
        page += 1
    if not stopped_early and expected_count is not None and len(rows) < expected_count:
        raise EastMoneyDatacenterError(
            f"EastMoney datacenter {report} returned {len(rows)} rows but page 1 "
            f"declared count={expected_count}; refusing truncated result"
        )
    return rows
