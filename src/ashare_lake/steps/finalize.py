"""Finalize steps: compact, derive_adj_factors, audit."""

from __future__ import annotations

import logging
from datetime import date

import polars as pl

from ashare_lake.config import Config
from ashare_lake.domain.datasets import PARTITION_COLS, WATERMARK_SKIP
from ashare_lake.orchestrator.registry import register_step
from ashare_lake.storage import StagingWriter, compact_dataset
from ashare_lake.storage.instruments import compact_instruments
from ashare_lake.storage.state import StateStore

logger = logging.getLogger(__name__)


def _max_partition_date(config: Config, dataset: str, partition_col: str) -> date | None:
    """Freshest date actually present, for the watermark.

    Reads the column rather than trusting the directory name: under month/year
    granularity a ``trade_date=2026`` directory says nothing about how far into
    the year the data goes, and taking the period start as the watermark would
    rewind it by up to a year and re-fetch everything since.
    """
    from ashare_lake.query.parquet_scan import list_partitions, partition_dir

    root = config.curated_root / dataset
    if not root.exists():
        return None

    parts = list_partitions(root, partition_col)
    if parts:
        # Newest period only — earlier ones cannot hold a later date.
        files = sorted(partition_dir(root, partition_col, parts[-1].value).glob("**/*.parquet"))
    else:
        files = list(root.glob("**/*.parquet"))
    if not files:
        return None
    combined = pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")
    if partition_col not in combined.columns:
        return None
    return combined[partition_col].max()


def _watermarked_datasets() -> list[tuple[str, str]]:
    return [
        (dataset, pcol)
        for dataset, pcol in PARTITION_COLS.items()
        if pcol is not None and dataset not in WATERMARK_SKIP
    ]


def _update_watermarks(
    config: Config,
    datasets: frozenset[str] | None,
    trade_date: date,
) -> None:
    """Advance each compacted dataset's watermark to the freshest date it holds.

    The watermark answers "through what date do we have data", so it is read
    back from the lake rather than assumed from the run date. Snapshot datasets
    used to be stamped with ``trade_date`` unconditionally, which made the
    watermark lie whenever a snapshot produced nothing: a partial baostock
    valuation backfill writing rows for an *earlier* day still pushed the
    watermark to today, so lake_health called valuation_metrics fresh on a day
    it had zero rows, and the missed days never showed up as coverage gaps.
    Snapshot fetching only ever requests trade_date, so a truthful (possibly
    older) watermark cannot trigger a re-fetch storm — it just surfaces the hole.
    """
    state = StateStore(config.meta_root)
    for dataset, pcol in _watermarked_datasets():
        if datasets is not None and dataset not in datasets:
            continue
        max_dt = _max_partition_date(config, dataset, pcol)
        if max_dt is not None:
            state.update_max_date(dataset, max_dt)


def _reconcile_watermarks(config: Config) -> list[dict]:
    """Pull back any watermark that claims data the lake does not have.

    Advancing only covers datasets that compacted this run, so a dataset whose
    source went dark — writing nothing, therefore never compacting — would keep
    a stale-but-fresh-looking watermark forever, which is exactly the case that
    hides an outage. This runs over every watermarked dataset and only ever
    corrects downward, so it cannot mask a real advance.
    """
    state = StateStore(config.meta_root)
    findings: list[dict] = []
    for dataset, pcol in _watermarked_datasets():
        current = state.get_date(dataset)
        if current is None:
            continue
        max_dt = _max_partition_date(config, dataset, pcol)
        if max_dt is None or current <= max_dt:
            continue
        state.set_date(dataset, max_dt)
        findings.append(
            {
                "dataset": dataset,
                "severity": "warning",
                "check": "watermark_ahead_of_data",
                "message": (
                    f"watermark claimed {current.isoformat()} but the freshest "
                    f"{pcol} in curated is {max_dt.isoformat()}; corrected. The "
                    "source produced nothing for the days in between"
                ),
                "claimed": current.isoformat(),
                "actual": max_dt.isoformat(),
            }
        )
        logger.warning(
            "%s: watermark %s ahead of data %s; corrected",
            dataset,
            current.isoformat(),
            max_dt.isoformat(),
        )
    return findings


@register_step("compact", group="finalize", parallelizable=False)
def step_compact(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    from ashare_lake.orchestrator.run_lock import run_lock

    # Compact does read-merge-write on shared curated partitions; overlapping
    # runs (cron group + manual run) must serialize here or lose rows.
    with run_lock(config.meta_root, "compact", blocking=True):
        return _compact_locked(config, trade_date, run_id, context)


def _compact_locked(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    from ashare_lake.orchestrator.compact_gate import compact_allowed
    from ashare_lake.orchestrator.manifest import Manifest

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
    audit_findings.extend(_reconcile_watermarks(config))

    from ashare_lake.query.views import ensure_duckdb_views

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
    from ashare_lake.derive.adj_factors import (
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
    from ashare_lake.quality.audit import run_audit

    findings = run_audit(config, run_id, trade_date, context)
    return {"rows_read": findings, "rows_written": findings}
