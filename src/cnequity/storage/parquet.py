from __future__ import annotations

from pathlib import Path

import polars as pl

from cnequity.domain.canonical import dedupe_by_primary_key
from cnequity.domain.datasets import granularity_for_dataset
from cnequity.domain.partitions import Granularity
from cnequity.domain.schemas import PRIMARY_KEYS, sanitize_dataset_rows, validate_dataframe
from cnequity.storage.atomic import write_parquet_atomic
from cnequity.storage.revisions import sha256_file


class StagingWriter:
    def __init__(self, staging_root: Path | str):
        # Process-pool workers may pass a str path across the boundary.
        self.staging_root = Path(staging_root)

    def write_batch(
        self,
        dataset: str,
        run_id: str,
        batch_id: str,
        df: pl.DataFrame,
    ) -> Path:
        df = sanitize_dataset_rows(validate_dataframe(df, dataset), dataset)
        out_dir = self.staging_root / dataset / f"run_id={run_id}"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"part-{batch_id}.parquet"
        write_parquet_atomic(path, df, compression="zstd")
        return path

    def list_run_files(self, dataset: str, run_id: str) -> list[Path]:
        run_dir = self.staging_root / dataset / f"run_id={run_id}"
        if not run_dir.exists():
            return []
        return sorted(run_dir.rglob("*.parquet"))


class CuratedWriter:
    def __init__(self, curated_root: Path):
        self.curated_root = curated_root

    def partition_path(self, dataset: str, partition_col: str, partition_value: str) -> Path:
        return self.curated_root / dataset / f"{partition_col}={partition_value}"

    def write_partition(
        self,
        dataset: str,
        partition_col: str,
        partition_value: str,
        df: pl.DataFrame,
        part_name: str = "part-0.parquet",
    ) -> Path:
        out_dir = self.partition_path(dataset, partition_col, partition_value)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / part_name
        write_parquet_atomic(path, df, compression="zstd")
        # A previous run may have left fragment files (for example after a
        # staging write used ``part-<batch>.parquet``), possibly under a
        # temporary subdirectory. Keeping them beside the canonical file makes
        # every later recursive scan read the same rows twice, even though the
        # merge above already deduplicated them. Remove only old parquet
        # descendants, and only after the atomic replacement has succeeded so
        # a failed write leaves the old partition readable.
        for stale in out_dir.rglob("*.parquet"):
            if stale != path:
                stale.unlink()
        return path


def _partition_values(df: pl.DataFrame, partition_col: str, granularity: Granularity) -> pl.Series:
    """Directory value per row for *partition_col*.

    Date columns map through the dataset's period (day/month/year); non-date
    keys like ``report_period`` ("2024Q1") are already period labels and are
    used verbatim.
    """
    col = df.get_column(partition_col)
    if col.dtype != pl.Date:
        return col.cast(pl.Utf8)
    if granularity == "year":
        return col.dt.strftime("%Y")
    if granularity == "month":
        return col.dt.strftime("%Y-%m")
    return col.dt.strftime("%Y-%m-%d")


def compact_dataset(
    staging_root: Path,
    curated_root: Path,
    dataset: str,
    run_id: str,
    partition_col: str | None = "trade_date",
    granularity: Granularity | None = None,
    changed_files: list[Path] | None = None,
) -> int:
    """Merge staging batches into curated partitions, dedupe by PK.

    Granularity decides how many dates share one partition directory; it comes
    from the dataset's registry entry unless overridden. See
    ``domain/partitions.py`` for why it is not always a day.
    """
    if granularity is None:
        granularity = granularity_for_dataset(dataset)
    staging = StagingWriter(staging_root)
    curated = CuratedWriter(curated_root)
    files = staging.list_run_files(dataset, run_id)
    if not files:
        return 0

    # Re-validate on read: staging/curated written before a schema change
    # (e.g. fetched_at str → timestamp) must be normalized before concat.
    combined = pl.concat(
        [
            sanitize_dataset_rows(validate_dataframe(pl.read_parquet(f), dataset), dataset)
            for f in files
        ],
        how="diagonal_relaxed",
    )
    pk = PRIMARY_KEYS.get(dataset, [])
    if pk:
        combined = dedupe_by_primary_key(combined, dataset)

    if partition_col not in combined.columns:
        out_dir = curated.curated_root / dataset
        out_path = out_dir / "part-merged.parquet"
        existing_files = sorted(out_dir.rglob("*.parquet")) if out_dir.exists() else []
        before_digest = sha256_file(out_path) if out_path.is_file() else None
        had_fragments = any(path != out_path for path in existing_files)
        if existing_files:
            existing = pl.concat(
                [
                    sanitize_dataset_rows(
                        validate_dataframe(pl.read_parquet(path), dataset), dataset
                    )
                    for path in existing_files
                ],
                how="diagonal_relaxed",
            )
            combined = pl.concat([existing, combined], how="diagonal_relaxed")
            if pk:
                combined = dedupe_by_primary_key(combined, dataset)
        out_dir.mkdir(parents=True, exist_ok=True)
        write_parquet_atomic(out_path, combined, compression="zstd")
        for stale in out_dir.rglob("*.parquet"):
            if stale != out_path:
                stale.unlink()
        if changed_files is not None and (before_digest != sha256_file(out_path) or had_fragments):
            changed_files.append(out_path)
        return combined.height

    _PART = "__partition__"
    combined = combined.with_columns(
        _partition_values(combined, partition_col, granularity).alias(_PART)
    )

    total = 0
    for key, group in combined.partition_by(_PART, as_dict=True).items():
        val_str = str(key[0] if isinstance(key, tuple) else key)
        group = group.drop(_PART)
        existing_dir = curated.partition_path(dataset, partition_col, val_str)
        out_path = existing_dir / "part-merged.parquet"
        before_digest = sha256_file(out_path) if out_path.is_file() else None
        had_fragments = False
        frames = [group]
        if existing_dir.exists():
            for existing in existing_dir.rglob("*.parquet"):
                had_fragments = had_fragments or existing != out_path
                frames.append(
                    sanitize_dataset_rows(
                        validate_dataframe(pl.read_parquet(existing), dataset), dataset
                    )
                )
        merged = pl.concat(frames, how="diagonal_relaxed")
        if pk:
            merged = dedupe_by_primary_key(merged, dataset)
        written = curated.write_partition(
            dataset, partition_col, val_str, merged, "part-merged.parquet"
        )
        if changed_files is not None and (before_digest != sha256_file(written) or had_fragments):
            changed_files.append(written)
        total += merged.height
    return total
