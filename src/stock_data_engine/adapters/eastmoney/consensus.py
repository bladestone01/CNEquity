"""EastMoney analyst consensus / earnings forecast (daily incremental)."""

from __future__ import annotations

import logging
from datetime import date

import polars as pl

from stock_data_engine.adapters.eastmoney.common import exchange_from_datacenter, symbol_from_em
from stock_data_engine.adapters.eastmoney.datacenter import fetch_datacenter
from stock_data_engine.adapters.eastmoney.em_auth import EastMoneyClient

logger = logging.getLogger(__name__)

_CONSENSUS_REPORT = "RPTA_WEB_RES_PROFIT"
_CONSENSUS_COLUMNS = (
    "SECURITY_CODE,PUBLISH_DATE,FORECAST_YEAR,FORECAST_EPS,FORECAST_PE,"
    "RATING,ORG_NUM,TARGET_PRICE"
)


def _parse_date(value: object, fallback: date) -> date:
    if value is None:
        return fallback
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return fallback


def fetch_analyst_consensus(
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
        _CONSENSUS_REPORT,
        _CONSENSUS_COLUMNS,
        filter_expr=f"(PUBLISH_DATE='{ds}')",
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
        forecast_date = _parse_date(item.get("PUBLISH_DATE"), trade_date)
        forecast_year = item.get("FORECAST_YEAR")
        rows.append(
            {
                "symbol": sym,
                "forecast_date": forecast_date,
                "forecast_year": int(forecast_year) if forecast_year is not None else None,
                "eps_forecast": float(item.get("FORECAST_EPS") or 0),
                "pe_forecast": float(item.get("FORECAST_PE") or 0),
                "target_price": float(item.get("TARGET_PRICE") or 0),
                "rating": str(item.get("RATING") or ""),
                "analyst_count": int(item.get("ORG_NUM") or 0),
            }
        )

    if not rows:
        return pl.DataFrame()
    return pl.DataFrame(rows).unique(subset=["symbol", "forecast_date"], keep="last")
