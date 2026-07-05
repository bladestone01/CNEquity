"""Shared helpers for step implementations."""

from __future__ import annotations

import polars as pl

from stock_data_engine.adapters.tdx_protocol.client import fetch_instruments
from stock_data_engine.config import Config
from stock_data_engine.storage import StagingWriter


def write_simple(config: Config, run_id: str, dataset: str, df: pl.DataFrame) -> dict:
    writer = StagingWriter(config.staging_root)
    writer.write_batch(dataset, run_id, "batch-0", df)
    return {"rows_read": df.height, "rows_written": df.height}


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
