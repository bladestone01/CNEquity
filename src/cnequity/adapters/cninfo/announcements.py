"""CNINFO announcement index (batch)."""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime

import httpx
import polars as pl

from cnequity.domain.market_time import SHANGHAI_TZ
from cnequity.domain.symbols import format_symbol, infer_exchange_from_code, is_all_a_symbol

# Epoch-millisecond bounds used to recognize a Unix ms timestamp regardless of
# which of the three candidate keys carried it: 2000-01-01 / 2100-01-01 in
# epoch ms. Both bounds sit orders of magnitude away from an 8-digit YYYYMMDD
# value, so there is no ambiguity between the two shapes.
_EPOCH_MS_MIN = 946_684_800_000
_EPOCH_MS_MAX = 4_102_444_800_000

logger = logging.getLogger(__name__)

_CNINFO_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
_PAGE_SIZE = 30

# A single unretried request killing a multi-year backfill walk over a
# transient 504 is how a 30-minute cninfo hiccup turns into hours of redone
# work — see `walk_day_backfill`, which restarts a whole step on any raise
# from its per-day fetch. Retrying here, close to the actual HTTP call, keeps
# the caller's "fail loud on a page" contract for a genuinely broken source
# while surviving the blip. Measured hitting this in production: a 504 on
# `regulatory_events` page 8 of a ~16-year sweep.
# 504 Gateway Time-out is common on deep sse pages of busy disclosure days;
# three tries with a short backoff still lost a 16h announcement walk at
# page 270. Be more patient here — a few extra minutes beats redoing months.
_POST_RETRIES = 6
_POST_BACKOFF_SECONDS = 5.0

# CNINFO announcement-category buckets (``category_<name>_szsh`` codes). The
# ``hisAnnouncement/query`` endpoint serves at most 100 pages (pageSize=30 ->
# 3000 rows) for a single query — past that it re-serves page 1's rows while
# ``totalpages``/``hasMore`` keep reporting more (measured 2026-08-28 szse,
# totalAnnouncement=6538 -> totalpages=217, page 101+ identical to page 1).
# Unlike ``column`` (=szse/sse/bj, which the A-share fulltext search ignores
# and returns the same set for every value), ``category`` genuinely subdivides
# the day: the bucket union covers ~98.5% of rows and the largest bucket stays
# under the 100-page cap. Bucket codes match akshare's ``__get_category_dict``.
_CNINFO_CATEGORIES: tuple[str, ...] = (
    "category_ndbg_szsh",  # 年报
    "category_bndbg_szsh",  # 半年报
    "category_yjdbg_szsh",  # 一季报
    "category_sjdbg_szsh",  # 三季报
    "category_yjygjxz_szsh",  # 业绩预告
    "category_qyfpxzcs_szsh",  # 权益分派
    "category_dshgg_szsh",  # 董事会
    "category_jshgg_szsh",  # 监事会
    "category_gddh_szsh",  # 股东大会
    "category_rcjy_szsh",  # 日常经营
    "category_gszl_szsh",  # 公司治理
    "category_zj_szsh",  # 中介报告
    "category_sf_szsh",  # 首发
    "category_zf_szsh",  # 增发
    "category_gqjl_szsh",  # 股权激励
    "category_pg_szsh",  # 配股
    "category_jj_szsh",  # 解禁
    "category_gszq_szsh",  # 公司债
    "category_kzzq_szsh",  # 可转债
    "category_qtrz_szsh",  # 其他融资
    "category_gqbd_szsh",  # 股权变动
    "category_bcgz_szsh",  # 补充更正
    "category_cqdq_szsh",  # 澄清致歉
    "category_fxts_szsh",  # 风险提示
    "category_tbclts_szsh",  # 特别处理和退市
    "category_tszlq_szsh",  # 退市整理期
)


def post_with_retry(client: httpx.Client, url: str, *, data: dict) -> dict:
    last_exc: Exception | None = None
    for attempt in range(_POST_RETRIES):
        try:
            resp = client.post(url, data=data)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001 — retried uniformly, re-raised below
            last_exc = exc
            if attempt + 1 < _POST_RETRIES:
                time.sleep(_POST_BACKOFF_SECONDS * (attempt + 1))
    raise last_exc  # type: ignore[misc]


def _symbol_from_cninfo(code: str, org_id: str | None = None) -> str | None:
    code = str(code).strip().zfill(6)
    if len(code) != 6 or not code.isdigit():
        return None
    exch = infer_exchange_from_code(code)
    if not is_all_a_symbol(code, exch):
        return None
    return format_symbol(code, exch)


def _announcement_id(item: dict) -> str | None:
    """Stable CNINFO identity, or None for a row that cannot be keyed."""
    value = item.get("announcementId") or item.get("adjunctUrl")
    text = str(value or "").strip()
    return text or None


