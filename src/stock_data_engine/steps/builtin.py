from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from stock_data_engine.adapters.tdx_protocol.client import (
    fetch_corporate_actions,
    fetch_index_bars,
    fetch_instruments,
    fetch_trading_calendar,
    fetch_trading_status,
    normalize_with_source,
)
from stock_data_engine.config import Config
from stock_data_engine.orchestrator.registry import register_step
from stock_data_engine.storage import StagingWriter, compact_dataset
from stock_data_engine.workers.pool import fetch_daily_bars_parallel


def _write_simple(config: Config, run_id: str, dataset: str, df: pl.DataFrame) -> dict:
    writer = StagingWriter(config.staging_root)
    writer.write_batch(dataset, run_id, "batch-0", df)
    return {"rows_read": df.height, "rows_written": df.height}


@register_step("instruments", group="core", requires_workers=False)
def step_instruments(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    rl = config.tdx_rate_limit_spec()
    df = fetch_instruments(rate_limit=rl, allow_mock=config.tdx_allow_mock)
    df = normalize_with_source(df)
    return _write_simple(config, run_id, "instruments", df)


@register_step("trading_calendar", group="core")
def step_trading_calendar(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    start = trade_date - timedelta(days=30)
    end = trade_date + timedelta(days=365)
    rl = config.tdx_rate_limit_spec()
    df = fetch_trading_calendar(start, end, rate_limit=rl, allow_mock=config.tdx_allow_mock)
    df = normalize_with_source(df)
    return _write_simple(config, run_id, "trading_calendar", df)


@register_step("trading_status", group="core")
def step_trading_status(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    symbols = context.get("symbols") or _load_symbols(config)
    rl = config.tdx_rate_limit_spec()
    df = fetch_trading_status(
        symbols[:500], trade_date, rate_limit=rl, allow_mock=config.tdx_allow_mock
    )
    df = normalize_with_source(df)
    return _write_simple(config, run_id, "trading_status", df)


@register_step("corporate_actions", group="core", depends_on=["instruments"])
def step_corporate_actions(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    rl = config.tdx_rate_limit_spec()
    df = fetch_corporate_actions(trade_date, rate_limit=rl, allow_mock=config.tdx_allow_mock)
    df = normalize_with_source(df)
    rebackfill = []
    if df.height and "symbol" in df.columns:
        rebackfill = df["symbol"].unique().to_list()
    context_updates = {"symbols_to_rebackfill": rebackfill}
    result = _write_simple(config, run_id, "corporate_actions", df)
    result["context_updates"] = context_updates
    return result


@register_step(
    "daily_bars",
    group="core",
    depends_on=["instruments", "corporate_actions"],
    requires_workers=True,
)
def step_daily_bars(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    symbols = _load_symbols(config)
    rebackfill = context.get("symbols_to_rebackfill") or []
    if rebackfill:
        symbols = list(dict.fromkeys(rebackfill + symbols))

    if getattr(config, "_backfill", False):
        start = date(2016, 1, 1)
    else:
        start = trade_date - timedelta(days=5)
    end = trade_date

    result = fetch_daily_bars_parallel(config, symbols, start, end, run_id, "daily_bars")
    return result


@register_step("index_bars", group="core", depends_on=["instruments"])
def step_index_bars(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    start = (
        trade_date - timedelta(days=5)
        if not getattr(config, "_backfill", False)
        else date(2016, 1, 1)
    )
    rl = config.tdx_rate_limit_spec()
    df = fetch_index_bars(start, trade_date, rate_limit=rl, allow_mock=config.tdx_allow_mock)
    df = normalize_with_source(df)
    return _write_simple(config, run_id, "index_bars", df)


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
    from stock_data_engine.duckdb.views import ensure_duckdb_views

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


def _load_symbols(config: Config) -> list[str]:
    curated = config.curated_root / "instruments" / "part-merged.parquet"
    staging_glob = list(config.staging_root.glob("instruments/run_id=*/part-*.parquet"))
    if curated.exists():
        return pl.read_parquet(curated)["symbol"].to_list()
    if staging_glob:
        latest = max(staging_glob, key=lambda p: p.stat().st_mtime)
        return pl.read_parquet(latest)["symbol"].to_list()
    df = fetch_instruments(
        rate_limit=config.tdx_rate_limit_spec(), allow_mock=config.tdx_allow_mock
    )
    return df["symbol"].to_list()
