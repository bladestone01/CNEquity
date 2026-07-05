"""EastMoney daily bars backup source (kline API)."""

from __future__ import annotations

import logging
from datetime import date

import polars as pl

from stock_data_engine.adapters.eastmoney.em_auth import EastMoneyClient
from stock_data_engine.domain.symbols import parse_symbol

logger = logging.getLogger(__name__)

_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_MARKET = {"SH": "1", "SZ": "0", "BJ": "2"}


def _secid(symbol: str) -> str:
    info = parse_symbol(symbol)
    return f"{_MARKET.get(info.exchange, '0')}.{info.code}"


def fetch_daily_bars(
    symbols: list[str],
    start: date,
    end: date,
    *,
    client: EastMoneyClient | None = None,
) -> pl.DataFrame:
    owns = client is None
    if client is None:
        client = EastMoneyClient()

    beg = start.strftime("%Y%m%d")
    end_s = end.strftime("%Y%m%d")
    rows: list[dict] = []

    for sym in symbols:
        params = {
            "secid": _secid(sym),
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101",
            "fqt": "0",
            "beg": beg,
            "end": end_s,
        }
        try:
            resp = client.get(_KLINE_URL, params=params)
            resp.raise_for_status()
            klines = (resp.json().get("data") or {}).get("klines") or []
        except Exception as exc:
            logger.warning("EastMoney kline failed for %s: %s", sym, exc)
            continue

        for line in klines:
            parts = str(line).split(",")
            if len(parts) < 7:
                continue
            try:
                trade_date = date.fromisoformat(parts[0])
            except ValueError:
                continue
            rows.append(
                {
                    "symbol": sym,
                    "trade_date": trade_date,
                    "open": float(parts[1]),
                    "close": float(parts[2]),
                    "high": float(parts[3]),
                    "low": float(parts[4]),
                    "volume": int(float(parts[5])),
                    "amount": float(parts[6]),
                }
            )

    if owns:
        client.close()
    if not rows:
        return pl.DataFrame()
    return pl.DataFrame(rows)
