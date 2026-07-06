"""Shared helpers for step implementations."""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from stock_data_engine.adapters.tdx_protocol.client import fetch_instruments
from stock_data_engine.config import Config
from stock_data_engine.storage import StagingWriter
from stock_data_engine.storage.state import StateStore

INCREMENTAL_LOOKBACK_DAYS = 5
BACKFILL_START = date(2016, 1, 1)


def write_simple(config: Config, run_id: str, dataset: str, df: pl.DataFrame) -> dict:
    writer = StagingWriter(config.staging_root)
    writer.write_batch(dataset, run_id, "batch-0", df)
    return {"rows_read": df.height, "rows_written": df.height}


def incremental_window(config: Config, dataset: str, trade_date: date) -> date:
    """Start date for incremental fetch: day after watermark, or lookback window."""
    state = StateStore(config.meta_root)
    watermark = state.get_date(dataset)
    if watermark is not None:
        return min(watermark + timedelta(days=1), trade_date)
    return trade_date - timedelta(days=INCREMENTAL_LOOKBACK_DAYS)


def load_symbols(config: Config) -> list[str]:
    """Universe symbols: curated instruments first, then staging, then source."""
    curated = config.curated_root / "instruments" / "part-merged.parquet"
    staging_glob = list(config.staging_root.glob("instruments/run_id=*/part-*.parquet"))
    if curated.exists():
        return pl.read_parquet(curated)["symbol"].to_list()
    if staging_glob:
        latest = max(staging_glob, key=lambda p: p.stat().st_mtime)
        return pl.read_parquet(latest)["symbol"].to_list()
    df = fetch_instruments(
        rate_limit=config.tdx_rate_limit_spec(), allow_mock=config.tdx_allow_mock
    )
    return df["symbol"].to_list()
