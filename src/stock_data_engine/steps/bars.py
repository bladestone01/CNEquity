"""L1 bar steps: daily_bars, index_bars."""

from __future__ import annotations

from datetime import date

from stock_data_engine.adapters.tdx_protocol.client import (
    fetch_index_bars,
    normalize_with_source,
)
from stock_data_engine.config import Config
from stock_data_engine.orchestrator.registry import register_step
from stock_data_engine.orchestrator.worker_pool import fetch_daily_bars_parallel
from stock_data_engine.steps.common import BACKFILL_START, incremental_window, load_symbols


@register_step(
    "daily_bars",
    group="core",
    depends_on=["instruments", "corporate_actions"],
    requires_workers=True,
)
def step_daily_bars(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    symbols = load_symbols(config)
    rebackfill = context.get("symbols_to_rebackfill") or []
    if rebackfill:
        symbols = list(dict.fromkeys(rebackfill + symbols))

    if getattr(config, "_backfill", False):
        start = BACKFILL_START
    else:
        start = incremental_window(config, "daily_bars", trade_date)

    end = trade_date
    batch_specs = context.get("_retry_batch_specs")
    result = fetch_daily_bars_parallel(
        config,
        symbols,
        start,
        end,
        run_id,
        "daily_bars",
        batch_specs=batch_specs,
    )
    return result


@register_step("index_bars", group="core", depends_on=["instruments"])
def step_index_bars(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    if getattr(config, "_backfill", False):
        start = BACKFILL_START
    else:
        start = incremental_window(config, "index_bars", trade_date)
    rl = config.tdx_rate_limit_spec()
    df = fetch_index_bars(
        start,
        trade_date,
        rate_limit=rl,
        allow_mock=config.tdx_allow_mock,
        backfill=getattr(config, "_backfill", False),
    )
    df = normalize_with_source(df)
    from stock_data_engine.steps.common import write_simple

    return write_simple(config, run_id, "index_bars", df)
