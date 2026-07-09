from __future__ import annotations

import json
from datetime import date

import polars as pl

from stock_data_engine.adapters.calendar.exchange_calendar import (
    CALENDAR_FORWARD_COVERAGE_WARN_DAYS,
    calendar_forward_coverage_days,
    calendar_seed_end,
)
from stock_data_engine.config import Config
from stock_data_engine.domain.datasets import PARTITION_COLS
from stock_data_engine.quality.cross_checks import (
    daily_bars_calendar_findings,
    valuation_bars_coverage_findings,
)
from stock_data_engine.quality.dataset_checks import audit_curated_dataset
from stock_data_engine.quality.source_diff import run_source_diffs
from stock_data_engine.query.parquet_scan import dataset_has_parquet, scan_parquet_root
from stock_data_engine.query.universe import (
    coverage_start_date,
    st_coverage_start,
    trading_status_coverage_start,
)

# Sample missing/orphan dates surfaced in a coverage finding.
_INDEX_COVERAGE_SAMPLE = 8


def _index_bars_coverage_findings(config: Config, trade_date: date) -> list[dict]:
    """Flag index symbols whose curated bars don't match the trading calendar.

    An index quotes every trading day, so within a symbol's covered span every
    calendar trading day must have a bar (missing days) and every bar day must
    be a calendar trading day (orphan bars). Divergence means either a fetch
    gap in index_bars or a wrong trading_calendar — both shrink the benchmark
    sample used downstream for excess-return / tracking-error stats.
    """
    findings: list[dict] = []
    cal_root = config.curated_root / "trading_calendar"
    ib_root = config.curated_root / "index_bars"
    if not dataset_has_parquet(cal_root) or not dataset_has_parquet(ib_root):
        return findings

    cal = (
        scan_parquet_root(cal_root, partition_col="trade_date", end=trade_date)
        .filter(pl.col("is_trading"))
        .select("trade_date")
        .unique()
        .collect()
    )
    trading_days = set(cal["trade_date"].to_list())
    if not trading_days:
        return findings

    ib = (
        scan_parquet_root(ib_root, partition_col="trade_date", end=trade_date)
        .select("symbol", "trade_date")
        .unique()
        .collect()
    )
    if ib.is_empty():
        return findings

    for sym in sorted(ib["symbol"].unique().to_list()):
        days = sorted(ib.filter(pl.col("symbol") == sym)["trade_date"].to_list())
        first, last = days[0], days[-1]
        have = set(days)
        expected = {d for d in trading_days if first <= d <= last}
        missing = sorted(expected - have)
        orphan = sorted(d for d in days if d not in trading_days)
        if not missing and not orphan:
            continue
        parts = []
        if missing:
            parts.append(f"{len(missing)} calendar trading day(s) with no bar")
        if orphan:
            parts.append(f"{len(orphan)} bar(s) on non-trading days")
        findings.append(
            {
                "dataset": "index_bars",
                "symbol": sym,
                "severity": "warning",
                "check": "index_bars_calendar_coverage",
                "message": (
                    f"{sym}: " + "; ".join(parts) + f" over {first.isoformat()}..{last.isoformat()}"
                ),
                "covered_days": len(have),
                "expected_days": len(expected),
                "missing_count": len(missing),
                "orphan_count": len(orphan),
                "missing_sample": [d.isoformat() for d in missing[:_INDEX_COVERAGE_SAMPLE]],
                "orphan_sample": [d.isoformat() for d in orphan[:_INDEX_COVERAGE_SAMPLE]],
            }
        )
    return findings


def _collect_lake_findings(
    config: Config, trade_date: date, context: dict | None = None
) -> list[dict]:
    """All quality findings for the current curated lake (run-independent)."""
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
        st_start = st_coverage_start(config)
        # Suspension is reconstructed from bar gaps across the whole history, so
        # the only residual universe-filter gap is ST *labels* before st_start.
        st_gap = (
            st_start is not None and bars_start is not None and st_start > bars_start
        )
        if st_start is None:
            message = (
                "trading_status has suspension history (from bar gaps) but no ST "
                "labels yet; universe=all_a does not exclude ST names — run the "
                "trading_status step with AKShare/EM ST enabled"
            )
        elif st_gap:
            message = (
                f"suspension covered from {ts_start.isoformat()}; ST labels only "
                f"from {st_start.isoformat()} (daily_bars start {bars_start.isoformat()}) "
                "— ST names not excluded in earlier backtest windows"
            )
        else:
            message = (
                f"trading_status: suspension + ST labels cover from {ts_start.isoformat()}"
            )
        findings.append(
            {
                "dataset": "trading_status",
                "severity": "warning" if (st_gap or st_start is None) else "info",
                "check": "trading_status_coverage_start",
                "message": message,
                "coverage_start": ts_start.isoformat(),
                "st_coverage_start": st_start.isoformat() if st_start else None,
                "daily_bars_start": bars_start.isoformat() if bars_start else None,
            }
        )

    findings.extend(_index_bars_coverage_findings(config, trade_date))
    findings.extend(daily_bars_calendar_findings(config, trade_date))
    findings.extend(valuation_bars_coverage_findings(config, trade_date))

    for ds, pcol in PARTITION_COLS.items():
        findings.extend(
            audit_curated_dataset(
                ds,
                pcol,
                config.curated_root / ds,
                trade_date,
            )
        )
    return findings


def run_audit(config: Config, run_id: str, trade_date: date, context: dict | None = None) -> int:
    findings = _collect_lake_findings(config, trade_date, context)

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


def lake_health(config: Config, trade_date: date) -> dict:
    """Whole-lake health snapshot: current findings + per-dataset freshness.

    Independent of any run's stale per-run findings file. Writes a stable
    ``meta/quality/health-latest.json`` and returns the summary.
    """
    from stock_data_engine.domain.datasets import is_stale
    from stock_data_engine.query.reader import list_datasets

    findings = _collect_lake_findings(config, trade_date, None)
    by_severity: dict[str, int] = {}
    for f in findings:
        sev = f.get("severity", "info")
        by_severity[sev] = by_severity.get(sev, 0) + 1

    anchor = _last_trading_day(config, trade_date)
    catalog = list_datasets(config=config)
    stale: list[str] = []
    empty: list[str] = []
    for row in catalog.iter_rows(named=True):
        if not row["has_data"]:
            empty.append(row["dataset"])
            continue
        if not row["watermarked"]:
            continue
        mark = row["watermark"] or row["coverage_end"]
        # Tolerance per dataset cadence (T+1, quarterly …) so inherent lag is
        # not mistaken for a stuck pipeline.
        if is_stale(row["dataset"], mark, anchor):
            stale.append(row["dataset"])

    health = {
        "trade_date": trade_date.isoformat(),
        "last_trading_day": anchor.isoformat(),
        "findings_by_severity": by_severity,
        "error_findings": [f for f in findings if f.get("severity") == "error"],
        "warning_findings": [f for f in findings if f.get("severity") == "warning"],
        "stale_datasets": sorted(stale),
        "empty_datasets": sorted(empty),
        "healthy": by_severity.get("error", 0) == 0 and not stale,
    }

    out_dir = config.meta_root / "quality"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "health-latest.json", "w", encoding="utf-8") as f:
        json.dump(health, f, ensure_ascii=False, indent=2, default=str)
    return health


def _last_trading_day(config: Config, trade_date: date) -> date:
    from datetime import timedelta

    from stock_data_engine.steps.common import is_trading_day

    d = trade_date
    for _ in range(15):
        if is_trading_day(config, d):
            return d
        d -= timedelta(days=1)
    return trade_date
