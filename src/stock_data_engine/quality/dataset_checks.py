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
from stock_data_engine.query.parquet_scan import (
    dataset_has_parquet,
    lazy_mock_row_count,
    lazy_n_unique_symbol,
    lazy_row_count,
    list_hive_partition_dates,
    scan_parquet_files,
    scan_parquet_root,
)

_AUDIT_SAMPLE_FILES = 20


def partition_parquet_files(root: Path, partition_col: str, partition_value: date) -> list[Path]:
    part_dir = root / f"{partition_col}={partition_value.isoformat()}"
    if not part_dir.exists():
        return []
    return sorted(part_dir.glob("**/*.parquet"))


def partition_row_stats(files: list[Path]) -> dict[str, int | None]:
    if not files:
        return {"rows": 0, "symbols": None}
    lf = scan_parquet_files(files)
    return {
        "rows": lazy_row_count(lf),
        "symbols": lazy_n_unique_symbol(lf),
    }


def _sample_files(files: list[Path], limit: int = _AUDIT_SAMPLE_FILES) -> list[Path]:
    return files[:limit] if len(files) <= limit else files[:limit]


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
    from stock_data_engine.domain.datasets import DATASETS

    required = DATASETS[dataset].required if dataset in DATASETS else True
    empty_severity = "error" if required else "warning"

    if not root.exists():
        findings.append(
            {
                "dataset": dataset,
                "severity": empty_severity,
                "check": "exists",
                "message": f"No curated data for {dataset}",
            }
        )
        return findings

    if not dataset_has_parquet(root):
        findings.append(
            {
                "dataset": dataset,
                "severity": empty_severity,
                "check": "non_empty",
                "message": f"Empty curated {dataset}",
            }
        )
        return findings

    audit_files: list[Path] | None = None
    partition_value: date | None = None
    previous_value: date | None = None
    audit_lf: pl.LazyFrame

    if partition_col is not None:
        partition_dates = list_hive_partition_dates(root, partition_col)
        if trade_date in partition_dates:
            partition_value = trade_date
            prior = [d for d in partition_dates if d < trade_date]
            previous_value = prior[-1] if prior else None
            part_files = partition_parquet_files(root, partition_col, trade_date)
            if part_files:
                audit_files = part_files
                audit_lf = scan_parquet_files(part_files)
            else:
                audit_lf = scan_parquet_root(
                    root,
                    partition_col=partition_col,
                    start=trade_date,
                    end=trade_date,
                )
        else:
            audit_lf = scan_parquet_root(root, partition_col=partition_col)
    else:
        audit_lf = scan_parquet_root(root, hive=False)

    sample_lf = (
        scan_parquet_files(_sample_files(audit_files))
        if audit_files is not None
        else audit_lf.limit(_AUDIT_SAMPLE_FILES)
    )
    sample_df = sample_lf.collect()
    row_count = lazy_row_count(audit_lf)
    mock_rows = lazy_mock_row_count(audit_lf, mock_source=MOCK_SOURCE)
    file_count = len(audit_files) if audit_files is not None else None

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
                f"{row_count} rows"
                + (
                    f" in {partition_col}={partition_value.isoformat()}"
                    if partition_value is not None
                    else " across dataset"
                )
            ),
            "sample_columns": sample_df.columns[:10],
            "partition_col": partition_col,
            "partition_value": partition_value.isoformat() if partition_value else None,
            "file_count": file_count,
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
