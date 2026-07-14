"""L7 rotation steps: hot rank, sector bars/flows, market news headlines."""

from __future__ import annotations

from datetime import date

from stock_data_engine.adapters.eastmoney.rotation import (
    fetch_hot_rank,
    fetch_news_headlines,
    fetch_sector_bars,
    fetch_sector_fund_flow,
)
from stock_data_engine.config import Config
from stock_data_engine.orchestrator.registry import register_step
from stock_data_engine.steps.http_common import run_incremental_fetched


def _run_rotation_step(
    config: Config,
    trade_date: date,
    run_id: str,
    dataset: str,
    fetch_fn,
    *,
    allow_empty: bool = True,
) -> dict:
    if not config.sources.get("eastmoney", True):
        raise RuntimeError(f"{dataset}: eastmoney source disabled in config")
    return run_incremental_fetched(
        config,
        trade_date,
        run_id,
        dataset,
        fetch_fn,
        source="eastmoney",
        allow_empty=allow_empty,
    )


@register_step("hot_rank", group="research", depends_on=["instruments"])
def step_hot_rank(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    return _run_rotation_step(config, trade_date, run_id, "hot_rank", fetch_hot_rank)


@register_step("sector_bars", group="research", depends_on=["instruments"])
def step_sector_bars(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    return _run_rotation_step(config, trade_date, run_id, "sector_bars", fetch_sector_bars)


@register_step("sector_fund_flow", group="research", depends_on=["instruments"])
def step_sector_fund_flow(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    return _run_rotation_step(
        config, trade_date, run_id, "sector_fund_flow", fetch_sector_fund_flow
    )


@register_step("news_headlines", group="research")
def step_news_headlines(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    return _run_rotation_step(
        config, trade_date, run_id, "news_headlines", fetch_news_headlines, allow_empty=True
    )
