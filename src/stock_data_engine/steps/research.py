"""L3/L4/L7 research steps: institutional holdings, analyst consensus, sentiment."""

from __future__ import annotations

from datetime import date

from stock_data_engine.adapters.eastmoney.consensus import fetch_analyst_consensus
from stock_data_engine.adapters.eastmoney.institutional import fetch_institutional_holdings
from stock_data_engine.config import Config
from stock_data_engine.derive.sentiment_scores import compute_sentiment_scores
from stock_data_engine.orchestrator.registry import register_step
from stock_data_engine.steps.http_common import write_fetched


@register_step("institutional_holdings", group="research", depends_on=["instruments"])
def step_institutional_holdings(
    config: Config, trade_date: date, run_id: str, context: dict
) -> dict:
    if not config.sources.get("eastmoney", True):
        raise RuntimeError("institutional_holdings: eastmoney source disabled in config")
    df = fetch_institutional_holdings(trade_date)
    if df.is_empty():
        return {"rows_read": 0, "rows_written": 0}
    return write_fetched(config, run_id, "institutional_holdings", df, source="eastmoney")


@register_step("analyst_consensus", group="research", depends_on=["instruments"])
def step_analyst_consensus(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    if not config.sources.get("eastmoney", True):
        raise RuntimeError("analyst_consensus: eastmoney source disabled in config")
    df = fetch_analyst_consensus(trade_date)
    if df.is_empty():
        return {"rows_read": 0, "rows_written": 0}
    return write_fetched(config, run_id, "analyst_consensus", df, source="eastmoney")


@register_step("sentiment_scores", group="research", depends_on=["announcement_index"])
def step_sentiment_scores(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    df = compute_sentiment_scores(config, trade_date)
    if df.is_empty():
        return {"rows_read": 0, "rows_written": 0}
    return write_fetched(config, run_id, "sentiment_scores", df, source="derived")
