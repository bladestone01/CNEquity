"""Sina daily K-line — the delisted-symbol bar source, and a cross-check on TDX.

Two jobs neither primary source can do:

**Delisted history.** TDX serves nothing for a code that has left the market
(verified: empty for 600001/600002/600003/600005, full history for live names),
and EastMoney's kline host is unreachable from many networks. Sina keeps the
whole series, listing day to delisting day, which is what makes a
survivorship-free universe possible at all.

**An independent second opinion on the close.** Sina is a different vendor on a
different protocol from TDX, so comparing closes catches a class of defect no
single-source check can see — most importantly a capture that fires before the
session ends and writes a bar with the right open, a wrong close, and partial
volume.

Two units traps, both verified against a 760-day overlap on 600519.SH:

* ``volume`` is in **shares**; the lake stores **lots** (手). Divide by 100.
* there is **no turnover/amount field**, so ``amount`` is null. Liquidity
  factors built on turnover will not see delisted names.

Prices are unadjusted, matching the lake's raw-price contract: over that same
760-day overlap 759 days matched the curated close exactly, and the one that did
not was the truncated capture this adapter now helps detect.
"""

from __future__ import annotations

import json
import logging
from datetime import date

import httpx
import polars as pl

from ashare_lake.adapters.sina.adj_factors import to_sina_symbol
from ashare_lake.domain.schemas import DAILY_BARS_SCHEMA

logger = logging.getLogger(__name__)

__all__ = ["fetch_daily_bars_sina", "symbol_exists", "SinaBarsError"]

_KLINE_URL = (
    "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.sina.com.cn/",
}
# Full history in one request. Measured: 5000 returns a complete 1998→2009
# series (2753 bars) for 600001.SH and larger values return no more, so this is
# the endpoint's ceiling rather than an arbitrary page size.
_FULL_HISTORY_LEN = 5000
# Sina reports shares; curated bars are in lots.
_SHARES_PER_LOT = 100

_OUTPUT_COLS = [c for c in DAILY_BARS_SCHEMA if c not in ("source", "data_version", "fetched_at")]


class SinaBarsError(RuntimeError):
    """Raised when Sina returns a payload that cannot be parsed."""


def _parse_payload(text: str) -> list[dict] | None:
    """Sina answers with JSON, or ``null`` for a code that never existed."""
    text = text.strip()
    if not text or text == "null":
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SinaBarsError(f"unparseable Sina kline payload: {text[:120]!r}") from exc


def _request(
    symbol: str,
    datalen: int,
    client: httpx.Client | None,
) -> list[dict] | None:
    params = {
        "symbol": to_sina_symbol(symbol),
        "scale": 240,  # daily
        "ma": "no",
        "datalen": datalen,
    }
    owns = client is None
    if client is None:
        client = httpx.Client(timeout=30.0)
    try:
        resp = client.get(_KLINE_URL, params=params, headers=_HEADERS)
        resp.raise_for_status()
        return _parse_payload(resp.text)
    finally:
        if owns:
            client.close()


def symbol_exists(symbol: str, *, client: httpx.Client | None = None) -> date | None:
    """Last trading date Sina has for *symbol*, or None if it never traded.

    A one-bar request, so it is cheap enough to sweep the whole A-share code
    space. This is how delisted codes are discovered without a vendor's
    delisting list: a code absent from today's instruments that still answers
    here used to trade, and the date it stops is roughly when it left.
    """
    rows = _request(symbol, 1, client)
    if not rows:
        return None
    try:
        return date.fromisoformat(str(rows[-1]["day"])[:10])
    except (KeyError, ValueError):
        return None


def fetch_daily_bars_sina(
    symbol: str,
    *,
    start: date | None = None,
    end: date | None = None,
    datalen: int = _FULL_HISTORY_LEN,
    client: httpx.Client | None = None,
) -> pl.DataFrame:
    """Unadjusted daily bars for *symbol*, in the curated ``daily_bars`` shape.

    Returns an empty frame (not an error) for a code Sina has never heard of —
    sweeping the code space depends on being able to tell "never issued" from
    "request failed", and a transport failure still raises.
    """
    rows = _request(symbol, datalen, client)
    if not rows:
        return pl.DataFrame(schema={c: DAILY_BARS_SCHEMA[c] for c in _OUTPUT_COLS})

    out: list[dict] = []
    for item in rows:
        try:
            trade_date = date.fromisoformat(str(item["day"])[:10])
            volume_shares = float(item["volume"])
            out.append(
                {
                    "symbol": symbol,
                    "trade_date": trade_date,
                    "open": float(item["open"]),
                    "high": float(item["high"]),
                    "low": float(item["low"]),
                    "close": float(item["close"]),
                    "volume": int(volume_shares // _SHARES_PER_LOT),
                    # Sina does not report turnover; leave it null rather than
                    # inventing close × volume, which is not the traded amount.
                    "amount": None,
                }
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("Sina kline: skipping malformed row for %s: %r", symbol, item)
            continue

    df = pl.DataFrame(out, schema={c: DAILY_BARS_SCHEMA[c] for c in _OUTPUT_COLS})
    if start is not None:
        df = df.filter(pl.col("trade_date") >= start)
    if end is not None:
        df = df.filter(pl.col("trade_date") <= end)
    return df.sort("trade_date")
