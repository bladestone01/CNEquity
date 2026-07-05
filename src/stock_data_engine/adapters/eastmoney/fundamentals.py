"""EastMoney financial statement items (batch, PIT via announce_date)."""

from __future__ import annotations

import logging
from datetime import date

import polars as pl

from stock_data_engine.adapters.eastmoney.common import exchange_from_datacenter, symbol_from_em
from stock_data_engine.adapters.eastmoney.datacenter import fetch_datacenter
from stock_data_engine.adapters.eastmoney.em_auth import EastMoneyClient

logger = logging.getLogger(__name__)

# (statement_type, item_code) -> EastMoney datacenter column on RPT_LICO_FN_CPD
_ITEM_FIELDS: dict[tuple[str, str], str] = {
    ("income", "revenue"): "TOTALOPERATEREVE",
    ("income", "net_profit"): "PARENTNETPROFIT",
    ("income", "deducted_net_profit"): "KCFJCXSYJLR",
    ("balance", "total_assets"): "TOTALASSETS",
    ("balance", "total_liabilities"): "TOTALLIAB",
    ("balance", "net_assets"): "TOTALSHEQUITY",
    ("cashflow", "operating_cashflow"): "NETOPERATECASHFLOW",
    ("indicator", "roe"): "ROEJQ",
    ("indicator", "debt_ratio"): "ZCFZL",
}

_COLUMNS = (
    "SECURITY_CODE,REPORTDATE,NOTICE_DATE,"
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


def fetch_financial_statement_items(
    trade_date: date,
    *,
    client: EastMoneyClient | None = None,
) -> pl.DataFrame:
    """Fetch financial items whose ``NOTICE_DATE`` equals *trade_date*."""
    owns = client is None
    if client is None:
        client = EastMoneyClient()

    ds = trade_date.isoformat()
    raw = fetch_datacenter(
        client,
        "RPT_LICO_FN_CPD",
        _COLUMNS,
        filter_expr=f"(NOTICE_DATE='{ds}')",
        page_size=5000,
    )

    rows: list[dict] = []
    for item in raw:
        code = str(item.get("SECURITY_CODE", "")).zfill(6)
        exch = exchange_from_datacenter(item)
        sym = symbol_from_em(code, 1 if exch == "SH" else (2 if exch == "BJ" else 0))
        if not sym:
            continue
        notice_raw = item.get("NOTICE_DATE") or ds
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

    if owns:
        client.close()
    if not rows:
        return pl.DataFrame()
    return pl.DataFrame(rows).unique(
        subset=["symbol", "report_period", "statement_type", "item_code"], keep="last"
    )
