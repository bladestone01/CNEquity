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
"""

from __future__ import annotations

import logging
from datetime import date

import polars as pl

from stock_data_engine.domain.symbols import parse_symbol

logger = logging.getLogger(__name__)

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


def to_baostock_symbol(symbol: str) -> str:
    """``600519.SH`` -> ``sh.600519`` (baostock's market-prefixed form)."""
    info = parse_symbol(symbol)
    prefix = {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(info.exchange, info.exchange.lower())
    return f"{prefix}.{info.code}"


def _to_float(raw: str | None) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _import_baostock():
    try:
        import baostock as bs  # noqa: PLC0415 — optional dependency, imported lazily
    except ImportError as exc:  # pragma: no cover - optional dep
        raise RuntimeError(
            "baostock is not installed; historical valuation backfill requires it. "
            "Install with `pip install -e '.[valuation]'`."
        ) from exc
    return bs


def fetch_valuation_history(
    symbols: list[str],
    start: date,
    end: date,
    *,
    bs=None,
) -> pl.DataFrame:
    """Per-symbol daily PE/PB/PS from baostock over ``[start, end]`` inclusive.

    Fail-loud on login failure (a bad session would otherwise forge empty rows).
    Symbols baostock does not cover (indices, early delistings) are skipped with a
    warning. ``total_mv`` / ``float_mv`` are null — not part of baostock k-data.

    ``bs`` is injectable for offline tests; production imports the real module.
    """
    if bs is None:
        bs = _import_baostock()

    login = bs.login()
    if getattr(login, "error_code", "0") != "0":
        raise RuntimeError(f"baostock login failed: {getattr(login, 'error_msg', 'unknown')}")

    rows: list[dict] = []
    try:
        for symbol in symbols:
            rs = bs.query_history_k_data_plus(
                to_baostock_symbol(symbol),
                _FIELDS,
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                frequency="d",
                adjustflag="3",  # unadjusted; PE/PB/PS ratios are adjust-independent
            )
            if getattr(rs, "error_code", "0") != "0":
                logger.warning(
                    "baostock valuation query failed for %s: %s",
                    symbol,
                    getattr(rs, "error_msg", "unknown"),
                )
                continue
            while rs.next():
                trade_raw, _code, pe, pb, ps = rs.get_row_data()
                rows.append(
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
    finally:
        bs.logout()

    if not rows:
        return pl.DataFrame(schema=_OUTPUT_SCHEMA)
    return pl.DataFrame(rows, schema=_OUTPUT_SCHEMA)
