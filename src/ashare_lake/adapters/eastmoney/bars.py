"""EastMoney daily bars: tip clist snapshot + historical kline."""

from __future__ import annotations

import logging
from datetime import date

import polars as pl

from ashare_lake.adapters.eastmoney.clist import clist_rows_to_symbols, fetch_clist_pages
from ashare_lake.adapters.eastmoney.common import _to_float
from ashare_lake.adapters.eastmoney.em_auth import EastMoneyClient
from ashare_lake.domain.symbols import parse_symbol

logger = logging.getLogger(__name__)

_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_MARKET = {"SH": "1", "SZ": "0", "BJ": "2"}

# push2 clist live quote fields → tip OHLC. There is no trade_date in the
# payload; callers stamp the run's session date (same pattern as fund_flow).
_CLIST_BAR_FIELDS = "f12,f13,f17,f15,f16,f2,f5,f6"


def _secid(symbol: str) -> str:
    info = parse_symbol(symbol)
    return f"{_MARKET.get(info.exchange, '0')}.{info.code}"


def fetch_daily_bars_clist(
    trade_date: date,
    *,
    symbols: set[str] | list[str] | None = None,
    client: EastMoneyClient | None = None,
    config=None,
) -> pl.DataFrame:
    """Full-market tip bars from push2 clist (~54 pages), stamped *trade_date*.

    Clist is a live cross-section: it cannot invent history. Optional *symbols*
    keeps only gap-fill keys so a later compact ``keep=last`` cannot overwrite
    rows the primary source already staged for the same PK.
    """
    owns = client is None
    if client is None:
        client = EastMoneyClient(config=config)
    want = set(symbols) if symbols is not None else None
    rows_raw = fetch_clist_pages(client, fields=_CLIST_BAR_FIELDS)
    rows: list[dict] = []
    for sym, item in clist_rows_to_symbols(rows_raw):
        if want is not None and sym not in want:
            continue
        open_ = _to_float(item.get("f17"))
        high = _to_float(item.get("f15"))
        low = _to_float(item.get("f16"))
        close = _to_float(item.get("f2"))
        if open_ is None or high is None or low is None or close is None:
            continue
        vol = _to_float(item.get("f5"))
        amount = _to_float(item.get("f6"))
        rows.append(
            {
                "symbol": sym,
                "trade_date": trade_date,
                "open": float(open_),
                "high": float(high),
                "low": float(low),
                "close": float(close),
                "volume": int(vol or 0),
                "amount": float(amount or 0.0),
            }
        )
    if owns:
        client.close()
    return pl.DataFrame(rows) if rows else pl.DataFrame()


def fetch_daily_bars(
    symbols: list[str],
    start: date,
    end: date,
    *,
    client: EastMoneyClient | None = None,
) -> pl.DataFrame:
    """Per-symbol historical kline (slow). Prefer :func:`fetch_daily_bars_clist` for tip."""
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
