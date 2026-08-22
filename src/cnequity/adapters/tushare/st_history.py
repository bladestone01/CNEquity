"""Historical ST evidence for Beijing-exchange symbols via Tushare Pro.

Tushare's ``stock_st`` endpoint is a list of ST symbols, not a complete
per-symbol trading-status series.  The adapter therefore joins the returned
positive facts to the lake's own traded dates and emits explicit ``normal``
rows for every other traded date.  A missing or malformed source response is
retryable; it never becomes negative evidence.

The endpoint documents a history floor of 2017-01-01.  Symbols with persisted
bars before that date are returned as failed so the coverage receipt cannot
claim a pre-floor window.  Legacy 43/83/87xxx and current 920xxx BJ codes are
queried as aliases because the exchange migrated code identities.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from functools import lru_cache

import httpx
import polars as pl

from cnequity.adapters.eastmoney.corporate_actions_migration import _code_mapping
from cnequity.config import Config
from cnequity.domain.symbols import parse_symbol
from cnequity.query.parquet_scan import scan_parquet_root

__all__ = ["TUSHARE_ST_HISTORY_FLOOR", "fetch_st_history"]

logger = logging.getLogger(__name__)

TUSHARE_API_URL = "https://api.tushare.pro"
TUSHARE_ST_HISTORY_FLOOR = date(2017, 1, 1)
_PAGE_SIZE = 1000
_MAX_PAGES = 1000

_OUTPUT_SCHEMA = {
    "symbol": pl.Utf8,
    "trade_date": pl.Date,
    "is_trading": pl.Boolean,
    "status": pl.Utf8,
}


@lru_cache(maxsize=1)
def _reverse_code_mapping() -> dict[str, str]:
    """Return current 920xxx code -> legacy code for source aliasing."""
    return {new: old for old, new in _code_mapping().items()}


def _source_codes(symbol: str) -> tuple[str, ...]:
    info = parse_symbol(symbol)
    if info.exchange != "BJ":
        return (f"{info.code}.{info.exchange}",)
    mapping = _code_mapping()
    reverse = _reverse_code_mapping()
    aliases = {info.code}
    if info.code in mapping:
        aliases.add(mapping[info.code])
    if info.code in reverse:
        aliases.add(reverse[info.code])
    return tuple(f"{code}.BJ" for code in sorted(aliases))


def _parse_date(value: object) -> date | None:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        try:
            return datetime.strptime(text, "%Y%m%d").date()
        except ValueError:
            return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _traded_dates_from_lake(
    config: Config,
    symbols: list[str],
    start: date,
    end: date,
) -> dict[str, list[date]]:
    root = config.curated_root / "daily_bars"
    if not root.exists():
        return {}
    frame = (
        scan_parquet_root(
            root,
            partition_col="trade_date",
            start=start,
            end=end,
            traded_only=True,
        )
        .filter(pl.col("symbol").is_in(symbols))
        .select("symbol", "trade_date")
        .unique()
        .sort(["symbol", "trade_date"])
        .collect(engine="streaming")
    )
    return {
        (key[0] if isinstance(key, tuple) else key): [
            row["trade_date"] for row in rows.iter_rows(named=True)
        ]
        for key, rows in frame.partition_by("symbol", as_dict=True).items()
    }


def _request_rows(
    client: httpx.Client,
    token: str,
    ts_code: str,
    start: date,
    end: date,
    *,
    config: Config | None,
) -> list[dict]:
    rows: list[dict] = []
    for page in range(_MAX_PAGES):
        if config is not None:
            config.rate_limit("tushare")
        response = client.post(
            TUSHARE_API_URL,
            json={
                "api_name": "stock_st",
                "token": token,
                "params": {
                    "ts_code": ts_code,
                    "start_date": start.strftime("%Y%m%d"),
                    "end_date": end.strftime("%Y%m%d"),
                    "limit": _PAGE_SIZE,
                    "offset": page * _PAGE_SIZE,
                },
                "fields": "ts_code,name,trade_date,type,type_name",
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Tushare stock_st response is not an object")
        if payload.get("code") not in (0, "0"):
            raise RuntimeError(
                f"Tushare stock_st failed: {payload.get('msg') or payload.get('message') or payload.get('code')}"
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("Tushare stock_st response has no data object")
        fields = data.get("fields")
        items = data.get("items")
        if not isinstance(fields, list) or not isinstance(items, list):
            raise RuntimeError("Tushare stock_st response has invalid fields/items")
        page_rows: list[dict] = []
        for item in items:
            if not isinstance(item, list) or len(item) != len(fields):
                raise RuntimeError("Tushare stock_st response contains a malformed row")
            row = dict(zip((str(field) for field in fields), item, strict=True))
            page_rows.append(row)
        rows.extend(page_rows)
        if len(page_rows) < _PAGE_SIZE:
            break
    else:
        raise RuntimeError("Tushare stock_st pagination exceeded the safety limit")
    return rows


def _empty() -> pl.DataFrame:
    return pl.DataFrame(schema=_OUTPUT_SCHEMA)


def fetch_st_history(
    symbols: list[str],
    start: date,
    end: date,
    *,
    token: str | None = None,
    client: httpx.Client | None = None,
    config: Config | None = None,
    trading_dates: Mapping[str, Iterable[date]] | None = None,
) -> tuple[pl.DataFrame, list[str]]:
    """Fetch complete traded-day ST/normal evidence for BJ symbols.

    ``trading_dates`` is injectable for tests and callers that already have a
    bar calendar.  In production it is read from ``daily_bars`` through
    *config*.  The source token is deliberately required; unlike a live ST
    snapshot, an unauthenticated empty response cannot be treated as normal.
    """
    token = (token or (config.tushare_token if config is not None else None) or "").strip()
    if not token:
        raise ValueError(
            "Tushare historical ST evidence requires TUSHARE_TOKEN or [sources.tushare].token"
        )
    if start > end:
        raise ValueError(f"Tushare ST history window is inverted: {start} > {end}")
    symbols = sorted(set(symbols))
    if any(parse_symbol(symbol).exchange != "BJ" for symbol in symbols):
        raise ValueError("Tushare ST history adapter only accepts BJ symbols")
    if trading_dates is None:
        if config is None:
            raise ValueError("Tushare ST history requires config or trading_dates")
        dates_by_symbol = _traded_dates_from_lake(config, symbols, start, end)
    else:
        dates_by_symbol = {
            symbol: sorted({d for d in dates if start <= d <= end})
            for symbol, dates in trading_dates.items()
        }

    owns_client = client is None
    if client is None:
        timeout = config.tushare_timeout_sec if config is not None else 30.0
        client = httpx.Client(timeout=timeout)

    rows: list[dict] = []
    failed: list[str] = []
    try:
        for symbol in symbols:
            traded_dates = dates_by_symbol.get(symbol, [])
            if any(d < TUSHARE_ST_HISTORY_FLOOR for d in traded_dates):
                failed.append(symbol)
                continue
            try:
                st_dates: set[date] = set()
                aliases = set(_source_codes(symbol))
                for source_code in sorted(aliases):
                    for item in _request_rows(
                        client,
                        token,
                        source_code,
                        max(start, TUSHARE_ST_HISTORY_FLOOR),
                        end,
                        config=config,
                    ):
                        returned_code = str(item.get("ts_code") or "").strip().upper()
                        if returned_code and returned_code not in aliases:
                            raise RuntimeError(
                                f"Tushare stock_st returned {returned_code} while querying {symbol}"
                            )
                        trade_date = _parse_date(item.get("trade_date"))
                        if trade_date is None or not (start <= trade_date <= end):
                            continue
                        kind = str(item.get("type") or "").strip().upper()
                        if kind not in {"ST", "*ST"}:
                            raise RuntimeError(f"unknown Tushare ST type {kind!r} for {symbol}")
                        st_dates.add(trade_date)
                rows.extend(
                    {
                        "symbol": symbol,
                        "trade_date": trade_date,
                        "is_trading": True,
                        "status": "st" if trade_date in st_dates else "normal",
                    }
                    for trade_date in traded_dates
                )
            except Exception as exc:  # noqa: BLE001 — retry the symbol, not a false normal set
                failed.append(symbol)
                logger.warning("tushare ST history: failed for %s: %s", symbol, exc)
    finally:
        if owns_client:
            client.close()

    if not rows:
        return _empty(), failed
    return (
        pl.DataFrame(rows, schema=_OUTPUT_SCHEMA)
        .unique(subset=["symbol", "trade_date"], keep="last")
        .sort(["trade_date", "symbol"]),
        failed,
    )