def _validate_source_date(item: dict, trade_date: date, *, column: str) -> None:
    """Reject a CNINFO row whose own date disagrees with the requested day.

    ``seDate`` is supposed to be an exact server-side filter, but the endpoint
    has returned rows outside that filter in the past. The old adapter stamped
    every row with ``trade_date`` without inspecting ``announcementTime``, so a
    cross-day response could be written into the wrong partition and still pass
    the generic by-date validation. Fixtures and older payloads may omit the
    field; absence is tolerated, but a present malformed or different date is a
    source-contract failure.
    """
    raw = next(
        (
            item[key]
            for key in ("announcementTime", "announcementDate", "announceDate")
            if key in item
        ),
        None,
    )
    if raw is None or str(raw).strip() == "":
        return
    numeric: int | None = None
    if isinstance(raw, int) and not isinstance(raw, bool):
        numeric = raw
    elif isinstance(raw, str) and raw.strip().isdigit():
        numeric = int(raw.strip())
    if numeric is not None and _EPOCH_MS_MIN <= numeric <= _EPOCH_MS_MAX:
        # The live endpoint sends announcementTime as a Unix millisecond
        # epoch, not a date string; only hand-written fixtures use ISO text.
        source_date = datetime.fromtimestamp(numeric / 1000, tz=SHANGHAI_TZ).date()
    else:
        text = str(raw).strip().replace("/", "-")
        try:
            source_date = date.fromisoformat(text[:10])
        except ValueError as exc:
            raise RuntimeError(
                f"CNINFO {column} row has invalid announcement date {raw!r}"
            ) from exc
    if source_date != trade_date:
        raise RuntimeError(
            f"CNINFO {column} row date {source_date.isoformat()} does not match "
            f"requested {trade_date.isoformat()}"
        )


def _announcement_batch(data: object, *, column: str, page: int) -> list[dict]:
    """Validate a CNINFO page while isolating malformed rows."""
    if not isinstance(data, dict):
        raise RuntimeError(f"CNINFO response for {column} page {page} is not an object")
    raw_batch = data.get("announcements")
    if raw_batch is None:
        return []
    if not isinstance(raw_batch, list):
        raise RuntimeError(f"CNINFO announcements for {column} page {page} is not a list")
    batch: list[dict] = []
    for index, item in enumerate(raw_batch):
        if not isinstance(item, dict):
            logger.warning(
                "CNINFO announcements: skipping non-object row %s on %s page %s",
                index,
                column,
                page,
            )
            continue
        batch.append(item)
    return batch


def _pagination_total_pages(data: dict, *, column: str, page: int) -> int | None:
    """Normalize the optional CNINFO page count without trusting bad metadata."""
    raw_total = data.get("totalpages")
    if raw_total is None:
        return None
    if isinstance(raw_total, bool):
        raise RuntimeError(
            f"CNINFO totalpages for {column} page {page} is not a non-negative integer"
        )
    if isinstance(raw_total, int):
        total_pages = raw_total
    elif isinstance(raw_total, str) and raw_total.strip().isdigit():
        total_pages = int(raw_total.strip())
    else:
        raise RuntimeError(
            f"CNINFO totalpages for {column} page {page} is not a non-negative integer"
        )
    if total_pages < 0:
        raise RuntimeError(
            f"CNINFO totalpages for {column} page {page} is not a non-negative integer"
        )
    return total_pages


def _pagination_has_more(data: dict, *, column: str, page: int) -> bool:
    """Normalize CNINFO's optional continuation flag."""
    raw_has_more = data.get("hasMore")
    if raw_has_more is None:
        return False
    if isinstance(raw_has_more, bool):
        return raw_has_more
    if isinstance(raw_has_more, str):
        normalized = raw_has_more.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no", ""}:
            return False
    raise RuntimeError(f"CNINFO hasMore for {column} page {page} is not a boolean value")


def _pagination_page_signature(batch: list[dict]) -> str:
    """Stable identity for a page, used to detect a non-advancing source."""
    return json.dumps(batch, sort_keys=True, default=str, separators=(",", ":"))


def _cninfo_payload(ds: str, *, page: int, category: str) -> dict:
    """HissAnnouncement/query request body for one category bucket."""
    return {
        "pageNum": page,
        "pageSize": 30,
        "column": "szse",
        "tabName": "fulltext",
        "plate": "",
        "stock": "",
        "searchkey": "",
        "secid": "",
        "category": category,
        "trade": "",
        "seDate": f"{ds}~{ds}",
    }


def _truncation_finding(*, dataset: str, bucket: str, page: int) -> dict:
    return {
        "dataset": dataset,
        "severity": "warning",
        "check": "cninfo_truncation_at_100_pages",
        "message": (
            f"CNINFO {dataset} {bucket} hit the server's 100-page cap at page "
            f"{page}; rows past page {page - 1} are not fetchable for this day"
        ),
        "bucket": bucket,
        "page": page,
    }


