"""L4 capital steps: fund flow, northbound, margin, dragon tiger, block trades."""

from __future__ import annotations

from datetime import date

from stock_data_engine.adapters.eastmoney.capital import (
    fetch_block_trades,
    fetch_dragon_tiger,
    fetch_fund_flow,
    fetch_margin_trading,
    fetch_northbound_flows,
    fetch_northbound_holdings,
)
from stock_data_engine.config import Config
from stock_data_engine.orchestrator.registry import register_step
from stock_data_engine.steps.http_common import empty_ok, write_fetched


def _run_capital_step(
    config: Config,
    trade_date: date,
    run_id: str,
    dataset: str,
    fetch_fn,
) -> dict:
    if not config.sources.get("eastmoney", True):
        raise RuntimeError(f"{dataset}: eastmoney source disabled in config")
    df = fetch_fn(trade_date)
    empty_ok(df, dataset, trade_date)
    return write_fetched(config, run_id, dataset, df, source="eastmoney")


@register_step("fund_flow", group="capital", depends_on=["instruments"])
def step_fund_flow(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    return _run_capital_step(config, trade_date, run_id, "fund_flow", fetch_fund_flow)


@register_step("northbound_holdings", group="capital", depends_on=["instruments"])
def step_northbound_holdings(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    return _run_capital_step(
        config, trade_date, run_id, "northbound_holdings", fetch_northbound_holdings
    )


@register_step("northbound_flows", group="capital")
def step_northbound_flows(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    return _run_capital_step(
        config, trade_date, run_id, "northbound_flows", fetch_northbound_flows
    )


@register_step("margin_trading", group="capital", depends_on=["instruments"])
def step_margin_trading(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    return _run_capital_step(config, trade_date, run_id, "margin_trading", fetch_margin_trading)


@register_step("dragon_tiger", group="signals", depends_on=["instruments"])
def step_dragon_tiger(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    return _run_capital_step(config, trade_date, run_id, "dragon_tiger", fetch_dragon_tiger)


@register_step("block_trades", group="signals", depends_on=["instruments"])
def step_block_trades(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    return _run_capital_step(config, trade_date, run_id, "block_trades", fetch_block_trades)
