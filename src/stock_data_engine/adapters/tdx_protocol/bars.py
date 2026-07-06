"""TDX daily bars with pagination beyond the 800-bar API limit."""

from __future__ import annotations

import logging
from datetime import date

import polars as pl

from stock_data_engine.domain.rate_limit import RateLimitSpec, wait_spec

logger = logging.getLogger(__name__)

_PAGE_SIZE = 800


class TdxBarsPaginationError(RuntimeError):
    """Raised when a TDX bars page fails and the caller requires complete history."""


def _date_column(pdf: pl.DataFrame) -> str:
    return "datetime" if "datetime" in pdf.columns else "date"


def _coerce_date(val) -> date:
    if isinstance(val, date):
        return val
    if hasattr(val, "date"):
        return val.date()
    if isinstance(val, str):
        return date.fromisoformat(val[:10])
    raise TypeError(f"unsupported bar date value: {val!r}")


def _page_min_date(pdf: pl.DataFrame) -> date | None:
    col = _date_column(pdf)
    if col not in pdf.columns or pdf.is_empty():
        return None
    series = pdf[col]
    if series.dtype == pl.Date:
        return series.min()
    mins: list[date] = []
    for val in series:
        if val is None:
            continue
        mins.append(_coerce_date(val))
    return min(mins) if mins else None


def _parse_bar_rows(pdf: pl.DataFrame, sym: str, start: date, end: date) -> list[dict]:
    date_col = _date_column(pdf)
    rows: list[dict] = []
    for row in pdf.iter_rows(named=True):
        td = _coerce_date(row[date_col])
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
    backfill: bool = False,
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
            if offset_pos == 0 or backfill:
                raise TdxBarsPaginationError(
                    f"TDX bars page failed for {sym} at start={offset_pos}"
                ) from exc
            logger.warning("TDX bars page failed for %s at start=%s: %s", sym, offset_pos, exc)
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

        page_min = _page_min_date(pdf)
        if page_min is not None and page_min < start:
            break

        if len(pdf) < _PAGE_SIZE:
            break
        offset_pos += _PAGE_SIZE

    if not all_rows:
        return []

    df = pl.DataFrame(all_rows).unique(subset=["symbol", "trade_date"], keep="last")
    return df.sort("trade_date").to_dicts()
