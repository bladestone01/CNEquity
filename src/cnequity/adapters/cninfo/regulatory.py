"""CNINFO regulatory / compliance events (filtered from announcements)."""

from __future__ import annotations

import logging
import re
from datetime import date

import httpx
import polars as pl

from cnequity.adapters.cninfo.announcements import (
    _CNINFO_CATEGORIES,
    _announcement_id,
    _iter_bucket_pages,
    _symbol_from_cninfo,
    _validate_source_date,
)

logger = logging.getLogger(__name__)

_KEYWORD_TYPES: list[tuple[str, str]] = [
    ("行政处罚", "penalty"),
    ("处罚决定", "penalty"),
    ("立案", "investigation"),
    ("调查", "investigation"),
    ("监管函", "regulatory_letter"),
    ("警示函", "warning_letter"),
    ("处分", "disciplinary"),
]


def _classify_event(title: str) -> str:
    for keyword, event_type in _KEYWORD_TYPES:
        if keyword in title:
            return event_type
    return "regulatory"


def fetch_regulatory_events(
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
    pattern = re.compile("|".join(re.escape(k) for k, _ in _KEYWORD_TYPES))
    rows: list[dict] = []
    try:
        for bucket in _CNINFO_CATEGORIES:
            for _, batch in _iter_bucket_pages(
                client,
                ds,
                bucket=bucket,
                label="regulatory",
                dataset="regulatory_events",
                findings=findings,
                config=config,
            ):
                for item in batch:
                    _validate_source_date(item, trade_date, column=bucket)
                    title = str(item.get("announcementTitle") or "")
                    if not pattern.search(title):
                        continue
                    sym = _symbol_from_cninfo(str(item.get("secCode", "")))
                    if not sym:
                        continue
                    ann_id = _announcement_id(item)
                    if ann_id is None:
                        logger.warning("CNINFO regulatory announcement missing identity; skipping")
                        continue
                    rows.append(
                        {
                            "event_id": f"reg-{ann_id}",
                            "symbol": sym,
                            "event_date": trade_date,
                            "event_type": _classify_event(title),
                            "title": title,
                        }
                    )
    finally:
        if owns:
            client.close()
    if not rows:
        return pl.DataFrame()
    return pl.DataFrame(rows).unique(subset=["event_id"], keep="last")
