#!/usr/bin/env python3
"""Add the optional bitemporal columns to legacy PIT Parquet files.

The migration is intentionally additive and conservative:

* ``observed_at`` is copied from the legacy ``fetched_at`` timestamp;
* ``revision_id`` is a deterministic hash of the business fact, value, and
  provenance;
* ``available_at`` and ``source_published_at`` remain null when the old file
  did not record those source-side times.  Guessing them from a backfill's
  report date would turn a reconstructed value into a false strict vintage.

The four columns are nullable and readers also fill them on the fly, so this
script is optional for correctness.  It is useful when downstream jobs need a
stable physical schema or when a lake owner wants to make the compatibility
migration explicit.  Running it repeatedly is safe: files that already carry
the same values are not rewritten.

Usage::

    scripts/migrate_pit_vintages.py --config configs/cnequity.toml --dry-run
    scripts/migrate_pit_vintages.py --config configs/cnequity.toml --apply
    scripts/migrate_pit_vintages.py --dataset financial_statement_items --apply

Dry-run is the default; ``--apply`` is required to edit curated files.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import polars as pl

from cnequity.config import load_config
from cnequity.domain.pit import (
    PIT_DATASET_NAMES,
    normalize_pit_storage_columns,
)
from cnequity.storage.atomic import write_parquet_atomic

DEFAULT_CONFIG = ROOT / "configs/cnequity.toml"


def migrate_frame(df: pl.DataFrame, dataset: str) -> tuple[pl.DataFrame, bool]:
    """Return ``(migrated, changed)`` for one PIT frame."""

    if dataset not in PIT_DATASET_NAMES:
        raise ValueError(f"{dataset!r} is not a registered PIT dataset")
    migrated = normalize_pit_storage_columns(df, dataset)
    return migrated, not df.equals(migrated)


def run(curated_root: Path, *, datasets: tuple[str, ...], apply: bool) -> int:
    """Migrate selected PIT datasets below *curated_root*.

    Return a process-style status code.  A missing dataset root is not an
    error: it is common for optional shareholder feeds not to have been
    initialized yet.
    """

    total_files = changed_files = total_rows = 0
    for dataset in datasets:
        root = curated_root / dataset
        files = sorted(root.glob("**/*.parquet")) if root.exists() else []
        if not files:
            print(f"{dataset}: no parquet under {root}")
            continue
        dataset_changed = 0
        for path in files:
            frame = pl.read_parquet(path)
            migrated, changed = migrate_frame(frame, dataset)
            total_files += 1
            total_rows += frame.height
            if not changed:
                continue
            changed_files += 1
            dataset_changed += 1
            if apply:
                write_parquet_atomic(path, migrated, compression="zstd")
        print(
            f"{dataset}: scanned {len(files)} file(s), "
            f"{'rewrote' if apply else 'would rewrite'} "
            f"{dataset_changed} file(s)"
        )

    verb = "Rewrote" if apply else "Would rewrite"
    print(f"\n{verb} {changed_files}/{total_files} PIT file(s), {total_rows:,} row(s) scanned.")
    if not apply:
        print("Dry run — nothing was written. Re-run with --apply to commit.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--dataset",
        action="append",
        choices=sorted(PIT_DATASET_NAMES),
        help="Migrate one PIT dataset (repeatable; default: all PIT datasets).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes. Without it the script only reports.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicit no-op form of the default behaviour.",
    )
    args = parser.parse_args()
    if args.apply and args.dry_run:
        parser.error("--apply and --dry-run are mutually exclusive")

    cfg = load_config(args.config)
    datasets = tuple(args.dataset or sorted(PIT_DATASET_NAMES))
    print(f"PIT bitemporal columns under {cfg.curated_root}")
    return run(cfg.curated_root, datasets=datasets, apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
