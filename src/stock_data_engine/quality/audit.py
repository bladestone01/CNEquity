from __future__ import annotations

import json
from datetime import date

import polars as pl

from stock_data_engine.config import Config
from stock_data_engine.domain.schemas import MOCK_SOURCE, PRIMARY_KEYS
from stock_data_engine.quality.source_diff import run_source_diffs
from stock_data_engine.query.universe import coverage_start_date, trading_status_coverage_start


def run_audit(config: Config, run_id: str, trade_date: date, context: dict | None = None) -> int:
    findings: list[dict] = []
    context = context or {}

    for skip in context.get("compact_skipped_datasets") or []:
        incomplete = skip.get(
            "incomplete_batches",
            skip.get("failed_batches", 0),
        )
        findings.append(
            {
                "dataset": skip["dataset"],
                "severity": "warning",
                "check": "compact_skipped",
                "message": (
                    f"{incomplete} incomplete batch(es) in run; "
                    "staging not merged and watermark not advanced"
                ),
                "incomplete_batches": incomplete,
            }
        )

    for extra in context.get("audit_findings") or []:
        findings.append(extra)

    ts_start = trading_status_coverage_start(config)
    if ts_start is not None:
        bars_start = coverage_start_date(config, "daily_bars")
        gap = bars_start is not None and ts_start > bars_start
        if gap:
            message = (
                f"trading_status coverage starts at {ts_start.isoformat()} but "
                f"daily_bars starts at {bars_start.isoformat()}; universe=all_a does not "
                "filter ST/suspended before trading_status coverage"
            )
        else:
            message = (
                f"trading_status coverage starts at {ts_start.isoformat()}; "
                "universe=all_a ST/suspended filter applies only on/after this date"
            )
        findings.append(
            {
                "dataset": "trading_status",
                "severity": "warning" if gap else "info",
                "check": "trading_status_coverage_start",
                "message": message,
                "coverage_start": ts_start.isoformat(),
                "daily_bars_start": bars_start.isoformat() if bars_start else None,
            }
        )

    datasets = ["daily_bars", "instruments", "trading_calendar", "index_bars"]

    for ds in datasets:
        root = config.curated_root / ds
        if not root.exists():
            findings.append(
                {
                    "dataset": ds,
                    "severity": "error",
                    "check": "exists",
                    "message": f"No curated data for {ds}",
                }
            )
            continue

        files = list(root.glob("**/*.parquet"))
        if not files:
            findings.append(
                {
                    "dataset": ds,
                    "severity": "error",
                    "check": "non_empty",
                    "message": f"Empty curated {ds}",
                }
            )
            continue

        frames = [pl.read_parquet(f) for f in files]
        df = pl.concat(frames[:20], how="diagonal_relaxed")
        row_count = sum(f.height for f in frames)
        mock_rows = sum(
            f.filter(pl.col("source") == MOCK_SOURCE).height
            for f in frames
            if "source" in f.columns
        )
        if mock_rows:
            findings.append(
                {
                    "dataset": ds,
                    "severity": "error",
                    "check": "mock_source",
                    "message": (
                        f"{mock_rows} fabricated rows (source={MOCK_SOURCE!r}) in curated {ds}; "
                        "regenerate with a real source before using downstream"
                    ),
                }
            )
        findings.append(
            {
                "dataset": ds,
                "severity": "info",
                "check": "row_count",
                "message": f"{row_count} rows across {len(files)} files",
                "sample_columns": df.columns[:10],
            }
        )

        pk = PRIMARY_KEYS.get(ds, [])
        if pk and all(c in df.columns for c in pk):
            dupes = df.height - df.unique(subset=pk).height
            if dupes:
                findings.append(
                    {
                        "dataset": ds,
                        "severity": "error",
                        "check": "pk_unique",
                        "message": f"{dupes} duplicate PK rows in curated {ds}",
                    }
                )

        if ds == "daily_bars" and "close" in df.columns:
            null_close = df.filter(pl.col("close").is_null()).height
            if null_close:
                findings.append(
                    {
                        "dataset": ds,
                        "severity": "warning",
                        "check": "null_close",
                        "message": f"{null_close} rows with null close",
                    }
                )

    out_dir = config.meta_root / "quality" / "findings"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{run_id}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {"run_id": run_id, "trade_date": trade_date.isoformat(), "findings": findings},
            f,
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    diffs = run_source_diffs(config, run_id, trade_date)
    return len(findings) + len(diffs)
