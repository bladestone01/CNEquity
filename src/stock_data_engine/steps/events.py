"""L2 corporate-event steps: corporate_actions, announcement_index,
earnings_disclosure_schedule."""

from __future__ import annotations

from datetime import date

import polars as pl

from stock_data_engine.adapters.cninfo.announcements import fetch_announcement_index
from stock_data_engine.adapters.eastmoney.corporate_actions import fetch_corporate_actions_eastmoney
from stock_data_engine.adapters.eastmoney.earnings_disclosure import (
    fetch_earnings_disclosure_schedule,
)
from stock_data_engine.adapters.tdx_protocol.client import fetch_corporate_actions
from stock_data_engine.config import Config
from stock_data_engine.domain.schemas import with_provenance
from stock_data_engine.orchestrator.registry import register_step
from stock_data_engine.quality.failover import (
    snapshot_corporate_actions_backup,
    snapshot_corporate_actions_tdx_backup,
)
from stock_data_engine.steps.common import fetch_incremental_daily, load_symbols, write_simple
from stock_data_engine.steps.http_common import run_incremental_fetched, write_fetched

# TDX xdxr is per-symbol (backfill); EastMoney datacenter supports ex-date filter (daily).
_CANONICAL_BACKFILL = "tdx_protocol"
_CANONICAL_DAILY = "eastmoney"


@register_step("corporate_actions", group="core", depends_on=["instruments"])
def step_corporate_actions(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    rl = config.tdx_rate_limit_spec()
    backfill = getattr(config, "_backfill", False)

    if backfill:
        symbols = load_symbols(config)
        if config.failover_enabled:
            snapshot_corporate_actions_backup(
                config, trade_date=trade_date, run_id=run_id, backfill=True
            )
        df = fetch_corporate_actions(
            trade_date,
            symbols=symbols,
            backfill=True,
            rate_limit=rl,
            allow_mock=config.tdx_allow_mock,
            primary_only=True,
            config=config,
        )
        canonical_source = _CANONICAL_BACKFILL
    else:
        if not config.sources.get("eastmoney", True):
            raise RuntimeError("corporate_actions daily: eastmoney source disabled in config")
        df, _findings = fetch_incremental_daily(
            config,
            "corporate_actions",
            trade_date,
            lambda d: fetch_corporate_actions_eastmoney(d, backfill=False, config=config),
            allow_empty=True,
        )
        canonical_source = _CANONICAL_DAILY
        if config.failover_enabled and df.height:
            ex_today = df.filter(pl.col("ex_date") == trade_date)
            if ex_today.height:
                snapshot_corporate_actions_tdx_backup(
                    config,
                    trade_date=trade_date,
                    symbols=ex_today["symbol"].unique().to_list(),
                    run_id=run_id,
                    rate_limit=rl,
                )

    if df.is_empty():
        return {
            "rows_read": 0,
            "rows_written": 0,
            "context_updates": {"symbols_to_rebackfill": []},
        }

    df = with_provenance(df, source=canonical_source, data_version="v1")

    rebackfill: list[str] = []
    if df.height and "symbol" in df.columns and "ex_date" in df.columns:
        today = df.filter(pl.col("ex_date") == trade_date)
        if today.height:
            rebackfill = today["symbol"].unique().to_list()

    context_updates = {"symbols_to_rebackfill": rebackfill}
    result = write_simple(config, run_id, "corporate_actions", df)
    result["context_updates"] = context_updates
    return result


@register_step(
    "earnings_disclosure_schedule", group="fundamentals", depends_on=["instruments"]
)
def step_earnings_disclosure_schedule(
    config: Config, trade_date: date, run_id: str, context: dict
) -> dict:
    if not config.sources.get("eastmoney", True):
        raise RuntimeError("earnings_disclosure_schedule: eastmoney source disabled in config")
    # Period-keyed like financial_statement_items (watermark=False): daily runs
    # refresh the open disclosure windows; backfill walks every period 2016+.
    backfill = getattr(config, "_backfill", False)
    df = fetch_earnings_disclosure_schedule(trade_date, backfill=backfill, config=config)
    if df.is_empty():
        return {"rows_read": 0, "rows_written": 0}
    return write_fetched(config, run_id, "earnings_disclosure_schedule", df, source="eastmoney")


@register_step("announcement_index", group="capital", depends_on=["instruments"])
def step_announcement_index(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    if not config.sources.get("cninfo", True):
        raise RuntimeError("announcement_index: cninfo source disabled in config")
    return run_incremental_fetched(
        config,
        trade_date,
        run_id,
        "announcement_index",
        fetch_announcement_index,
        source="cninfo",
    )
