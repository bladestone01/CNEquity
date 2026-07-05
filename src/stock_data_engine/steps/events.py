"""L2 corporate-event steps: corporate_actions."""

from __future__ import annotations

from datetime import date

from stock_data_engine.adapters.tdx_protocol.client import (
    fetch_corporate_actions,
    normalize_with_source,
)
from stock_data_engine.config import Config
from stock_data_engine.orchestrator.registry import register_step
from stock_data_engine.steps.common import write_simple


@register_step("corporate_actions", group="core", depends_on=["instruments"])
def step_corporate_actions(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    rl = config.tdx_rate_limit_spec()
    df = fetch_corporate_actions(trade_date, rate_limit=rl, allow_mock=config.tdx_allow_mock)
    df = normalize_with_source(df)
    rebackfill = []
    if df.height and "symbol" in df.columns:
        rebackfill = df["symbol"].unique().to_list()
    context_updates = {"symbols_to_rebackfill": rebackfill}
    result = write_simple(config, run_id, "corporate_actions", df)
    result["context_updates"] = context_updates
    return result
