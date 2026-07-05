"""Finalize steps: compact, derive_adj_factors, audit."""

from __future__ import annotations

from datetime import date

import polars as pl

from stock_data_engine.config import Config
from stock_data_engine.orchestrator.registry import register_step
from stock_data_engine.storage import StagingWriter, compact_dataset


@register_step("compact", group="finalize", parallelizable=False)
def step_compact(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    datasets = [
        "instruments",
        "trading_calendar",
        "trading_status",
        "daily_bars",
        "index_bars",
        "corporate_actions",
    ]
    total = 0
    partition_cols = {
        "instruments": "symbol",
        "trading_calendar": "trade_date",
        "trading_status": "trade_date",
        "daily_bars": "trade_date",
        "index_bars": "trade_date",
        "corporate_actions": "ex_date",
    }
    for ds in datasets:
        pcol = partition_cols.get(ds, "trade_date")
        if ds == "instruments":
            files = StagingWriter(config.staging_root).list_run_files(ds, run_id)
            if files:
                combined = pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")
                out = config.curated_root / ds / "part-merged.parquet"
                out.parent.mkdir(parents=True, exist_ok=True)
                combined.write_parquet(out, compression="zstd")
                total += combined.height
        else:
            total += compact_dataset(
                config.staging_root,
                config.curated_root,
                ds,
                run_id,
                partition_col=pcol,
            )
    from stock_data_engine.query.views import ensure_duckdb_views

    ensure_duckdb_views(config)
    return {"rows_read": total, "rows_written": total}


@register_step("derive_adj_factors", group="finalize", parallelizable=False)
def step_derive_adj_factors(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    from stock_data_engine.derive.adj_factors import compute_adj_factors

    rows = compute_adj_factors(config)
    return {"rows_read": rows, "rows_written": rows}


@register_step("audit", group="finalize", parallelizable=False)
def step_audit(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    from stock_data_engine.quality.audit import run_audit

    findings = run_audit(config, run_id, trade_date)
    return {"rows_read": findings, "rows_written": findings}
