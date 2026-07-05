"""CNINFO announcement index (batch)."""

from __future__ import annotations

import logging
from datetime import date

import httpx
import polars as pl

from stock_data_engine.domain.symbols import format_symbol, is_all_a_symbol

logger = logging.getLogger(__name__)

_CNINFO_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"


def _symbol_from_cninfo(code: str, org_id: str | None = None) -> str | None:
    code = str(code).zfill(6)
    if code.startswith(("60", "68")):
        exch = "SH"
    elif code.startswith("92"):
        exch = "BJ"
    else:
        exch = "SZ"
    if not is_all_a_symbol(code, exch):
        return None
    return format_symbol(code, exch)


def fetch_announcement_index(
    trade_date: date,
    *,
    client: httpx.Client | None = None,
) -> pl.DataFrame:
    owns = client is None
    if client is None:
        client = httpx.Client(timeout=30.0, headers={"User-Agent": "Mozilla/5.0"})

    ds = trade_date.strftime("%Y-%m-%d")
    rows: list[dict] = []
    for column in ("szse", "sse"):
        page = 1
        while True:
            payload = {
                "pageNum": page,
                "pageSize": 30,
                "column": column,
                "tabName": "fulltext",
                "plate": "",
                "stock": "",
                "searchkey": "",
                "secid": "",
                "category": "",
                "trade": "",
                "seDate": f"{ds}~{ds}",
            }
            try:
                resp = client.post(_CNINFO_URL, data=payload)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning("CNINFO announcement page failed (%s p%s): %s", column, page, exc)
                break

            batch = data.get("announcements") or []
            if not batch:
                break
            for item in batch:
                sym = _symbol_from_cninfo(str(item.get("secCode", "")))
                if not sym:
                    continue
                ann_id = str(item.get("announcementId") or item.get("adjunctUrl", ""))
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
            if not data.get("hasMore"):
                break
            page += 1

    if owns:
        client.close()
    if not rows:
        return pl.DataFrame()
    return pl.DataFrame(rows).unique(subset=["announcement_id"], keep="last")
