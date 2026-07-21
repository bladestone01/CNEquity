"""L1 bar steps: daily_bars, index_bars."""

from __future__ import annotations

from datetime import date

from ashare_lake.adapters.tdx_protocol.client import (
    fetch_index_bars,
    normalize_with_source,
)
from ashare_lake.config import Config
from ashare_lake.orchestrator.registry import register_step
from ashare_lake.orchestrator.worker_pool import fetch_daily_bars_parallel
from ashare_lake.steps.common import BACKFILL_START, incremental_window, load_symbols


def _backfill_window(config: Config, trade_date: date) -> tuple[date, date]:
    """``--start/--end`` window for a backfill, defaulting to the full history.

    Repairing a single bad session must not mean re-fetching a decade for every
    symbol. A capture that fires before the close writes a truncated bar — right
    open, wrong close, partial volume — and the repair is one day wide.
    """
    end = getattr(config, "_backfill_end", None) or trade_date
    start = getattr(config, "_backfill_start", None) or BACKFILL_START
    return start, end


@register_step(
    "daily_bars",
    group="core",
    depends_on=["instruments", "corporate_actions"],
    requires_workers=True,
)
def step_daily_bars(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    batch_specs = context.get("_retry_batch_specs")
    if batch_specs:
        return fetch_daily_bars_parallel(
            config,
            [],
            trade_date,
            trade_date,
            run_id,
            "daily_bars",
            batch_specs=batch_specs,
        )

    symbols = load_symbols(config)
    rebackfill = context.get("symbols_to_rebackfill") or []
    if rebackfill:
        symbols = list(dict.fromkeys(rebackfill + symbols))

    if getattr(config, "_backfill", False):
        start, end = _backfill_window(config, trade_date)
    else:
        start = incremental_window(config, "daily_bars", trade_date)
        end = trade_date

    result = fetch_daily_bars_parallel(
        config,
        symbols,
        start,
        end,
        run_id,
        "daily_bars",
    )
    return result


@register_step("index_bars", group="core", depends_on=["instruments"])
def step_index_bars(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    if getattr(config, "_backfill", False):
        start, end = _backfill_window(config, trade_date)
    else:
        start = incremental_window(config, "index_bars", trade_date)
        end = trade_date
    rl = config.tdx_rate_limit_spec()
    df = fetch_index_bars(
        start,
        end,
        rate_limit=rl,
        allow_mock=config.tdx_allow_mock,
        backfill=getattr(config, "_backfill", False),
        config=config,
    )
    df = normalize_with_source(df)
    from ashare_lake.steps.common import write_simple

    return write_simple(config, run_id, "index_bars", df)
