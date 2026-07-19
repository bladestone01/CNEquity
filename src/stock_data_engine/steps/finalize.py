"""Finalize steps: compact, derive_adj_factors, audit."""

from __future__ import annotations

from datetime import date

import polars as pl

from stock_data_engine.config import Config
from stock_data_engine.domain.datasets import PARTITION_COLS, WATERMARK_SKIP, fetch_semantics
from stock_data_engine.orchestrator.registry import register_step
from stock_data_engine.storage import StagingWriter, compact_dataset
from stock_data_engine.storage.instruments import compact_instruments
from stock_data_engine.storage.state import StateStore


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


def _update_watermarks(
    config: Config,
    datasets: frozenset[str] | None,
    trade_date: date,
) -> None:
    state = StateStore(config.meta_root)
    for dataset, pcol in PARTITION_COLS.items():
        if pcol is None or dataset in WATERMARK_SKIP:
            continue
        if datasets is not None and dataset not in datasets:
            continue
        if fetch_semantics(dataset) == "snapshot":
            state.set_date(dataset, trade_date)
            continue
        max_dt = _max_partition_date(config, dataset, pcol)
        if max_dt is not None:
            state.update_max_date(dataset, max_dt)


@register_step("compact", group="finalize", parallelizable=False)
def step_compact(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    from stock_data_engine.orchestrator.run_lock import run_lock

    # Compact does read-merge-write on shared curated partitions; overlapping
    # runs (cron group + manual run) must serialize here or lose rows.
    with run_lock(config.meta_root, "compact", blocking=True):
        return _compact_locked(config, trade_date, run_id, context)


def _compact_locked(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    from stock_data_engine.orchestrator.compact_gate import compact_allowed
    from stock_data_engine.orchestrator.manifest import Manifest

    manifest = Manifest(config.manifest_path)
    writer = StagingWriter(config.staging_root)
    staged = [ds for ds in PARTITION_COLS if writer.list_run_files(ds, run_id)]
    total = 0
    compacted: set[str] = set()
    skipped: list[dict] = []
    audit_findings: list[dict] = []

    for ds in staged:
        allowed, incomplete_count = compact_allowed(
            manifest,
            run_id,
            ds,
            stale_after_seconds=config.batch_stale_seconds,
        )
        if not allowed:
            skipped.append(
                {
                    "dataset": ds,
                    "incomplete_batches": incomplete_count,
                }
            )
            continue

        pcol = PARTITION_COLS[ds]
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
        _update_watermarks(config, frozenset(compacted), trade_date)

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
