"""EastMoney financial statement items (batch, PIT via announce_date)."""

from __future__ import annotations

import logging
from datetime import date

import polars as pl

from stock_data_engine.adapters.eastmoney.common import symbol_from_secucode
from stock_data_engine.adapters.eastmoney.datacenter import fetch_datacenter
from stock_data_engine.adapters.eastmoney.em_auth import EastMoneyClient
from stock_data_engine.config import Config

logger = logging.getLogger(__name__)

_BACKFILL_START_YEAR = 2016
_QUARTER_END_MMDD = (("03", "31"), ("06", "30"), ("09", "30"), ("12", "31"))

# (statement_type, item_code) -> EastMoney datacenter column on RPT_LICO_FN_CPD.
# EastMoney's current financial quick-report endpoint exposes a compact set of
# reported items; request only live fields so fail-loud validation catches real
# API changes rather than our stale aliases.
_ITEM_FIELDS: dict[tuple[str, str], str] = {
    ("income", "revenue"): "TOTAL_OPERATE_INCOME",
    ("income", "net_profit"): "PARENT_NETPROFIT",
    ("indicator", "roe"): "WEIGHTAVG_ROE",
}

_COLUMNS = (
    "SECURITY_CODE,SECUCODE,REPORTDATE,NOTICE_DATE,"
    + ",".join(dict.fromkeys(_ITEM_FIELDS.values()))
)


def _report_period(raw: str | None) -> str | None:
    if not raw:
        return None
    text = str(raw)[:10]
    if len(text) < 7:
        return text
    year = text[:4]
    month = int(text[5:7])
    if month == 3:
        q = "Q1"
    elif month == 6:
        q = "Q2"
    elif month == 9:
        q = "Q3"
    else:
        q = "Q4"
    return f"{year}{q}"


def _report_period_dates(trade_date: date) -> list[str]:
    """Quarter-end report dates from 2016 through *trade_date* (descending)."""
    out: list[str] = []
    for year in range(_BACKFILL_START_YEAR, trade_date.year + 1):
        for mm, dd in _QUARTER_END_MMDD:
            ds = f"{year}-{mm}-{dd}"
            if date.fromisoformat(ds) <= trade_date:
                out.append(ds)
    return sorted(out, reverse=True)


def _parse_rows(raw: list[dict], *, default_notice: str) -> list[dict]:
    rows: list[dict] = []
    for item in raw:
        # SECUCODE (e.g. 600519.SH) filters to A-share and drops NEEQ (.NQ),
        # which dominate same-day announcements and would otherwise be empty.
        sym = symbol_from_secucode(item.get("SECUCODE"))
        if not sym:
            continue
        notice_raw = item.get("NOTICE_DATE") or default_notice
        announce_date = date.fromisoformat(str(notice_raw)[:10])
        report_period = _report_period(item.get("REPORTDATE"))
        if not report_period:
            continue
        for (statement_type, item_code), field in _ITEM_FIELDS.items():
            val = item.get(field)
            if val is None:
                continue
            try:
                item_value = float(val)
            except (TypeError, ValueError):
                continue
            rows.append(
                {
                    "symbol": sym,
                    "report_period": report_period,
                    "statement_type": statement_type,
                    "item_code": item_code,
                    "item_value": item_value,
                    "announce_date": announce_date,
                }
            )
    return rows


def fetch_financial_statement_items(
    trade_date: date,
    *,
    backfill: bool = False,
    client: EastMoneyClient | None = None,
    config: Config | None = None,
) -> pl.DataFrame:
    """Fetch financial statement items with PIT ``announce_date``.

    ``backfill=False`` (daily): rows whose ``NOTICE_DATE`` equals *trade_date*
    — catches newly announced reports. ``backfill=True``: every A-share report
    for each quarter-end period from 2016 through *trade_date*, keyed by
    ``REPORTDATE`` (the quarterly cadence the NOTICE_DATE path cannot reach).
    """
    owns = client is None
    if client is None:
        client = EastMoneyClient(config=config)

    ds = trade_date.isoformat()
    if backfill:
        filters = [f"(REPORTDATE='{p}')" for p in _report_period_dates(trade_date)]
    else:
        filters = [f"(NOTICE_DATE='{ds}')"]

    rows: list[dict] = []
    try:
        for filter_expr in filters:
            if config is not None:
                config.rate_limit("eastmoney")
            raw = fetch_datacenter(
                client,
                "RPT_LICO_FN_CPD",
                _COLUMNS,
                filter_expr=filter_expr,
                page_size=5000,
            )
            rows.extend(_parse_rows(raw, default_notice=ds))
    finally:
        if owns:
            client.close()

    if not rows:
        return pl.DataFrame()
    return pl.DataFrame(rows).unique(
        subset=["symbol", "report_period", "statement_type", "item_code"], keep="last"
    )
