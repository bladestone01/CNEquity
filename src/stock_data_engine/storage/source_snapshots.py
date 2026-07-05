"""Backup-source snapshot storage (ADR-0003 — never write to curated)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from stock_data_engine.domain.schemas import validate_dataframe


class SnapshotStore:
    """Write/read backup-source captures under ``meta/source_snapshots/``."""

    def __init__(self, meta_root: Path):
        self.root = meta_root / "source_snapshots"

    def write(
        self,
        dataset: str,
        df: pl.DataFrame,
        *,
        source: str,
        data_version: str,
        run_id: str,
        batch_id: str = "backup",
        trade_date: date | None = None,
    ) -> Path | None:
        if df.is_empty():
            return None
        df = validate_dataframe(df, dataset)
        out_dir = (
            self.root
            / dataset
            / f"source={source}"
            / f"data_version={data_version}"
            / f"run_id={run_id}"
        )
        if trade_date is not None:
            out_dir = out_dir / f"trade_date={trade_date.isoformat()}"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"part-{batch_id}.parquet"
        df.write_parquet(path, compression="zstd")
        return path

    def list_files(
        self,
        dataset: str,
        *,
        source: str | None = None,
        run_id: str | None = None,
    ) -> list[Path]:
        base = self.root / dataset
        if not base.exists():
            return []
        pattern = "**/*.parquet"
        if source:
            base = base / f"source={source}"
        if not base.exists():
            return []
        files = sorted(base.glob(pattern))
        if run_id:
            files = [f for f in files if f"run_id={run_id}" in str(f)]
        return files

    def read_latest(self, dataset: str, *, source: str) -> pl.DataFrame:
        files = self.list_files(dataset, source=source)
        if not files:
            return pl.DataFrame()
        return pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")
