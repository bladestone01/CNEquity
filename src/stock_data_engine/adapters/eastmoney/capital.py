"""EastMoney L4 capital datasets: fund flow, margin, northbound, dragon tiger, block trades."""

from __future__ import annotations

import logging
import time
from datetime import date

import polars as pl

from stock_data_engine.adapters.eastmoney.clist import clist_rows_to_symbols, fetch_clist_pages
from stock_data_engine.adapters.eastmoney.common import (
    _to_float,
    exchange_from_datacenter,
    symbol_from_em,
    symbol_from_secucode,
)
from stock_data_engine.adapters.eastmoney.datacenter import fetch_datacenter
from stock_data_engine.adapters.eastmoney.em_auth import EastMoneyClient
from stock_data_engine.config import Config
from stock_data_engine.domain.symbols import format_symbol

logger = logging.getLogger(__name__)

_FUND_FLOW_FIELDS = "f12,f13,f62,f66,f72,f78,f84"
_MARGIN_REPORT = "RPTA_WEB_RZRQ_GGMX"
_MARGIN_COLUMNS = "DATE,SCODE,SECUCODE,RZYE,RZMRE,RQYE,RQMCL"
_NORTH_HOLD_REPORT = "RPT_MUTUAL_HOLDSTOCKNORTH_STA"
_NORTH_HOLD_COLUMNS = (
    "SECUCODE,TRADE_DATE,MUTUAL_TYPE,HOLD_SHARES,HOLD_MARKET_CAP,HOLD_SHARES_RATIO"
)
_DRAGON_COLUMNS = (
    "SECURITY_CODE,TRADE_DATE,EXPLANATION,BILLBOARD_BUY_AMT,BILLBOARD_SELL_AMT,BILLBOARD_NET_AMT"
)
_BLOCK_COLUMNS = "SECURITY_CODE,TRADE_DATE,VOLUME,DEAL_AMT,AVERAGE_PRICE,PREMIUM_RATIO"
_FFLOW_KLINE_URL = (
    "https://push2his.eastmoney.com/api/qt/stock/fflow/kline/get"
    "?secid=1.000001&klt=101&lmt={limit}&fields1=f1,f2,f3,f7"
    "&fields2=f51,f52,f53,f54,f55,f56"
)
_KAMT_URL = "https://push2.eastmoney.com/api/qt/kamt/get"


def _channel(mutual_type: str | int | None) -> str:
    text = str(mutual_type or "")
    if text in {"001", "1"} or "沪" in text or "SH" in text.upper():
        return "SH"
    return "SZ"


def _margin_symbol(item: dict) -> str | None:
    sym = symbol_from_secucode(item.get("SECUCODE"))
    if sym:
        return sym
    code = str(item.get("SCODE", "")).zfill(6)
    market = str(item.get("TRADE_MARKET") or item.get("MARKET") or "")
    if "沪" in market or "科创" in market:
        exch = "SH"
    elif "京" in market or "北" in market:
        exch = "BJ"
    else:
        exch = "SZ"
    return symbol_from_em(code, 1 if exch == "SH" else (2 if exch == "BJ" else 0))


def _northbound_kline_lines(
    client: EastMoneyClient, *, limit: int = 120, max_retries: int = 3
) -> list[str]:
    url = _FFLOW_KLINE_URL.format(limit=limit)
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = client.get(url, timeout=30.0)
            resp.raise_for_status()
            payload = resp.json()
            data = payload.get("data") or {}
            lines = data.get("klines") or []
            if lines:
                return lines
        except Exception as exc:
            last_exc = exc
            if attempt + 1 < max_retries:
                time.sleep(1.0 * (attempt + 1))
    if last_exc is not None:
        logger.warning("EastMoney northbound kline fetch failed: %s", last_exc)
    return []


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
                "main_net_inflow": _to_float(item.get("f62")),
                "super_large_net_inflow": _to_float(item.get("f66")),
                "large_net_inflow": _to_float(item.get("f72")),
                "medium_net_inflow": _to_float(item.get("f78")),
                "small_net_inflow": _to_float(item.get("f84")),
            }
        )
    if owns:
        client.close()
    return pl.DataFrame(rows) if rows else pl.DataFrame()


def fetch_margin_trading(
    trade_date: date, *, client: EastMoneyClient | None = None
) -> pl.DataFrame:
    owns = client is None
    if client is None:
        client = EastMoneyClient()
    ds = trade_date.isoformat()
    raw = fetch_datacenter(
        client,
        _MARGIN_REPORT,
        _MARGIN_COLUMNS,
        filter_expr=f"(DATE='{ds}')",
    )
    rows = []
    for item in raw:
        sym = _margin_symbol(item)
        if not sym:
            continue
        rows.append(
            {
                "symbol": sym,
                "trade_date": trade_date,
                "margin_balance": float(item.get("RZYE") or 0),
                "margin_buy": float(item.get("RZMRE") or 0),
                "short_balance": float(item.get("RQYE") or 0),
                "short_sell_volume": float(item.get("RQMCL") or 0),
            }
        )
    if owns:
        client.close()
    return pl.DataFrame(rows) if rows else pl.DataFrame()


