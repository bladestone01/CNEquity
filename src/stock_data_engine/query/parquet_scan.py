"""Lazy parquet scans with hive partition pruning for curated/derived lakes."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from stock_data_engine.domain.datasets import PARTITION_COLS


def dataset_has_parquet(root: Path) -> bool:
    return root.exists() and any(root.rglob("*.parquet"))


def list_hive_partition_dates(root: Path, partition_col: str) -> list[date]:
    prefix = f"{partition_col}="
    dates: list[date] = []
    if not root.exists():
        return dates
    for entry in root.iterdir():
        if not entry.is_dir() or not entry.name.startswith(prefix):
            continue
        try:
            dates.append(date.fromisoformat(entry.name[len(prefix) :]))
        except ValueError:
            continue
    return sorted(dates)


def coverage_start_from_partitions(root: Path, partition_col: str) -> date | None:
    dates = list_hive_partition_dates(root, partition_col)
    return dates[0] if dates else None


def uses_hive_partitions(root: Path, partition_col: str | None) -> bool:
    if partition_col is None:
        return False
    prefix = f"{partition_col}="
    if not root.exists():
        return False
    return any(
        entry.is_dir() and entry.name.startswith(prefix) for entry in root.iterdir()
    )


def scan_parquet_root(
    root: Path,
    *,
    partition_col: str | None = None,
    start: date | None = None,
    end: date | None = None,
    symbols: list[str] | None = None,
    hive: bool | None = None,
) -> pl.LazyFrame:
    if not dataset_has_parquet(root):
        msg = f"no parquet data under {root}"
        raise FileNotFoundError(msg)

    use_hive = uses_hive_partitions(root, partition_col) if hive is None else hive
    lf = pl.scan_parquet(str(root / "**" / "*.parquet"), hive_partitioning=use_hive)

    if partition_col and (start is not None or end is not None):
        if start is not None:
            lf = lf.filter(pl.col(partition_col) >= start)
        if end is not None:
            lf = lf.filter(pl.col(partition_col) <= end)
    if symbols and "symbol" in lf.collect_schema().names():
        lf = lf.filter(pl.col("symbol").is_in(symbols))
    return lf


def scan_parquet_files(
    files: list[Path],
    *,
    hive: bool = False,
) -> pl.LazyFrame:
    if not files:
        return pl.LazyFrame()
    return pl.scan_parquet([str(path) for path in files], hive_partitioning=hive)


def collect_parquet_root(
    root: Path,
    *,
    partition_col: str | None = None,
    start: date | None = None,
    end: date | None = None,
    symbols: list[str] | None = None,
    hive: bool | None = None,
) -> pl.DataFrame:
    return scan_parquet_root(
        root,
        partition_col=partition_col,
        start=start,
        end=end,
        symbols=symbols,
        hive=hive,
    ).collect()


def lazy_row_count(lf: pl.LazyFrame) -> int:
    if lf.collect_schema().names() == ():
        return 0
    return int(lf.select(pl.len()).collect().item())


def lazy_mock_row_count(lf: pl.LazyFrame, *, mock_source: str) -> int:
    schema = lf.collect_schema().names()
    if "source" not in schema:
        return 0
    return int(
        lf.filter(pl.col("source") == mock_source).select(pl.len()).collect().item()
    )


def lazy_n_unique_symbol(lf: pl.LazyFrame) -> int | None:
    if "symbol" not in lf.collect_schema().names():
        return None
    return int(lf.select(pl.col("symbol").n_unique()).collect().item())


def partition_col_for_dataset(dataset: str) -> str | None:
    return PARTITION_COLS.get(dataset)
