"""L5 structure steps: sector members, index constituents, industry members."""

from __future__ import annotations

from datetime import date

from stock_data_engine.adapters.eastmoney.index_constituents import fetch_index_constituents
from stock_data_engine.adapters.eastmoney.industry import fetch_industry_members
from stock_data_engine.adapters.eastmoney.sectors import fetch_sector_members
from stock_data_engine.config import Config
from stock_data_engine.orchestrator.registry import register_step
from stock_data_engine.steps.http_common import run_incremental_fetched


@register_step("sector_members", group="capital", depends_on=["instruments"])
def step_sector_members(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    if not config.sources.get("eastmoney", True):
        raise RuntimeError("sector_members: eastmoney source disabled in config")
    return run_incremental_fetched(
        config,
        trade_date,
        run_id,
        "sector_members",
        fetch_sector_members,
        source="eastmoney",
    )


@register_step("index_constituents", group="fundamentals", depends_on=["instruments"])
def step_index_constituents(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    if not config.sources.get("eastmoney", True):
        raise RuntimeError("index_constituents: eastmoney source disabled in config")
    return run_incremental_fetched(
        config,
        trade_date,
        run_id,
        "index_constituents",
        fetch_index_constituents,
        source="eastmoney",
    )


@register_step("industry_members", group="fundamentals", depends_on=["instruments"])
def step_industry_members(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    if not config.sources.get("eastmoney", True):
        raise RuntimeError("industry_members: eastmoney source disabled in config")
    return run_incremental_fetched(
        config,
        trade_date,
        run_id,
        "industry_members",
        fetch_industry_members,
        source="eastmoney",
    )
