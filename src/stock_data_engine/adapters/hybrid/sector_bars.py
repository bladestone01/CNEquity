"""Hybrid sector_bars daily snapshot: EM clist universe + TDX OHLC where routed."""

from __future__ import annotations

import logging
from datetime import date

import polars as pl

from stock_data_engine.adapters.eastmoney.rotation import fetch_sector_bars
from stock_data_engine.adapters.tdx_protocol.sector_bars import fetch_sector_index_snapshot
from stock_data_engine.config import Config
from stock_data_engine.derive.sector_routing import OHLC_EM, OHLC_TDX, load_sector_routing

logger = logging.getLogger(__name__)


def fetch_hybrid_sector_bars(trade_date: date, *, config: Config) -> pl.DataFrame:
    """EastMoney board list with TDX index OHLC substituted for routed boards."""
    em_df = fetch_sector_bars(trade_date)
    if em_df.is_empty():
        return em_df

    routing = load_sector_routing(config)
    if routing.is_empty() or not config.sources.get("tdx", True):
        return em_df.with_columns(pl.lit(OHLC_EM).alias("source"))

    tdx_routed = routing.filter(pl.col("ohlc_source") == OHLC_TDX)
    replacements: list[dict] = []
    replaced_codes: list[str] = []
    for row in tdx_routed.iter_rows(named=True):
        tdx_code = row.get("tdx_code")
        if not tdx_code:
            continue
        try:
            snap = fetch_sector_index_snapshot(
                sector_code=row["sector_code"],
                sector_name=row["sector_name"],
                board_type=row["board_type"],
                tdx_code=str(tdx_code),
                trade_date=trade_date,
                config=config,
            )
        except Exception as exc:
            logger.warning(
                "hybrid sector_bars: TDX snapshot %s failed, keeping EM OHLC: %s",
                row["sector_code"],
                exc,
            )
            continue
        if not snap:
            continue
        replacements.append({**snap, "source": OHLC_TDX})
        replaced_codes.append(row["sector_code"])

    em_df = em_df.with_columns(pl.lit(OHLC_EM).alias("source"))
    if not replacements:
        return em_df

    rep_df = pl.DataFrame(replacements)
    em_df = em_df.filter(~pl.col("sector_code").is_in(replaced_codes))
    return pl.concat([em_df, rep_df], how="diagonal_relaxed")
