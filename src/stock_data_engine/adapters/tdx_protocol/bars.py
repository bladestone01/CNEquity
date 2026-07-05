"""TDX daily bars with pagination beyond the 800-bar API limit."""

from __future__ import annotations

import logging
from datetime import date

import polars as pl

from stock_data_engine.domain.rate_limit import RateLimitSpec, wait_spec

logger = logging.getLogger(__name__)

_PAGE_SIZE = 800


def _parse_bar_rows(pdf: pl.DataFrame, sym: str, start: date, end: date) -> list[dict]:
    date_col = "datetime" if "datetime" in pdf.columns else "date"
    rows: list[dict] = []
    for row in pdf.iter_rows(named=True):
        td = row[date_col]
        if hasattr(td, "date"):
            td = td.date()
        if td < start or td > end:
            continue
        rows.append(
            {
                "symbol": sym,
                "trade_date": td,
                "open": float(row.get("open", 0)),
                "high": float(row.get("high", 0)),
                "low": float(row.get("low", 0)),
                "close": float(row.get("close", 0)),
                "volume": int(row.get("volume", row.get("vol", 0))),
                "amount": float(row.get("amount", 0)),
            }
        )
    return rows


def fetch_bars_paginated(
    client,
    sym: str,
    start: date,
    end: date,
    *,
    rate_limit: RateLimitSpec | None = None,
) -> list[dict]:
    """Fetch daily bars for *sym* in [start, end], paging through TDX history."""
    code, exch = sym.split(".")
    market = 1 if exch == "SH" else (0 if exch == "SZ" else 2)
    offset_pos = 0
    all_rows: list[dict] = []

    while True:
        wait_spec(rate_limit)
        try:
            raw = client.bars(
                symbol=code,
                frequency=9,
                market=market,
                start=offset_pos,
                offset=_PAGE_SIZE,
            )
        except Exception as exc:
            logger.debug("TDX bars page failed for %s at start=%s: %s", sym, offset_pos, exc)
            break

        if raw is None or len(raw) == 0:
            break

        if isinstance(raw, pl.DataFrame):
            pdf = raw
        elif hasattr(raw, "columns"):
            pdf = pl.from_pandas(raw)
        else:
            pdf = pl.DataFrame(raw)

        page_rows = _parse_bar_rows(pdf, sym, start, end)
        if page_rows:
            all_rows.extend(page_rows)

        if len(pdf) < _PAGE_SIZE:
            break
        offset_pos += _PAGE_SIZE

    if not all_rows:
        return []

    df = pl.DataFrame(all_rows).unique(subset=["symbol", "trade_date"], keep="last")
    return df.sort("trade_date").to_dicts()
