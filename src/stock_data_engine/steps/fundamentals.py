"""L3 fundamentals steps: valuation metrics, financial statement items."""

from __future__ import annotations

from datetime import date

from stock_data_engine.adapters.eastmoney.fundamentals import fetch_financial_statement_items
from stock_data_engine.adapters.eastmoney.valuation import fetch_valuation_metrics
from stock_data_engine.config import Config
from stock_data_engine.domain.symbols import is_all_a_symbol, parse_symbol
from stock_data_engine.orchestrator.registry import register_step
from stock_data_engine.steps.common import load_symbols
from stock_data_engine.steps.http_common import run_incremental_fetched, write_fetched

# EastMoney's valuation clist is a live snapshot only; history comes from baostock.
_VALUATION_BACKFILL_START = date(2016, 1, 1)


@register_step("valuation_metrics", group="capital", depends_on=["instruments"])
def step_valuation_metrics(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    if getattr(config, "_backfill", False):
        return _backfill_valuation_metrics(config, trade_date, run_id)
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


def _backfill_valuation_metrics(config: Config, trade_date: date, run_id: str) -> dict:
    """Historical PE/PB/PS from baostock over the all_a universe (2016 → today)."""
    from stock_data_engine.adapters.baostock.valuation import fetch_valuation_history

    symbols = [s for s in load_symbols(config) if _is_all_a(s)]
    df = fetch_valuation_history(symbols, _VALUATION_BACKFILL_START, trade_date)
    if df.is_empty():
        return {"rows_read": 0, "rows_written": 0}
    return write_fetched(config, run_id, "valuation_metrics", df, source="baostock")


def _is_all_a(symbol: str) -> bool:
    try:
        info = parse_symbol(symbol)
    except ValueError:
        return False
    return is_all_a_symbol(info.code, info.exchange)


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
