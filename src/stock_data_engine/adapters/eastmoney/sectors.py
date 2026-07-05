"""EastMoney sector / concept board membership."""

from __future__ import annotations

from datetime import date

import polars as pl

from stock_data_engine.adapters.eastmoney.common import exchange_from_datacenter, symbol_from_em
from stock_data_engine.adapters.eastmoney.datacenter import fetch_datacenter
from stock_data_engine.adapters.eastmoney.em_auth import EastMoneyClient


def fetch_sector_members(as_of_date: date, *, client: EastMoneyClient | None = None) -> pl.DataFrame:
    owns = client is None
    if client is None:
        client = EastMoneyClient()
    raw = fetch_datacenter(
        client,
        "RPT_CONCEPT_BOARD_CONSTITUENT",
        "SECURITY_CODE,BOARD_CODE,BOARD_NAME",
        page_size=5000,
    )
    rows = []
    for item in raw:
        code = str(item.get("SECURITY_CODE", "")).zfill(6)
        exch = exchange_from_datacenter(item)
        sym = symbol_from_em(code, 1 if exch == "SH" else (2 if exch == "BJ" else 0))
        if not sym:
            continue
        rows.append(
            {
                "symbol": sym,
                "sector_code": str(item.get("BOARD_CODE") or ""),
                "sector_name": str(item.get("BOARD_NAME") or ""),
                "as_of_date": as_of_date,
            }
        )
    if owns:
        client.close()
    return pl.DataFrame(rows) if rows else pl.DataFrame()
