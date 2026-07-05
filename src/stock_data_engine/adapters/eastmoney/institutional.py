"""EastMoney institutional holdings (季报 batch, NOTICE_DATE incremental)."""

from __future__ import annotations

import logging
from datetime import date

import polars as pl

from stock_data_engine.adapters.eastmoney.common import exchange_from_datacenter, symbol_from_em
from stock_data_engine.adapters.eastmoney.datacenter import fetch_datacenter
from stock_data_engine.adapters.eastmoney.em_auth import EastMoneyClient

logger = logging.getLogger(__name__)

_HOLD_REPORT = "RPT_MAIN_ORGHOLD"
_HOLD_COLUMNS = (
    "SECURITY_CODE,REPORT_DATE,ORG_TYPE,ORG_NUM,HOLD_MARKET_CAP,HOLD_RATIO,NOTICE_DATE"
)

_HOLDER_TYPE_MAP = {
    "基金": "fund",
    "QFII": "qfii",
    "社保": "social_security",
    "保险": "insurance",
    "券商": "broker",
    "信托": "trust",
    "银行": "bank",
    "一般法人": "corporate",
}


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


def _normalize_holder_type(raw: str | None) -> str:
    text = str(raw or "").strip()
    for key, value in _HOLDER_TYPE_MAP.items():
        if key in text:
            return value
    slug = text.lower().replace(" ", "_") if text else "other"
    return slug or "other"


def fetch_institutional_holdings(
    trade_date: date,
    *,
    client: EastMoneyClient | None = None,
) -> pl.DataFrame:
    owns = client is None
    if client is None:
        client = EastMoneyClient()

    ds = trade_date.isoformat()
    raw = fetch_datacenter(
        client,
        _HOLD_REPORT,
        _HOLD_COLUMNS,
        filter_expr=f"(NOTICE_DATE='{ds}')",
        page_size=5000,
    )
    if owns:
        client.close()

    rows: list[dict] = []
    for item in raw:
        code = str(item.get("SECURITY_CODE", "")).zfill(6)
        exch = exchange_from_datacenter(item)
        sym = symbol_from_em(code, 1 if exch == "SH" else (2 if exch == "BJ" else 0))
        if not sym:
            continue
        period = _report_period(item.get("REPORT_DATE"))
        if not period:
            continue
        rows.append(
            {
                "symbol": sym,
                "holder_type": _normalize_holder_type(item.get("ORG_TYPE")),
                "report_period": period,
                "holding_shares": float(item.get("ORG_NUM") or 0),
                "holding_ratio": float(item.get("HOLD_RATIO") or 0),
                "holding_mv": float(item.get("HOLD_MARKET_CAP") or 0),
            }
        )

    if not rows:
        return pl.DataFrame()
    return pl.DataFrame(rows).unique(subset=["symbol", "holder_type", "report_period"], keep="last")
