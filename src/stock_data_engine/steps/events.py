"""L2 corporate-event steps: corporate_actions."""

from __future__ import annotations

from datetime import date

import polars as pl

from stock_data_engine.adapters.tdx_protocol.client import fetch_corporate_actions
from stock_data_engine.config import Config
from stock_data_engine.domain.schemas import with_provenance
from stock_data_engine.orchestrator.registry import register_step
from stock_data_engine.steps.common import load_symbols, write_simple


@register_step("corporate_actions", group="core", depends_on=["instruments"])
def step_corporate_actions(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    rl = config.tdx_rate_limit_spec()
    backfill = getattr(config, "_backfill", False)
    symbols = load_symbols(config) if backfill else None
    df = fetch_corporate_actions(
        trade_date,
        symbols=symbols,
        backfill=backfill,
        rate_limit=rl,
        allow_mock=config.tdx_allow_mock,
    )
    if "source" not in df.columns:
        df = with_provenance(df, source="tdx_protocol", data_version="v1")
    else:
        df = with_provenance(df, source="tdx_protocol", data_version="v1")

    rebackfill: list[str] = []
    if df.height and "symbol" in df.columns and "ex_date" in df.columns:
        today = df.filter(pl.col("ex_date") == trade_date)
        if today.height:
            rebackfill = today["symbol"].unique().to_list()

    context_updates = {"symbols_to_rebackfill": rebackfill}
    result = write_simple(config, run_id, "corporate_actions", df)
    result["context_updates"] = context_updates
    return result
