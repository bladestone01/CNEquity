"""Baostock historical valuation (PE/PB/PS) — backfill source for valuation_metrics.

EastMoney's valuation endpoint is a live snapshot (the clist page stamped with
today's ``trade_date``); it cannot replay history, so the lake only ever held a
single day of PE/PB/PS. Baostock exposes per-symbol *daily* ``peTTM`` /
``pbMRQ`` / ``psTTM`` back to 2016, which unlocks historical valuation
percentiles for value strategies.

Market cap (``total_mv`` / ``float_mv``) is not part of baostock's k-data, so
those columns stay null on the backfill path; the daily EastMoney snapshot keeps
filling them going forward. Provenance ``source="baostock"`` marks the historical
rows so ``audit`` can cross-check the overlap day against the EastMoney snapshot.

Reliability: baostock throttles/drops a long-held session under a full-market
sweep (broken pipe → ``error_code != '0'``). ``fetch_valuation_history`` retries
each symbol with a fresh login + backoff, and — critically — returns the list of
symbols that still failed so the caller can fail loud and resume, rather than
silently reporting a mostly-empty backfill as success.
"""

from __future__ import annotations

import logging
import time
from datetime import date

import polars as pl

from stock_data_engine.adapters.baostock._session import (
    fetch_per_symbol,
    to_baostock_symbol,
)

logger = logging.getLogger(__name__)

# Re-exported for backwards compatibility (``adapters.baostock.valuation`` was
# the original home of this helper before the shared session driver).
__all__ = ["fetch_valuation_history", "to_baostock_symbol"]

# baostock k-data field order requested per row.
_FIELDS = "date,code,peTTM,pbMRQ,psTTM"

_OUTPUT_SCHEMA = {
    "symbol": pl.Utf8,
    "trade_date": pl.Date,
    "pe_ttm": pl.Float64,
    "pb": pl.Float64,
    "ps_ttm": pl.Float64,
    "total_mv": pl.Float64,
    "float_mv": pl.Float64,
}

def _to_float(raw: str | None) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _fetch_one(bs, symbol: str, start: date, end: date) -> list[dict] | None:
    """Rows for one symbol, or ``None`` if the query errored (retryable).

    An ``error_code == '0'`` result with zero rows is a legitimate empty
    (delisted before the window, no baostock coverage) — returns ``[]``, not
    ``None``, so the caller does not treat it as a failure to retry.
    """
    rs = bs.query_history_k_data_plus(
        to_baostock_symbol(symbol),
        _FIELDS,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        frequency="d",
        adjustflag="3",  # unadjusted; PE/PB/PS ratios are adjust-independent
    )
    if getattr(rs, "error_code", "0") != "0":
        return None
    out: list[dict] = []
    while rs.next():
        trade_raw, _code, pe, pb, ps = rs.get_row_data()
        out.append(
            {
                "symbol": symbol,
                "trade_date": date.fromisoformat(trade_raw),
                "pe_ttm": _to_float(pe),
                "pb": _to_float(pb),
                "ps_ttm": _to_float(ps),
                "total_mv": None,
                "float_mv": None,
            }
        )
    return out


def fetch_valuation_history(
    symbols: list[str],
    start: date,
    end: date,
    *,
    bs=None,
    sleep=time.sleep,
) -> tuple[pl.DataFrame, list[str]]:
    """Per-symbol daily PE/PB/PS from baostock over ``[start, end]`` inclusive.

    Returns ``(dataframe, failed_symbols)``. Fail-loud on login failure. Each
    symbol is retried up to ``_MAX_RETRIES`` times with a fresh session + backoff
    on a query error (baostock throttling / broken pipe); symbols still failing
    are returned in ``failed_symbols`` so the caller can surface them and resume
    instead of silently shipping a partial backfill. ``total_mv`` / ``float_mv``
    are null — not part of baostock k-data.

    ``bs`` / ``sleep`` are injectable for offline tests.
    """
    rows, failed = fetch_per_symbol(
        symbols, start, end, _fetch_one, bs=bs, sleep=sleep, label="baostock valuation"
    )
    df = pl.DataFrame(rows, schema=_OUTPUT_SCHEMA) if rows else pl.DataFrame(schema=_OUTPUT_SCHEMA)
    return df, failed
