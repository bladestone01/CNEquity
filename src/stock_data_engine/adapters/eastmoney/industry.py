"""EastMoney industry classification membership."""

from __future__ import annotations

from datetime import date

import polars as pl

from stock_data_engine.adapters.eastmoney.common import exchange_from_datacenter, symbol_from_em
from stock_data_engine.adapters.eastmoney.datacenter import fetch_datacenter
from stock_data_engine.adapters.eastmoney.em_auth import EastMoneyClient


def fetch_industry_members(
    as_of_date: date,
    *,
    client: EastMoneyClient | None = None,
) -> pl.DataFrame:
    owns = client is None
    if client is None:
        client = EastMoneyClient()

    raw = fetch_datacenter(
        client,
        "RPT_STOCK_INDUSTRY",
        "SECURITY_CODE,INDUSTRY_CODE,INDUSTRY_NAME,INDUSTRY_TYPE",
        page_size=5000,
    )
    rows: list[dict] = []
    for item in raw:
        code = str(item.get("SECURITY_CODE", "")).zfill(6)
        exch = exchange_from_datacenter(item)
        sym = symbol_from_em(code, 1 if exch == "SH" else (2 if exch == "BJ" else 0))
        if not sym:
            continue
        system = str(item.get("INDUSTRY_TYPE") or "eastmoney").lower()
        rows.append(
            {
                "symbol": sym,
                "classification_system": system,
                "industry_code": str(item.get("INDUSTRY_CODE") or ""),
                "industry_name": str(item.get("INDUSTRY_NAME") or ""),
                "as_of_date": as_of_date,
            }
        )

    if owns:
        client.close()
    if not rows:
        return pl.DataFrame()
    return pl.DataFrame(rows).unique(
        subset=["symbol", "classification_system", "as_of_date"], keep="last"
    )
