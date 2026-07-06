"""Finalize steps: compact, derive_adj_factors, audit."""

from __future__ import annotations

from datetime import date

import polars as pl

from stock_data_engine.config import Config
from stock_data_engine.orchestrator.registry import register_step
from stock_data_engine.storage import StagingWriter, compact_dataset
from stock_data_engine.storage.instruments import compact_instruments
from stock_data_engine.storage.state import StateStore

_PARTITION_COLS = {
    "instruments": None,
    "trading_calendar": "trade_date",
    "trading_status": "trade_date",
    "daily_bars": "trade_date",
    "index_bars": "trade_date",
    "corporate_actions": "ex_date",
    "fund_flow": "trade_date",
    "margin_trading": "trade_date",
    "northbound_holdings": "trade_date",
    "northbound_flows": "trade_date",
    "valuation_metrics": "trade_date",
    "sector_members": "as_of_date",
    "announcement_index": "announce_date",
    "dragon_tiger": "trade_date",
    "block_trades": "trade_date",
    "financial_statement_items": "report_period",
    "index_constituents": "as_of_date",
    "industry_members": "as_of_date",
    "macro_indicators": "obs_date",
    "market_breadth": "trade_date",
    "share_unlock_schedule": "unlock_date",
    "regulatory_events": "event_date",
    "institutional_holdings": "report_period",
    "analyst_consensus": "forecast_date",
    "sentiment_scores": "trade_date",
}

# Datasets partitioned by non-date keys — skip date-based watermarks.
_WATERMARK_SKIP = frozenset({"financial_statement_items", "institutional_holdings"})


def _max_partition_date(config: Config, dataset: str, partition_col: str) -> date | None:
    root = config.curated_root / dataset
    if not root.exists():
        return None

    prefix = f"{partition_col}="
    max_dt: date | None = None
    for entry in root.iterdir():
        if entry.is_dir() and entry.name.startswith(prefix):
            try:
                candidate = date.fromisoformat(entry.name[len(prefix) :])
            except ValueError:
                continue
            if max_dt is None or candidate > max_dt:
                max_dt = candidate
    if max_dt is not None:
        return max_dt

    files = list(root.glob("**/*.parquet"))
    if not files:
        return None
    combined = pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")
    if partition_col not in combined.columns:
        return None
    return combined[partition_col].max()


def _update_watermarks(config: Config, datasets: frozenset[str] | None = None) -> None:
    state = StateStore(config.meta_root)
    for dataset, pcol in _PARTITION_COLS.items():
        if pcol is None or dataset in _WATERMARK_SKIP:
            continue
        if datasets is not None and dataset not in datasets:
            continue
        max_dt = _max_partition_date(config, dataset, pcol)
        if max_dt is not None:
            state.update_max_date(dataset, max_dt)


@register_step("compact", group="finalize", parallelizable=False)
def step_compact(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    from stock_data_engine.orchestrator.compact_gate import compact_allowed
    from stock_data_engine.orchestrator.manifest import Manifest

    manifest = Manifest(config.manifest_path)
    writer = StagingWriter(config.staging_root)
    staged = [
        ds
        for ds in _PARTITION_COLS
        if writer.list_run_files(ds, run_id)
    ]
    total = 0
    compacted: set[str] = set()
    skipped: list[dict] = []
    audit_findings: list[dict] = []

    for ds in staged:
        allowed, incomplete_count = compact_allowed(manifest, run_id, ds)
        if not allowed:
            skipped.append(
                {
                    "dataset": ds,
                    "incomplete_batches": incomplete_count,
                }
            )
            continue

        pcol = _PARTITION_COLS[ds]
        if ds == "instruments":
            rows, inst_findings = compact_instruments(
                config.staging_root,
                config.curated_root,
                run_id,
                trade_date,
            )
            if rows:
                compacted.add(ds)
            total += rows
            if inst_findings:
                audit_findings.extend(inst_findings)
        elif pcol:
            rows = compact_dataset(
                config.staging_root,
                config.curated_root,
                ds,
                run_id,
                partition_col=pcol,
            )
            if rows:
                compacted.add(ds)
            total += rows

    if compacted:
        _update_watermarks(config, frozenset(compacted))

    from stock_data_engine.query.views import ensure_duckdb_views

    ensure_duckdb_views(config)

    result: dict = {"rows_read": total, "rows_written": total}
    context_updates: dict = {}
    if skipped:
        context_updates["compact_skipped_datasets"] = skipped
    if audit_findings:
        context_updates["audit_findings"] = audit_findings
    if context_updates:
        result["context_updates"] = context_updates
    return result


@register_step(
    "derive_adj_factors",
    group="finalize",
    parallelizable=False,
    depends_on=["daily_bars", "compact"],
)
def step_derive_adj_factors(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    from stock_data_engine.derive.adj_factors import (
        FAIL_RATIO_THRESHOLD,
        AdjFactorsDeriveError,
        compute_adj_factors,
    )

    rebackfill = context.get("symbols_to_rebackfill") or []
    result = compute_adj_factors(config, refresh_symbols=rebackfill)
    out: dict = {"rows_read": result.rows, "rows_written": result.rows}
    if result.findings:
        out["context_updates"] = {"audit_findings": result.findings}
    if result.failed and result.fail_ratio > FAIL_RATIO_THRESHOLD:
        raise AdjFactorsDeriveError(
            (
                f"adj_factors: {len(result.failed)}/{result.task_count} symbol×type tasks "
                f"failed uncached fetch (>{FAIL_RATIO_THRESHOLD:.0%} threshold)"
            ),
            findings=result.findings,
        )
    return out


@register_step(
    "audit",
    group="finalize",
    parallelizable=False,
    depends_on=["compact", "derive_adj_factors"],
)
def step_audit(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    from stock_data_engine.quality.audit import run_audit

    findings = run_audit(config, run_id, trade_date, context)
    return {"rows_read": findings, "rows_written": findings}
