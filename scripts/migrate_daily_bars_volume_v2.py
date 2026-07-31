#!/usr/bin/env python3
"""One-off rewrite: put every curated ``daily_bars.volume`` row in 股 (v1 → v2).

Rows written before the unit fix are in whichever unit their vendor happened to
use — 手 from tdx_protocol and sina, 股 from ths and baostock. Under either
convention some of them are wrong, so there is no reading of the existing lake
that makes the column correct; it has to be rewritten.

What this does, per curated ``daily_bars`` parquet file:

* ``source in {tdx_protocol, sina}`` and ``data_version == 'v1'`` → multiply
  ``volume`` by 100 (手 → 股).
* ``source in {ths, baostock, eastmoney}`` and ``data_version == 'v1'`` →
  leave ``volume`` alone; it was already 股.
* every touched row → ``data_version = 'v2'``.
* rows already at ``v2`` are skipped, so the script is idempotent and safe to
  resume after an interrupt.

``fetched_at`` is deliberately **not** restamped: these rows were fetched when
they were fetched, and rewriting that would erase when the data was actually
observed. ``data_version`` is the column that records the reinterpretation,
which is exactly what it is for — a v1 row means "unit depends on source", a
v2 row means "volume is 股". Readers can tell the two apart.

EastMoney is in the no-conversion list because the only EastMoney rows in the
lake are all-zero suspension placeholders, where ×100 and ×1 agree. Its *live*
unit is 手 and its adapter now converts; see ``ashare_lake.domain.units``.

Usage::

    scripts/migrate_daily_bars_volume_v2.py --config configs/ashare-lake.toml --dry-run
    scripts/migrate_daily_bars_volume_v2.py --config configs/ashare-lake.toml --apply

``--dry-run`` (the default) reports what would change and touches nothing.
Take a backup before ``--apply``; this edits curated data in place.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import polars as pl

from ashare_lake.config import load_config
from ashare_lake.domain.units import SHARES_PER_LOT
from ashare_lake.storage.atomic import write_parquet_atomic

DEFAULT_CONFIG = ROOT / "configs/ashare-lake.toml"

# Sources whose v1 rows were stored in 手 and need rescaling.
LOTS_SOURCES = ("tdx_protocol", "sina")
FROM_VERSION = "v1"
TO_VERSION = "v2"


def migrate_frame(df: pl.DataFrame) -> tuple[pl.DataFrame, int, int]:
    """Return ``(migrated, rescaled_rows, restamped_rows)``.

    ``restamped_rows`` counts every v1 row moved to v2; ``rescaled_rows`` is the
    subset whose ``volume`` was also multiplied.
    """
    stale = pl.col("data_version") == FROM_VERSION
    needs_rescale = stale & pl.col("source").is_in(LOTS_SOURCES)

    restamped = int(df.select(stale.sum()).item())
    if restamped == 0:
        return df, 0, 0
    rescaled = int(df.select(needs_rescale.sum()).item())

    return (
        df.with_columns(
            pl.when(needs_rescale)
            .then(pl.col("volume") * SHARES_PER_LOT)
            .otherwise(pl.col("volume"))
            .cast(pl.Int64)
            .alias("volume"),
            pl.when(stale)
            .then(pl.lit(TO_VERSION))
            .otherwise(pl.col("data_version"))
            .alias("data_version"),
        ),
        rescaled,
        restamped,
    )


def run(curated_root: Path, *, apply: bool) -> int:
    root = curated_root / "daily_bars"
    files = sorted(root.glob("**/*.parquet"))
    if not files:
        print(f"No daily_bars parquet under {root}")
        return 1

    total_rescaled = total_restamped = touched_files = 0
    for i, path in enumerate(files, start=1):
        df = pl.read_parquet(path)
        migrated, rescaled, restamped = migrate_frame(df)
        if restamped == 0:
            continue
        touched_files += 1
        total_rescaled += rescaled
        total_restamped += restamped
        if apply:
            write_parquet_atomic(path, migrated)
        if i % 500 == 0 or i == len(files):
            print(f"  … {i}/{len(files)} files scanned, {touched_files} to change", flush=True)

    verb = "Rewrote" if apply else "Would rewrite"
    print(
        f"\n{verb} {touched_files}/{len(files)} file(s): "
        f"{total_restamped:,} row(s) stamped {FROM_VERSION}→{TO_VERSION}, "
        f"of which {total_rescaled:,} had volume ×{SHARES_PER_LOT} (手→股)."
    )
    if not apply:
        print("Dry run — nothing was written. Re-run with --apply to commit.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the changes. Without it the script only reports.",
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
    print(f"daily_bars volume {FROM_VERSION}→{TO_VERSION} under {cfg.curated_root}")
    return run(cfg.curated_root, apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
