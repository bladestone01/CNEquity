"""Reconstruct historical suspension status from daily_bars gaps.

Exchanges publish no daily bar for a suspended stock, so a listed symbol with
no bar on a trading day was suspended that day. This is authoritative and
covers the whole bar history — filling the trading_status gap that free ST
feeds (EastMoney / AKShare current-snapshot) cannot reach.

Only sparse ``suspended`` rows are written; real daily rows (EM/AKShare) win on
any primary-key overlap.
"""

from __future__ import annotations

import logging
from datetime import date

import polars as pl

from ashare_lake.config import Config
from ashare_lake.domain.schemas import validate_dataframe, with_provenance
from ashare_lake.query.parquet_scan import dataset_has_parquet, scan_parquet_root
from ashare_lake.storage.parquet import CuratedWriter

logger = logging.getLogger(__name__)

_DERIVED_SOURCE = "derived_bar_gap"


def _suspended_pairs(config: Config) -> pl.DataFrame:
    """(symbol, trade_date) that were trading days in a symbol's active range but have no bar."""
    bars_root = config.curated_root / "daily_bars"
    cal_root = config.curated_root / "trading_calendar"
    inst_path = config.curated_root / "instruments" / "part-merged.parquet"
    if not (
        dataset_has_parquet(bars_root) and dataset_has_parquet(cal_root) and inst_path.exists()
    ):
        return pl.DataFrame(schema={"symbol": pl.Utf8, "trade_date": pl.Date})

    bars = (
        scan_parquet_root(bars_root, partition_col="trade_date")
        .select(["symbol", "trade_date"])
        .unique()
        .collect()
    )
    if bars.is_empty():
        return pl.DataFrame(schema={"symbol": pl.Utf8, "trade_date": pl.Date})

    cal = (
        scan_parquet_root(cal_root, partition_col="trade_date")
        .filter(pl.col("is_trading"))
        .select("trade_date")
        .collect()
    )
    inst = pl.read_parquet(inst_path).select(["symbol", "list_date", "delist_date"])

    sym_range = bars.group_by("symbol").agg(
        pl.col("trade_date").min().alias("bmin"),
        pl.col("trade_date").max().alias("bmax"),
    )
    active = inst.join(sym_range, on="symbol", how="inner").with_columns(
        pl.max_horizontal(pl.col("list_date").fill_null(pl.col("bmin")), pl.col("bmin")).alias(
            "astart"
        ),
        pl.min_horizontal(pl.col("delist_date").fill_null(pl.col("bmax")), pl.col("bmax")).alias(
            "aend"
        ),
    )

    expected = (
        active.select(["symbol", "astart", "aend"])
        .join(cal, how="cross")
        .filter(
            (pl.col("trade_date") >= pl.col("astart")) & (pl.col("trade_date") <= pl.col("aend"))
        )
        .select(["symbol", "trade_date"])
    )
    return expected.join(bars, on=["symbol", "trade_date"], how="anti")


def derive_suspension_history(config: Config) -> int:
    """Write derived ``suspended`` rows into curated trading_status. Returns row count."""
    pairs = _suspended_pairs(config)
    if pairs.is_empty():
        return 0

    rows = pairs.with_columns(
        pl.lit(False).alias("is_trading"),
        pl.lit("suspended").alias("status"),
    )
    rows = with_provenance(rows, source=_DERIVED_SOURCE, data_version="v1")
    rows = validate_dataframe(rows, "trading_status")

    writer = CuratedWriter(config.curated_root)
    total = 0
    for key, group in rows.partition_by("trade_date", as_dict=True).items():
        td = key[0] if isinstance(key, tuple) else key
        val = td.isoformat() if isinstance(td, date) else str(td)
        existing_dir = writer.partition_path("trading_status", "trade_date", val)
        frames = [group]
        stray_parts: list = []
        if existing_dir.exists():
            for f in existing_dir.glob("*.parquet"):
                frames.append(validate_dataframe(pl.read_parquet(f), "trading_status"))
                if f.name != "part-merged.parquet":
                    stray_parts.append(f)
        merged = pl.concat(frames, how="diagonal_relaxed")
        # Real daily rows (non-derived) win on PK overlap: sort them first.
        merged = merged.with_columns(
            (pl.col("source") == _DERIVED_SOURCE).alias("_is_derived")
        ).sort("_is_derived")
        merged = merged.unique(subset=["symbol", "trade_date"], keep="first").drop("_is_derived")
        # Reuse compact's filename so we overwrite the single canonical part
        # rather than adding a second file (which would double-count on read).
        writer.write_partition("trading_status", "trade_date", val, merged, "part-merged.parquet")
        for stray in stray_parts:
            stray.unlink(missing_ok=True)
        total += group.height
    logger.info("derived %d historical suspension rows into trading_status", total)
    return total
