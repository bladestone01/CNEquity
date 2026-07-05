"""EastMoney L4 capital datasets: fund flow, margin, northbound, dragon tiger, block trades."""

from __future__ import annotations

import logging
from datetime import date

import polars as pl

from stock_data_engine.adapters.eastmoney.clist import clist_rows_to_symbols, fetch_clist_pages
from stock_data_engine.adapters.eastmoney.common import exchange_from_datacenter, symbol_from_em
from stock_data_engine.adapters.eastmoney.datacenter import fetch_datacenter
from stock_data_engine.adapters.eastmoney.em_auth import EastMoneyClient
from stock_data_engine.domain.symbols import format_symbol

logger = logging.getLogger(__name__)

_FUND_FLOW_FIELDS = "f12,f13,f62,f66,f72,f78,f84"
_MARGIN_COLUMNS = "SECURITY_CODE,TRADE_DATE,MARGIN_BALANCE,MARGIN_BUY,SHORT_BALANCE,SHORT_SELL_VOLUME"
_NORTH_HOLD_COLUMNS = "SECURITY_CODE,TRADE_DATE,MUTUAL_TYPE,HOLD_SHARES,HOLD_MARKETCAP,HOLD_RATIO"
_NORTH_FLOW_COLUMNS = "TRADE_DATE,MUTUAL_TYPE,NET_BUY_AMT,BUY_AMT,SELL_AMT"
_DRAGON_COLUMNS = "SECURITY_CODE,TRADE_DATE,EXPLANATION,BUY_AMT,SELL_AMT,NET_AMT"
_BLOCK_COLUMNS = "SECURITY_CODE,TRADE_DATE,DEAL_PRICE,DEAL_VOLUME,DEAL_AMT,PREMIUM_RATIO"


def _channel(mutual_type: str | int | None) -> str:
    text = str(mutual_type or "").upper()
    if "SH" in text or text in {"1", "沪"}:
        return "SH"
    return "SZ"


def fetch_fund_flow(trade_date: date, *, client: EastMoneyClient | None = None) -> pl.DataFrame:
    owns = client is None
    if client is None:
        client = EastMoneyClient()
    rows_raw = fetch_clist_pages(client, fields=_FUND_FLOW_FIELDS)
    rows = []
    for sym, item in clist_rows_to_symbols(rows_raw):
        rows.append(
            {
                "symbol": sym,
                "trade_date": trade_date,
                "main_net_inflow": float(item.get("f62") or 0),
                "super_large_net_inflow": float(item.get("f66") or 0),
                "large_net_inflow": float(item.get("f72") or 0),
                "medium_net_inflow": float(item.get("f78") or 0),
                "small_net_inflow": float(item.get("f84") or 0),
            }
        )
    if owns:
        client.close()
    return pl.DataFrame(rows) if rows else pl.DataFrame()


def fetch_margin_trading(trade_date: date, *, client: EastMoneyClient | None = None) -> pl.DataFrame:
    owns = client is None
    if client is None:
        client = EastMoneyClient()
    ds = trade_date.isoformat()
    raw = fetch_datacenter(
        client,
        "RPTA_WEB_RZRQ_GGMX",
        _MARGIN_COLUMNS,
        filter_expr=f"(TRADE_DATE='{ds}')",
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
                "trade_date": trade_date,
                "margin_balance": float(item.get("MARGIN_BALANCE") or 0),
                "margin_buy": float(item.get("MARGIN_BUY") or 0),
                "short_balance": float(item.get("SHORT_BALANCE") or 0),
                "short_sell_volume": float(item.get("SHORT_SELL_VOLUME") or 0),
            }
        )
    if owns:
        client.close()
    return pl.DataFrame(rows) if rows else pl.DataFrame()


def fetch_northbound_holdings(trade_date: date, *, client: EastMoneyClient | None = None) -> pl.DataFrame:
    owns = client is None
    if client is None:
        client = EastMoneyClient()
    ds = trade_date.isoformat()
    raw = fetch_datacenter(
        client,
        "RPT_MUTUAL_HOLDSTOCKND",
        _NORTH_HOLD_COLUMNS,
        filter_expr=f"(TRADE_DATE='{ds}')",
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
                "trade_date": trade_date,
                "channel": _channel(item.get("MUTUAL_TYPE")),
                "holding_shares": float(item.get("HOLD_SHARES") or 0),
                "holding_mv": float(item.get("HOLD_MARKETCAP") or 0),
                "holding_ratio": float(item.get("HOLD_RATIO") or 0),
            }
        )
    if owns:
        client.close()
    return pl.DataFrame(rows) if rows else pl.DataFrame()


def fetch_northbound_flows(trade_date: date, *, client: EastMoneyClient | None = None) -> pl.DataFrame:
    owns = client is None
    if client is None:
        client = EastMoneyClient()
    ds = trade_date.isoformat()
    raw = fetch_datacenter(
        client,
        "RPT_MUTUAL_NETBUY",
        _NORTH_FLOW_COLUMNS,
        filter_expr=f"(TRADE_DATE='{ds}')",
    )
    rows = []
    for item in raw:
        td_raw = item.get("TRADE_DATE") or ds
        td = date.fromisoformat(str(td_raw)[:10])
        rows.append(
            {
                "trade_date": td,
                "channel": _channel(item.get("MUTUAL_TYPE")),
                "net_buy": float(item.get("NET_BUY_AMT") or 0),
                "buy_amount": float(item.get("BUY_AMT") or 0),
                "sell_amount": float(item.get("SELL_AMT") or 0),
            }
        )
    if owns:
        client.close()
    return pl.DataFrame(rows) if rows else pl.DataFrame()


def fetch_dragon_tiger(trade_date: date, *, client: EastMoneyClient | None = None) -> pl.DataFrame:
    owns = client is None
    if client is None:
        client = EastMoneyClient()
    ds = trade_date.isoformat()
    raw = fetch_datacenter(
        client,
        "RPT_DAILYBILLBOARD_DETAILS",
        _DRAGON_COLUMNS,
        filter_expr=f"(TRADE_DATE='{ds}')",
    )
    rows = []
    for item in raw:
        code = str(item.get("SECURITY_CODE", "")).zfill(6)
        exch = exchange_from_datacenter(item)
        sym = format_symbol(code, exch)
        if not symbol_from_em(code, 1 if exch == "SH" else 0):
            continue
        rows.append(
            {
                "symbol": sym,
                "trade_date": trade_date,
                "reason": str(item.get("EXPLANATION") or ""),
                "buy_amount": float(item.get("BUY_AMT") or 0),
                "sell_amount": float(item.get("SELL_AMT") or 0),
                "net_amount": float(item.get("NET_AMT") or 0),
            }
        )
    if owns:
        client.close()
    return pl.DataFrame(rows) if rows else pl.DataFrame()


def fetch_block_trades(trade_date: date, *, client: EastMoneyClient | None = None) -> pl.DataFrame:
    owns = client is None
    if client is None:
        client = EastMoneyClient()
    ds = trade_date.isoformat()
    raw = fetch_datacenter(
        client,
        "RPT_BLOCKTRADE_STA",
        _BLOCK_COLUMNS,
        filter_expr=f"(TRADE_DATE='{ds}')",
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
                "trade_date": trade_date,
                "price": float(item.get("DEAL_PRICE") or 0),
                "volume": float(item.get("DEAL_VOLUME") or 0),
                "amount": float(item.get("DEAL_AMT") or 0),
                "premium_ratio": float(item.get("PREMIUM_RATIO") or 0),
            }
        )
    if owns:
        client.close()
    return pl.DataFrame(rows) if rows else pl.DataFrame()
