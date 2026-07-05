"""Merge-style compact for instruments (preserve delisted symbols)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from stock_data_engine.domain.schemas import INSTRUMENTS_SCHEMA, validate_dataframe
from stock_data_engine.storage.atomic import write_parquet_atomic
from stock_data_engine.storage.parquet import StagingWriter


def compact_instruments(
    staging_root: Path,
    curated_root: Path,
    run_id: str,
    trade_date: date,
) -> int:
    """Merge staging instruments into curated, retaining symbols missing from TDX."""
    staging = StagingWriter(staging_root)
    files = staging.list_run_files("instruments", run_id)
    if not files:
        return 0

    incoming = pl.concat(
        [validate_dataframe(pl.read_parquet(f), "instruments") for f in files],
        how="diagonal_relaxed",
    )
    incoming = incoming.sort("fetched_at").unique(subset=["symbol"], keep="last")
    incoming = incoming.with_columns(pl.lit(None).cast(pl.Date).alias("delist_date"))

    out_path = curated_root / "instruments" / "part-merged.parquet"
    if out_path.exists():
        existing = validate_dataframe(pl.read_parquet(out_path), "instruments")
    else:
        existing = pl.DataFrame(schema=INSTRUMENTS_SCHEMA)

    incoming_symbols = incoming["symbol"].to_list()
    if not existing.is_empty():
        preserved = existing.filter(~pl.col("symbol").is_in(incoming_symbols))
        preserved = preserved.with_columns(
            pl.when(pl.col("delist_date").is_null())
            .then(pl.lit(trade_date))
            .otherwise(pl.col("delist_date"))
            .alias("delist_date")
        )
        prior_dates = existing.select(
            [
                "symbol",
                pl.col("list_date").alias("_prior_list_date"),
            ]
        )
        incoming = incoming.join(prior_dates, on="symbol", how="left")
        incoming = incoming.with_columns(
            pl.coalesce(pl.col("list_date"), pl.col("_prior_list_date")).alias("list_date")
        ).drop("_prior_list_date")
    else:
        preserved = pl.DataFrame(schema=INSTRUMENTS_SCHEMA)

    merged = pl.concat([incoming, preserved], how="diagonal_relaxed")
    merged = merged.sort("fetched_at").unique(subset=["symbol"], keep="last")

    write_parquet_atomic(out_path, merged, compression="zstd")
    return merged.height
