"""Curated dataset registry: partition keys and audit thresholds."""

from __future__ import annotations

# partition column per curated dataset; None = merge-style (e.g. instruments).
PARTITION_COLS: dict[str, str | None] = {
    "instruments": None,
    "trading_calendar": "trade_date",
    "trading_status": "trade_date",
    "daily_bars": "trade_date",
    "index_bars": "trade_date",
    "corporate_actions": "ex_date",
    "fund_flow": "trade_date",
    "margin_trading": "trade_date",
    "northbound_holdings": "trade_date",
    "northbound_flows": "trade_date",
    "valuation_metrics": "trade_date",
    "sector_members": "as_of_date",
    "announcement_index": "announce_date",
    "dragon_tiger": "trade_date",
    "block_trades": "trade_date",
    "financial_statement_items": "report_period",
    "index_constituents": "as_of_date",
    "industry_members": "as_of_date",
    "macro_indicators": "obs_date",
    "market_breadth": "trade_date",
    "share_unlock_schedule": "unlock_date",
    "regulatory_events": "event_date",
    "institutional_holdings": "report_period",
    "analyst_consensus": "forecast_date",
    "sentiment_scores": "trade_date",
}

# Datasets partitioned by non-date keys — skip date-based watermarks.
WATERMARK_SKIP = frozenset({"financial_statement_items", "institutional_holdings"})

# Warn when a partition's row/symbol count falls below this fraction of the prior partition.
ROW_COUNT_MUTATION_MIN_RATIO = 0.5

# Ignore mutation checks when the baseline partition is smaller than this.
ROW_COUNT_MUTATION_MIN_BASELINE_ROWS = 50
