"""Finalize steps: compact, derive_adj_factors, audit."""

from __future__ import annotations

from datetime import date

import polars as pl

from stock_data_engine.config import Config
from stock_data_engine.orchestrator.registry import register_step
from stock_data_engine.storage import StagingWriter, compact_dataset
from stock_data_engine.storage.state import StateStore

_PARTITION_COLS = {
    "instruments": None,
    "trading_calendar": "trade_date",
    "trading_status": "trade_date",
    "daily_bars": "trade_date",
    "index_bars": "trade_date",
    "corporate_actions": "ex_date",
}


def _max_partition_date(config: Config, dataset: str, partition_col: str) -> date | None:
    root = config.curated_root / dataset
    if not root.exists():
        return None
    files = list(root.glob("**/*.parquet"))
    if not files:
        return None
    combined = pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")
    if partition_col not in combined.columns:
        return None
    return combined[partition_col].max()


def _update_watermarks(config: Config) -> None:
    state = StateStore(config.meta_root)
    for dataset, pcol in _PARTITION_COLS.items():
        if pcol is None:
            continue
        max_dt = _max_partition_date(config, dataset, pcol)
        if max_dt is not None:
            state.update_max_date(dataset, max_dt)


@register_step("compact", group="finalize", parallelizable=False)
def step_compact(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    datasets = list(_PARTITION_COLS.keys())
    total = 0
    for ds in datasets:
        pcol = _PARTITION_COLS[ds]
        if ds == "instruments":
            files = StagingWriter(config.staging_root).list_run_files(ds, run_id)
            if files:
                combined = pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")
                out = config.curated_root / ds / "part-merged.parquet"
                out.parent.mkdir(parents=True, exist_ok=True)
                combined.write_parquet(out, compression="zstd")
                total += combined.height
        elif pcol:
            total += compact_dataset(
                config.staging_root,
                config.curated_root,
                ds,
                run_id,
                partition_col=pcol,
            )

    _update_watermarks(config)

    from stock_data_engine.query.views import ensure_duckdb_views

    ensure_duckdb_views(config)
    return {"rows_read": total, "rows_written": total}


@register_step(
    "derive_adj_factors",
    group="finalize",
    parallelizable=False,
    depends_on=["daily_bars", "compact"],
)
def step_derive_adj_factors(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    from stock_data_engine.derive.adj_factors import compute_adj_factors

    rebackfill = context.get("symbols_to_rebackfill") or []
    rows = compute_adj_factors(config, refresh_symbols=rebackfill)
    return {"rows_read": rows, "rows_written": rows}


@register_step("audit", group="finalize", parallelizable=False)
def step_audit(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    from stock_data_engine.quality.audit import run_audit

    findings = run_audit(config, run_id, trade_date)
    return {"rows_read": findings, "rows_written": findings}
