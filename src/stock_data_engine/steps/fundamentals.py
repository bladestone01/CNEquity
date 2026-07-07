"""L3 fundamentals steps: valuation metrics, financial statement items."""

from __future__ import annotations

from datetime import date

from stock_data_engine.adapters.eastmoney.fundamentals import fetch_financial_statement_items
from stock_data_engine.adapters.eastmoney.valuation import fetch_valuation_metrics
from stock_data_engine.config import Config
from stock_data_engine.orchestrator.registry import register_step
from stock_data_engine.steps.http_common import run_incremental_fetched, write_fetched


@register_step("valuation_metrics", group="capital", depends_on=["instruments"])
def step_valuation_metrics(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    if not config.sources.get("eastmoney", True):
        raise RuntimeError("valuation_metrics: eastmoney source disabled in config")
    return run_incremental_fetched(
        config,
        trade_date,
        run_id,
        "valuation_metrics",
        fetch_valuation_metrics,
        source="eastmoney",
        allow_empty=True,
    )


@register_step("financial_statement_items", group="fundamentals", depends_on=["instruments"])
def step_financial_statement_items(
    config: Config, trade_date: date, run_id: str, context: dict
) -> dict:
    if not config.sources.get("eastmoney", True):
        raise RuntimeError("financial_statement_items: eastmoney source disabled in config")
    # Quarterly data: daily runs pick up same-day announcements; backfill walks
    # every report period 2016+ (NOTICE_DATE incremental cannot reach history).
    backfill = getattr(config, "_backfill", False)
    df = fetch_financial_statement_items(trade_date, backfill=backfill, config=config)
    if df.is_empty():
        return {"rows_read": 0, "rows_written": 0}
    return write_fetched(config, run_id, "financial_statement_items", df, source="eastmoney")
