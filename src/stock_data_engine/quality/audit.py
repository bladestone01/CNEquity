from __future__ import annotations

import json
from datetime import date

import polars as pl

from stock_data_engine.config import Config


def run_audit(config: Config, run_id: str, trade_date: date) -> int:
    findings: list[dict] = []
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

        df = pl.concat([pl.read_parquet(f) for f in files[:20]], how="diagonal_relaxed")
        row_count = sum(pl.read_parquet(f).height for f in files)
        findings.append(
            {
                "dataset": ds,
                "severity": "info",
                "check": "row_count",
                "message": f"{row_count} rows across {len(files)} files",
                "sample_columns": df.columns[:10],
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
        )
    return len(findings)
