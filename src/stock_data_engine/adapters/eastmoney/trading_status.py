"""EastMoney ST / suspension status for trading_status dataset."""

from __future__ import annotations

import logging
from datetime import date

import polars as pl

from stock_data_engine.adapters.eastmoney.em_auth import EastMoneyClient
from stock_data_engine.domain.symbols import format_symbol, is_all_a_symbol

logger = logging.getLogger(__name__)

_ST_FS = "m:0+t:5,m:0+t:6,m:0+t:7,m:0+t:80,m:1+t:2,m:1+t:23"
_SUSPEND_REPORT = "RPT_CUSTOM_SUSPEND_DATA_INTERFACE"
_CLIST = "https://push2.eastmoney.com/api/qt/clist/get"
_DATACENTER = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def _exchange_from_code(code: str) -> str:
    if code.startswith(("60", "68")):
        return "SH"
    if code.startswith("92"):
        return "BJ"
    return "SZ"


def _fetch_st_symbols(client: EastMoneyClient) -> set[str]:
    symbols: set[str] = set()
    page = 1
    while True:
        url = (
            f"{_CLIST}?pn={page}&pz=5000&po=1&np=1&fltt=2&invt=2"
            f"&fid=f3&fs={_ST_FS}&fields=f12,f13,f14"
        )
        try:
            resp = client.get(url)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            logger.warning("EastMoney ST list failed (page %s): %s", page, exc)
            break

        diff = (payload.get("data") or {}).get("diff") or []
        if not diff:
            break
        for item in diff:
            code = str(item.get("f12", "")).zfill(6)
            market = int(item.get("f13", 0))
            exch = "SH" if market == 1 else ("BJ" if market == 2 else "SZ")
            if is_all_a_symbol(code, exch):
                symbols.add(format_symbol(code, exch))
        total = int((payload.get("data") or {}).get("total") or 0)
        if page * 5000 >= total:
            break
        page += 1
    return symbols


def _fetch_suspended_symbols(client: EastMoneyClient, trade_date: date) -> set[str]:
    symbols: set[str] = set()
    ds = trade_date.strftime("%Y-%m-%d")
    url = (
        f"{_DATACENTER}?reportName={_SUSPEND_REPORT}"
        f"&columns=SECURITY_CODE,TRADE_MARKET,STOP_DATE,RESUME_DATE"
        f"&pageSize=5000&pageNumber=1"
        f"&filter=(STOP_DATE<='{ds}')(RESUME_DATE>='{ds}'~RESUME_DATE='null')"
    )
    try:
        resp = client.get(url)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        logger.debug("EastMoney suspend list unavailable: %s", exc)
        return symbols

    for item in payload.get("result", {}).get("data") or []:
        code = str(item.get("SECURITY_CODE", "")).zfill(6)
        exch = _exchange_from_code(code)
        if is_all_a_symbol(code, exch):
            symbols.add(format_symbol(code, exch))
    return symbols


def fetch_trading_status_eastmoney(
    symbols: list[str],
    trade_date: date,
    *,
    client: EastMoneyClient | None = None,
) -> pl.DataFrame:
    owns = client is None
    if client is None:
        client = EastMoneyClient(min_interval=0.3)

    st_set = _fetch_st_symbols(client)
    suspended = _fetch_suspended_symbols(client, trade_date)

    rows = []
    for sym in symbols:
        if sym in suspended:
            rows.append(
                {
                    "symbol": sym,
                    "trade_date": trade_date,
                    "is_trading": False,
                    "status": "suspended",
                }
            )
        elif sym in st_set:
            rows.append(
                {
                    "symbol": sym,
                    "trade_date": trade_date,
                    "is_trading": True,
                    "status": "st",
                }
            )
        else:
            rows.append(
                {
                    "symbol": sym,
                    "trade_date": trade_date,
                    "is_trading": True,
                    "status": "normal",
                }
            )

    if owns:
        client.close()
    return pl.DataFrame(rows)
