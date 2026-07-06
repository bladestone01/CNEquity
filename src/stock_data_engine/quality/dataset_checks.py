"""Curated dataset existence, integrity, and partition row-count sentinels."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from stock_data_engine.domain.datasets import (
    ROW_COUNT_MUTATION_MIN_BASELINE_ROWS,
    ROW_COUNT_MUTATION_MIN_RATIO,
)
from stock_data_engine.domain.schemas import MOCK_SOURCE, PRIMARY_KEYS

_AUDIT_SAMPLE_FILES = 20


def list_partition_dates(root: Path, partition_col: str) -> list[date]:
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


def _partition_dir(root: Path, partition_col: str, partition_value: date) -> Path:
    return root / f"{partition_col}={partition_value.isoformat()}"


def partition_parquet_files(root: Path, partition_col: str, partition_value: date) -> list[Path]:
    part_dir = _partition_dir(root, partition_col, partition_value)
    if not part_dir.exists():
        return []
    return sorted(part_dir.glob("**/*.parquet"))


def partition_row_stats(files: list[Path]) -> dict[str, int | None]:
    if not files:
        return {"rows": 0, "symbols": None}
    frames = [pl.read_parquet(f) for f in files]
    df = pl.concat(frames, how="diagonal_relaxed")
    symbols = int(df["symbol"].n_unique()) if "symbol" in df.columns else None
    return {"rows": sum(f.height for f in frames), "symbols": symbols}


def _sample_files(files: list[Path], limit: int = _AUDIT_SAMPLE_FILES) -> list[Path]:
    return files[:limit] if len(files) <= limit else files[:limit]


def _mock_row_count(files: list[Path]) -> int:
    total = 0
    for path in files:
        df = pl.read_parquet(path)
        if "source" not in df.columns:
            continue
        total += df.filter(pl.col("source") == MOCK_SOURCE).height
    return total


def _pk_duplicate_count(df: pl.DataFrame, dataset: str) -> int:
    pk = PRIMARY_KEYS.get(dataset, [])
    if not pk or not all(c in df.columns for c in pk):
        return 0
    return df.height - df.unique(subset=pk).height


def _mutation_ratio(current: int, baseline: int) -> float:
    if baseline <= 0:
        return 1.0
    return current / baseline


def check_partition_row_mutation(
    dataset: str,
    partition_col: str,
    *,
    current_value: date,
    previous_value: date,
    current_stats: dict[str, int | None],
    previous_stats: dict[str, int | None],
) -> dict | None:
    prev_rows = int(previous_stats["rows"])
    cur_rows = int(current_stats["rows"])
    if prev_rows < ROW_COUNT_MUTATION_MIN_BASELINE_ROWS:
        return None

    row_ratio = _mutation_ratio(cur_rows, prev_rows)
    row_triggered = row_ratio < ROW_COUNT_MUTATION_MIN_RATIO

    symbol_triggered = False
    symbol_ratio = None
    prev_symbols = previous_stats.get("symbols")
    cur_symbols = current_stats.get("symbols")
    if prev_symbols is not None and cur_symbols is not None:
        prev_symbols = int(prev_symbols)
        cur_symbols = int(cur_symbols)
        if prev_symbols >= ROW_COUNT_MUTATION_MIN_BASELINE_ROWS:
            symbol_ratio = _mutation_ratio(cur_symbols, prev_symbols)
            symbol_triggered = symbol_ratio < ROW_COUNT_MUTATION_MIN_RATIO

    if not row_triggered and not symbol_triggered:
        return None

    parts = [
        (
            f"partition {partition_col}={current_value.isoformat()} has {cur_rows} rows "
            f"vs {prev_rows} on {previous_value.isoformat()} "
            f"({row_ratio:.0%} of prior)"
        )
    ]
    if symbol_ratio is not None:
        parts.append(
            f"symbols {cur_symbols} vs {prev_symbols} ({symbol_ratio:.0%} of prior)"
        )
    return {
        "dataset": dataset,
        "severity": "warning",
        "check": "row_count_mutation",
        "message": "; ".join(parts),
        "partition_col": partition_col,
        "current_partition": current_value.isoformat(),
        "previous_partition": previous_value.isoformat(),
        "current_rows": cur_rows,
        "previous_rows": prev_rows,
        "row_ratio": round(row_ratio, 4),
        "current_symbols": cur_symbols,
        "previous_symbols": prev_symbols,
        "min_ratio_threshold": ROW_COUNT_MUTATION_MIN_RATIO,
    }


def audit_curated_dataset(
    dataset: str,
    partition_col: str | None,
    root: Path,
    trade_date: date,
) -> list[dict]:
    findings: list[dict] = []

    if not root.exists():
        findings.append(
            {
                "dataset": dataset,
                "severity": "error",
                "check": "exists",
                "message": f"No curated data for {dataset}",
            }
        )
        return findings

    all_files = sorted(root.glob("**/*.parquet"))
    if not all_files:
        findings.append(
            {
                "dataset": dataset,
                "severity": "error",
                "check": "non_empty",
                "message": f"Empty curated {dataset}",
            }
        )
        return findings

    audit_files = all_files
    partition_value: date | None = None
    previous_value: date | None = None

    if partition_col is not None:
        partition_dates = list_partition_dates(root, partition_col)
        if trade_date in partition_dates:
            partition_value = trade_date
            prior = [d for d in partition_dates if d < trade_date]
            previous_value = prior[-1] if prior else None
            part_files = partition_parquet_files(root, partition_col, trade_date)
            if part_files:
                audit_files = part_files

    sample = _sample_files(audit_files)
    sample_df = pl.concat([pl.read_parquet(f) for f in sample], how="diagonal_relaxed")
    row_count = sum(pl.read_parquet(f).height for f in audit_files)
    mock_rows = _mock_row_count(audit_files)

    if mock_rows:
        findings.append(
            {
                "dataset": dataset,
                "severity": "error",
                "check": "mock_source",
                "message": (
                    f"{mock_rows} fabricated rows (source={MOCK_SOURCE!r}) in curated {dataset}; "
                    "regenerate with a real source before using downstream"
                ),
            }
        )

    findings.append(
        {
            "dataset": dataset,
            "severity": "info",
            "check": "row_count",
            "message": (
                f"{row_count} rows across {len(audit_files)} file(s)"
                + (
                    f" in {partition_col}={partition_value.isoformat()}"
                    if partition_value is not None
                    else f" across {len(all_files)} file(s)"
                )
            ),
            "sample_columns": sample_df.columns[:10],
            "partition_col": partition_col,
            "partition_value": partition_value.isoformat() if partition_value else None,
        }
    )

    dupes = _pk_duplicate_count(sample_df, dataset)
    if dupes:
        findings.append(
            {
                "dataset": dataset,
                "severity": "error",
                "check": "pk_unique",
                "message": f"{dupes} duplicate PK rows in curated {dataset} sample",
            }
        )

    if dataset == "daily_bars" and "close" in sample_df.columns:
        null_close = sample_df.filter(pl.col("close").is_null()).height
        if null_close:
            findings.append(
                {
                    "dataset": dataset,
                    "severity": "warning",
                    "check": "null_close",
                    "message": f"{null_close} rows with null close in sample",
                }
            )

    if (
        partition_col is not None
        and partition_value is not None
        and previous_value is not None
    ):
        current_stats = partition_row_stats(
            partition_parquet_files(root, partition_col, partition_value)
        )
        previous_stats = partition_row_stats(
            partition_parquet_files(root, partition_col, previous_value)
        )
        mutation = check_partition_row_mutation(
            dataset,
            partition_col,
            current_value=partition_value,
            previous_value=previous_value,
            current_stats=current_stats,
            previous_stats=previous_stats,
        )
        if mutation is not None:
            findings.append(mutation)

    return findings
