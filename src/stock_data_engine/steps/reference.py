"""L0 reference steps: instruments, trading_calendar, trading_status."""

from __future__ import annotations

from datetime import date, timedelta

from stock_data_engine.adapters.tdx_protocol.client import (
    fetch_instruments,
    fetch_trading_calendar,
    fetch_trading_status,
    normalize_with_source,
)
from stock_data_engine.config import Config
from stock_data_engine.orchestrator.registry import register_step
from stock_data_engine.steps.common import load_symbols, write_simple


@register_step("instruments", group="core", requires_workers=False)
def step_instruments(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    rl = config.tdx_rate_limit_spec()
    df = fetch_instruments(rate_limit=rl, allow_mock=config.tdx_allow_mock)
    df = normalize_with_source(df)
    return write_simple(config, run_id, "instruments", df)


@register_step("trading_calendar", group="core")
def step_trading_calendar(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    start = trade_date - timedelta(days=30)
    end = trade_date + timedelta(days=365)
    rl = config.tdx_rate_limit_spec()
    df = fetch_trading_calendar(start, end, rate_limit=rl, allow_mock=config.tdx_allow_mock)
    df = normalize_with_source(df)
    return write_simple(config, run_id, "trading_calendar", df)


@register_step("trading_status", group="core")
def step_trading_status(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    symbols = context.get("symbols") or load_symbols(config)
    rl = config.tdx_rate_limit_spec()
    df = fetch_trading_status(
        symbols[:500], trade_date, rate_limit=rl, allow_mock=config.tdx_allow_mock
    )
    df = normalize_with_source(df)
    return write_simple(config, run_id, "trading_status", df)
