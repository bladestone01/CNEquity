"""EastMoney share-unlock (限售解禁) schedule."""

from __future__ import annotations

import logging
from datetime import date, timedelta

import polars as pl

from stock_data_engine.adapters.eastmoney.common import exchange_from_datacenter, symbol_from_em
from stock_data_engine.adapters.eastmoney.datacenter import fetch_datacenter
from stock_data_engine.adapters.eastmoney.em_auth import EastMoneyClient

logger = logging.getLogger(__name__)

_UNLOCK_REPORT = "RPTA_WEB_XSJJMX"
_UNLOCK_COLUMNS = "SECURITY_CODE,FREE_DATE,FREE_SHARES,FREE_RATIO,FREE_TYPE,NOTICE_DATE"


def fetch_share_unlock_schedule(
    trade_date: date,
    *,
    horizon_days: int = 180,
    client: EastMoneyClient | None = None,
) -> pl.DataFrame:
    owns = client is None
    if client is None:
        client = EastMoneyClient()

    start = trade_date.isoformat()
    end = (trade_date + timedelta(days=horizon_days)).isoformat()
    raw = fetch_datacenter(
        client,
        _UNLOCK_REPORT,
        _UNLOCK_COLUMNS,
        filter_expr=f"(FREE_DATE>='{start}')(FREE_DATE<='{end}')",
    )
    if owns:
        client.close()

    rows: list[dict] = []
    for item in raw:
        code = str(item.get("SECURITY_CODE", "")).zfill(6)
        exch = exchange_from_datacenter(item)
        market_id = 1 if exch == "SH" else (2 if exch == "BJ" else 0)
        sym = symbol_from_em(code, market_id)
        if not sym:
            continue
        unlock_raw = item.get("FREE_DATE")
        if not unlock_raw:
            continue
        try:
            unlock_date = date.fromisoformat(str(unlock_raw)[:10])
        except ValueError:
            continue
        rows.append(
            {
                "symbol": sym,
                "unlock_date": unlock_date,
                "unlock_shares": float(item.get("FREE_SHARES") or 0),
                "unlock_ratio": float(item.get("FREE_RATIO") or 0),
                "unlock_type": str(item.get("FREE_TYPE") or ""),
            }
        )

    if not rows:
        return pl.DataFrame()
    return pl.DataFrame(rows).unique(subset=["symbol", "unlock_date"], keep="last")
