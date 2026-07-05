"""L6 macro + L8 risk batch steps (v1.1)."""

from __future__ import annotations

from datetime import date

from stock_data_engine.adapters.cninfo.regulatory import fetch_regulatory_events
from stock_data_engine.adapters.eastmoney.share_unlock import fetch_share_unlock_schedule
from stock_data_engine.adapters.macro.indicators import fetch_macro_indicators
from stock_data_engine.config import Config
from stock_data_engine.derive.market_breadth import compute_market_breadth
from stock_data_engine.orchestrator.registry import register_step
from stock_data_engine.steps.http_common import empty_ok, write_fetched


@register_step("macro_indicators", group="macro_risk")
def step_macro_indicators(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    df = fetch_macro_indicators(trade_date)
    if df.is_empty():
        return {"rows_read": 0, "rows_written": 0}
    return write_fetched(config, run_id, "macro_indicators", df, source="eastmoney")


@register_step("market_breadth", group="macro_risk", depends_on=["daily_bars"])
def step_market_breadth(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    df = compute_market_breadth(config, trade_date)
    empty_ok(df, "market_breadth", trade_date)
    return write_fetched(config, run_id, "market_breadth", df, source="derived")


@register_step("share_unlock_schedule", group="macro_risk", depends_on=["instruments"])
def step_share_unlock_schedule(
    config: Config, trade_date: date, run_id: str, context: dict
) -> dict:
    if not config.sources.get("eastmoney", True):
        raise RuntimeError("share_unlock_schedule: eastmoney source disabled in config")
    df = fetch_share_unlock_schedule(trade_date)
    empty_ok(df, "share_unlock_schedule", trade_date)
    return write_fetched(config, run_id, "share_unlock_schedule", df, source="eastmoney")


@register_step("regulatory_events", group="macro_risk", depends_on=["instruments"])
def step_regulatory_events(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    if not config.sources.get("cninfo", True):
        raise RuntimeError("regulatory_events: cninfo source disabled in config")
    df = fetch_regulatory_events(trade_date)
    if df.is_empty():
        return {"rows_read": 0, "rows_written": 0}
    return write_fetched(config, run_id, "regulatory_events", df, source="cninfo")
