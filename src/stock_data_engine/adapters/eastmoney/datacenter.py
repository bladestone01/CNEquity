"""EastMoney datacenter pagination helper."""

from __future__ import annotations

import logging
import time
from urllib.parse import quote

from stock_data_engine.adapters.eastmoney.common import DATACENTER_BASE
from stock_data_engine.adapters.eastmoney.em_auth import EastMoneyClient

logger = logging.getLogger(__name__)

_EMPTY_RESULT_MESSAGES = frozenset({"返回数据为空"})

# EastMoney datacenter caps pageSize at 500; requesting more returns only 500
# rows and — because the short page looks like the last one — silently
# truncates every high-volume report. Clamp so pagination stays correct.
_MAX_PAGE_SIZE = 500


class EastMoneyDatacenterError(RuntimeError):
    """Raised when datacenter pagination fails after partial or zero results."""


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
) -> list[dict]:
    page_size = min(page_size, _MAX_PAGE_SIZE)
    rows: list[dict] = []
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
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                if attempt + 1 < max_retries:
                    time.sleep(retry_backoff_seconds * (attempt + 1))
        if last_exc is not None:
            raise EastMoneyDatacenterError(
                f"EastMoney datacenter {report} page {page} failed after {max_retries} attempts"
            ) from last_exc

        if payload.get("success") is False:
            msg = str(payload.get("message") or "")
            if msg not in _EMPTY_RESULT_MESSAGES:
                code = payload.get("code")
                raise EastMoneyDatacenterError(
                    f"EastMoney datacenter {report} page {page} rejected: {msg}"
                    + (f" (code={code})" if code is not None else "")
                )
            break

        batch = (payload.get("result") or {}).get("data") or []
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < page_size:
            break
        page += 1
    return rows