def _iter_bucket_pages(
    client: httpx.Client,
    ds: str,
    *,
    bucket: str,
    label: str,
    dataset: str,
    findings: list[dict] | None = None,
    config=None,
) -> iter[tuple[int, list[dict]]]:
    """Yield ``(page, batch)`` while walking one CNINFO category bucket.

    Encapsulates the pagination decisions both CNINFO fetchers share. ``label``
    is the noun used in failure messages ("announcement" / "regulatory");
    ``dataset`` names the finding's owning dataset.

    The server caps a single query at 100 effective pages; past that it
    re-serves an earlier page, so a repeated page signature means truncation,
    not a broken source: the bucket is stopped and a
    ``cninfo_truncation_at_100_pages`` finding is recorded instead of raising.
    Transport / malformed-metadata / empty-before-end failures still raise.
    """
    page = 1
    seen_page_signatures: set[str] = set()
    while True:
        if config is not None:
            config.rate_limit("cninfo")
        payload = _cninfo_payload(ds, page=page, category=bucket)
        try:
            data = post_with_retry(client, _CNINFO_URL, data=payload)
            batch = _announcement_batch(data, column=bucket, page=page)
            total_pages = _pagination_total_pages(data, column=bucket, page=page)
            has_more = _pagination_has_more(data, column=bucket, page=page)
            has_more_present = data.get("hasMore") is not None
            if batch:
                page_signature = _pagination_page_signature(batch)
                if page_signature in seen_page_signatures:
                    if findings is not None:
                        findings.append(
                            _truncation_finding(dataset=dataset, bucket=bucket, page=page)
                        )
                    return
                seen_page_signatures.add(page_signature)
        except Exception as exc:  # noqa: BLE001 - retried in post_with_retry, re-raised below
            logger.warning("CNINFO %s page failed (%s p%s): %s", label, bucket, page, exc)
            raise RuntimeError(
                f"CNINFO {label} pagination failed for {bucket} page {page}: {exc}"
            ) from exc

        if not batch:
            if (isinstance(total_pages, int) and total_pages > 0 and page <= total_pages) or (
                total_pages is None and has_more
            ):
                raise RuntimeError(
                    f"CNINFO {label} returned an empty page before the "
                    f"reported end for {bucket} page {page}"
                )
            return
        yield page, batch
        if isinstance(total_pages, int) and page >= total_pages:
            # `hasMore` cannot be trusted past the server's own reported
            # total: measured live, requesting page 2000 of a 105-page
            # day still returns page 1's rows with hasMore still true —
            # an infinite loop with no other exit. total_pages stays
            # correct even on those overshot pages, so it is the one
            # authoritative stop condition.
            return
        if isinstance(total_pages, int):
            # When the server supplies a page count, it is also the safer
            # continuation signal: a stale false `hasMore` would otherwise
            # silently drop the remaining pages.
            page += 1
            continue
        if not has_more:
            # Some CNINFO responses omit both continuation fields. A full
            # page is then evidence that another request may be needed;
            # stopping on it silently truncates a busy disclosure day.
            if total_pages is None and not has_more_present and len(batch) >= _PAGE_SIZE:
                page += 1
                continue
            return
        page += 1


def fetch_announcement_index(
    trade_date: date,
    *,
    client: httpx.Client | None = None,
    config=None,
    findings: list[dict] | None = None,
) -> pl.DataFrame:
    owns = client is None
    if client is None:
        client = httpx.Client(timeout=30.0, headers={"User-Agent": "Mozilla/5.0"})

    ds = trade_date.strftime("%Y-%m-%d")
    rows: list[dict] = []
    try:
        for bucket in _CNINFO_CATEGORIES:
            for _, batch in _iter_bucket_pages(
                client,
                ds,
                bucket=bucket,
                label="announcement",
                dataset="announcement_index",
                findings=findings,
                config=config,
            ):
                for item in batch:
                    _validate_source_date(item, trade_date, column=bucket)
                    sym = _symbol_from_cninfo(str(item.get("secCode", "")))
                    if not sym:
                        continue
                    ann_id = _announcement_id(item)
                    if ann_id is None:
                        logger.warning(
                            "CNINFO announcement missing announcementId and adjunctUrl; skipping"
                        )
                        continue
                    rows.append(
                        {
                            "announcement_id": ann_id,
                            "symbol": sym,
                            "title": str(item.get("announcementTitle") or ""),
                            "announce_date": trade_date,
                            "category": str(item.get("announcementType") or ""),
                            "url": str(item.get("adjunctUrl") or ""),
                        }
                    )
    finally:
        if owns:
            client.close()
    if not rows:
        return pl.DataFrame()
    return pl.DataFrame(rows).unique(subset=["announcement_id"], keep="last")
