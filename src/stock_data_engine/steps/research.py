"""L3/L4/L7 research steps: institutional holdings, analyst consensus, sentiment."""

from __future__ import annotations

from datetime import date

from stock_data_engine.adapters.eastmoney.consensus import fetch_analyst_consensus
from stock_data_engine.adapters.eastmoney.institutional import fetch_institutional_holdings
from stock_data_engine.config import Config
from stock_data_engine.derive.sentiment_scores import compute_sentiment_scores
from stock_data_engine.orchestrator.registry import register_step
from stock_data_engine.steps.http_common import run_incremental_fetched


@register_step("institutional_holdings", group="research", depends_on=["instruments"])
def step_institutional_holdings(
    config: Config, trade_date: date, run_id: str, context: dict
) -> dict:
    if not config.sources.get("eastmoney", True):
        raise RuntimeError("institutional_holdings: eastmoney source disabled in config")
    return run_incremental_fetched(
        config,
        trade_date,
        run_id,
        "institutional_holdings",
        fetch_institutional_holdings,
        source="eastmoney",
        allow_empty=True,
    )


@register_step("analyst_consensus", group="research", depends_on=["instruments"])
def step_analyst_consensus(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    if not config.sources.get("eastmoney", True):
        raise RuntimeError("analyst_consensus: eastmoney source disabled in config")
    return run_incremental_fetched(
        config,
        trade_date,
        run_id,
        "analyst_consensus",
        fetch_analyst_consensus,
        source="eastmoney",
        allow_empty=True,
    )


@register_step("sentiment_scores", group="research", depends_on=["announcement_index"])
def step_sentiment_scores(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    return run_incremental_fetched(
        config,
        trade_date,
        run_id,
        "sentiment_scores",
        lambda d: compute_sentiment_scores(config, d),
        source="derived",
        allow_empty=True,
    )