_NORTH_BACKFILL_START_YEAR = 2016
_NORTH_QUARTER_END_MMDD = (("03", "31"), ("06", "30"), ("09", "30"), ("12", "31"))


def _quarter_end_dates(trade_date: date) -> list[str]:
    """Quarter-end dates from 2016 through *trade_date*, most recent first."""
    out: list[str] = []
    for year in range(_NORTH_BACKFILL_START_YEAR, trade_date.year + 1):
        for mm, dd in _NORTH_QUARTER_END_MMDD:
            ds = f"{year}-{mm}-{dd}"
            if date.fromisoformat(ds) <= trade_date:
                out.append(ds)
    return sorted(out, reverse=True)


def fetch_northbound_holdings(
    trade_date: date,
    *,
    backfill: bool = False,
    client: EastMoneyClient | None = None,
    config: Config | None = None,
) -> pl.DataFrame:
    # Since Aug 2024 CSRC publishes per-stock northbound holdings only on
    # quarter-ends (no daily feed), so fetch by quarter-end TRADE_DATE: daily
    # keeps the latest quarter fresh, backfill walks every quarter from 2016.
    owns = client is None
    if client is None:
        client = EastMoneyClient(config=config)

    periods = _quarter_end_dates(trade_date)
    if not backfill:
        # latest 2 quarters: the just-ended one may not be published yet, so
        # keep the last complete quarter fresh too.
        periods = periods[:2]

    rows: list[dict] = []
    try:
        for period in periods:
            if config is not None:
                config.rate_limit("eastmoney")
            raw = fetch_datacenter(
                client,
                _NORTH_HOLD_REPORT,
                _NORTH_HOLD_COLUMNS,
                filter_expr=f"(TRADE_DATE='{period}')",
            )
            period_date = date.fromisoformat(period)
            for item in raw:
                sym = symbol_from_secucode(item.get("SECUCODE"))
                if not sym:
                    continue
                rows.append(
                    {
                        "symbol": sym,
                        "trade_date": period_date,
                        "channel": _channel(item.get("MUTUAL_TYPE")),
                        "holding_shares": float(item.get("HOLD_SHARES") or 0),
                        "holding_mv": float(item.get("HOLD_MARKET_CAP") or 0),
                        "holding_ratio": float(item.get("HOLD_SHARES_RATIO") or 0),
                    }
                )
    finally:
        if owns:
            client.close()
    return pl.DataFrame(rows) if rows else pl.DataFrame()


def fetch_northbound_flows(
    trade_date: date, *, client: EastMoneyClient | None = None
) -> pl.DataFrame:
    owns = client is None
    if client is None:
        client = EastMoneyClient()

    rows: list[dict] = []
    for line in _northbound_kline_lines(client):
        parts = line.split(",")
        if len(parts) < 3:
            continue
        try:
            row_date = date.fromisoformat(parts[0])
        except ValueError:
            continue
        if row_date != trade_date:
            continue
        rows.extend(
            [
                {
                    "trade_date": row_date,
                    "channel": "SH",
                    "net_buy": float(parts[1]),
                    "buy_amount": 0.0,
                    "sell_amount": 0.0,
                },
                {
                    "trade_date": row_date,
                    "channel": "SZ",
                    "net_buy": float(parts[2]),
                    "buy_amount": 0.0,
                    "sell_amount": 0.0,
                },
            ]
        )
        break

    if not rows:
        try:
            resp = client.get(f"{_KAMT_URL}?fields1=f1,f2,f3,f4&fields2=f51,f52,f53,f54,f55,f56")
            resp.raise_for_status()
            data = resp.json().get("data") or {}
        except Exception as exc:
            logger.warning("EastMoney kamt northbound fetch failed: %s", exc)
            data = {}
        for key, channel in (("hk2sh", "SH"), ("hk2sz", "SZ")):
            block = data.get(key) or {}
            date2 = str(block.get("date2") or "")[:10]
            if date2 != trade_date.isoformat():
                continue
            # dayNetAmtIn is 万元 on EastMoney kamt API.
            net = float(block.get("dayNetAmtIn") or 0) * 10_000
            rows.append(
                {
                    "trade_date": trade_date,
                    "channel": channel,
                    "net_buy": net,
                    "buy_amount": 0.0,
                    "sell_amount": 0.0,
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
                "buy_amount": float(item.get("BILLBOARD_BUY_AMT") or 0),
                "sell_amount": float(item.get("BILLBOARD_SELL_AMT") or 0),
                "net_amount": float(item.get("BILLBOARD_NET_AMT") or 0),
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
                "price": float(item.get("AVERAGE_PRICE") or 0),
                "volume": float(item.get("VOLUME") or 0),
                "amount": float(item.get("DEAL_AMT") or 0),
                "premium_ratio": float(item.get("PREMIUM_RATIO") or 0),
            }
        )
    if owns:
        client.close()
    return pl.DataFrame(rows) if rows else pl.DataFrame()
