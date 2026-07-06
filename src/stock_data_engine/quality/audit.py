from __future__ import annotations

import json
from datetime import date

from stock_data_engine.adapters.calendar.exchange_calendar import (
    CALENDAR_FORWARD_COVERAGE_WARN_DAYS,
    calendar_forward_coverage_days,
    calendar_seed_end,
)
from stock_data_engine.config import Config
from stock_data_engine.domain.datasets import PARTITION_COLS
from stock_data_engine.quality.dataset_checks import audit_curated_dataset
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

    seed_end = calendar_seed_end()
    forward_days = calendar_forward_coverage_days(trade_date)
    if forward_days < CALENDAR_FORWARD_COVERAGE_WARN_DAYS:
        findings.append(
            {
                "dataset": "trading_calendar",
                "severity": "warning",
                "check": "calendar_forward_coverage",
                "message": (
                    f"holiday seed hardcoded through {seed_end.isoformat()}; "
                    f"only {forward_days} day(s) forward from {trade_date.isoformat()}; "
                    "extend holidays_cn.py before calendar goes stale"
                ),
                "seed_end": seed_end.isoformat(),
                "forward_days": forward_days,
                "warn_threshold_days": CALENDAR_FORWARD_COVERAGE_WARN_DAYS,
            }
        )

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

    for ds, pcol in PARTITION_COLS.items():
        findings.extend(
            audit_curated_dataset(
                ds,
                pcol,
                config.curated_root / ds,
                trade_date,
            )
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
