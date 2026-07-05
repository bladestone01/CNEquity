from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from stock_data_engine.adapters.calendar.exchange_calendar import (
    build_trading_calendar,
    ensure_seed_csv,
)
from stock_data_engine.adapters.eastmoney.corporate_actions import fetch_corporate_actions_eastmoney
from stock_data_engine.adapters.eastmoney.trading_status import fetch_trading_status_eastmoney
from stock_data_engine.adapters.tdx_protocol.bars import fetch_bars_paginated
from stock_data_engine.adapters.tdx_protocol.corporate_actions import fetch_corporate_actions_tdx
from stock_data_engine.domain.rate_limit import RateLimitSpec, wait_spec
from stock_data_engine.domain.schemas import MOCK_SOURCE, with_provenance
from stock_data_engine.domain.symbols import format_symbol, is_all_a_symbol

logger = logging.getLogger(__name__)

INDEX_SYMBOLS = [
    ("000001", "SH"),
    ("399001", "SZ"),
    ("399006", "SZ"),
    ("000688", "SH"),
]


class TdxSourceError(RuntimeError):
    """Raised when the TDX source cannot deliver real data.

    Fabricated fallback data is only allowed behind an explicit
    `allow_mock=True` (config `[tdx_protocol].allow_mock`), and is then
    labeled `source="mock"` so audit can reject it.
    """


def _quotes_client():
    """Build a mootdx client; isolated so tests can monkeypatch it."""
    from mootdx.quotes import Quotes

    return Quotes.factory(market="std")


def _mark_mock(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(pl.lit(MOCK_SOURCE).alias("source"))


def _mock_instruments() -> pl.DataFrame:
    rows = []
    for code, exch in [("600519", "SH"), ("000001", "SZ"), ("300750", "SZ"), ("920000", "BJ")]:
        rows.append(
            {
                "symbol": format_symbol(code, exch),
                "name": f"Mock-{code}",
                "exchange": exch,
                "asset_type": "stock",
                "list_date": date(2010, 1, 1),
                "delist_date": None,
                "prev_symbol": None,
            }
        )
    return _mark_mock(pl.DataFrame(rows))


def _mock_calendar(start: date, end: date) -> pl.DataFrame:
    rows = []
    d = start
    while d <= end:
        is_trading = d.weekday() < 5
        rows.append({"trade_date": d, "is_trading": is_trading})
        d += timedelta(days=1)
    return _mark_mock(pl.DataFrame(rows))


def _mock_bars(symbols: list[str], start: date, end: date) -> pl.DataFrame:
    rows = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            for i, sym in enumerate(symbols):
                base = 10.0 + i
                rows.append(
                    {
                        "symbol": sym,
                        "trade_date": d,
                        "open": base,
                        "high": base + 1,
                        "low": base - 0.5,
                        "close": base + 0.2,
                        "volume": 1_000_000,
                        "amount": base * 1_000_000,
                    }
                )
        d += timedelta(days=1)
    return _mark_mock(pl.DataFrame(rows))


def _fail_or_mock(
    dataset: str, reason: str, allow_mock: bool, mock_df: pl.DataFrame
) -> pl.DataFrame:
    if not allow_mock:
        raise TdxSourceError(f"{dataset}: {reason} (set [tdx_protocol].allow_mock for tests)")
    logger.warning("%s: %s; returning mock rows labeled source=%s", dataset, reason, MOCK_SOURCE)
    return mock_df


def fetch_instruments(
    *, rate_limit: RateLimitSpec | None = None, allow_mock: bool = False
) -> pl.DataFrame:
    wait_spec(rate_limit)
    try:
        client = _quotes_client()
        frames = []
        for market, exch in (("SH", "SH"), ("SZ", "SZ"), ("BJ", "BJ")):
            try:
                raw = client.stocks(market=market)
            except Exception:
                continue
            if raw is None or len(raw) == 0:
                continue
            pdf = pl.from_pandas(raw) if hasattr(raw, "columns") else pl.DataFrame(raw)
            code_col = "code" if "code" in pdf.columns else pdf.columns[0]
            name_col = "name" if "name" in pdf.columns else pdf.columns[1]
            for row in pdf.iter_rows(named=True):
                code = str(row[code_col]).zfill(6)
                if not is_all_a_symbol(code, exch):
                    continue
                frames.append(
                    {
                        "symbol": format_symbol(code, exch),
                        "name": str(row[name_col]),
                        "exchange": exch,
                        "asset_type": "stock",
                        "list_date": None,
                        "delist_date": None,
                        "prev_symbol": None,
                    }
                )
        if frames:
            return pl.DataFrame(frames)
        reason = "TDX returned no instruments"
    except ImportError:
        reason = "mootdx not installed"
    except Exception as exc:
        reason = f"TDX fetch failed: {exc}"
    return _fail_or_mock("instruments", reason, allow_mock, _mock_instruments())


def fetch_trading_calendar(
    start: date,
    end: date,
    *,
    rate_limit: RateLimitSpec | None = None,
    allow_mock: bool = False,
    curated_root: Path | None = None,
    seed_path: Path | None = None,
) -> pl.DataFrame:
    wait_spec(rate_limit)
    try:
        ensure_seed_csv(seed_path)
        return build_trading_calendar(
            start,
            end,
            seed_path=seed_path,
            curated_root=curated_root,
        )
    except Exception as exc:
        reason = f"calendar seed load failed: {exc}"
        return _fail_or_mock("trading_calendar", reason, allow_mock, _mock_calendar(start, end))


def fetch_daily_bars(
    symbols: list[str],
    start: date,
    end: date,
    *,
    rate_limit: RateLimitSpec | None = None,
    allow_mock: bool = False,
) -> pl.DataFrame:
    try:
        client = _quotes_client()
        rows = []
        for sym in symbols:
            rows.extend(fetch_bars_paginated(client, sym, start, end, rate_limit=rate_limit))
        if rows:
            return pl.DataFrame(rows)
        reason = "TDX returned no bars"
    except ImportError:
        reason = "mootdx not installed"
    except Exception as exc:
        reason = f"TDX fetch failed: {exc}"
    return _fail_or_mock("daily_bars", reason, allow_mock, _mock_bars(symbols, start, end))


def fetch_index_bars(
    start: date,
    end: date,
    *,
    rate_limit: RateLimitSpec | None = None,
    allow_mock: bool = False,
) -> pl.DataFrame:
    symbols = [format_symbol(c, e) for c, e in INDEX_SYMBOLS]
    df = fetch_daily_bars(symbols, start, end, rate_limit=rate_limit, allow_mock=allow_mock)
    return df.with_columns(pl.lit("1d").alias("frequency"))


def fetch_corporate_actions(
    trade_date: date,
    *,
    symbols: list[str] | None = None,
    backfill: bool = False,
    rate_limit: RateLimitSpec | None = None,
    allow_mock: bool = False,
    primary_only: bool = False,
) -> pl.DataFrame:
    wait_spec(rate_limit)
    empty = pl.DataFrame(
        schema={
            "symbol": pl.Utf8,
            "ex_date": pl.Date,
            "action_type": pl.Utf8,
            "cash_dividend": pl.Float64,
            "bonus_ratio": pl.Float64,
            "transfer_ratio": pl.Float64,
            "allotment_ratio": pl.Float64,
            "allotment_price": pl.Float64,
        }
    )

    frames: list[pl.DataFrame] = []
    try:
        if symbols:
            tdx_df = fetch_corporate_actions_tdx(
                symbols,
                trade_date=trade_date,
                backfill=backfill,
                client_factory=_quotes_client,
                rate_limit=rate_limit,
            )
            if tdx_df.height:
                frames.append(tdx_df.with_columns(pl.lit("tdx_protocol").alias("source")))
    except ImportError:
        logger.debug("mootdx not installed for corporate_actions")
    except Exception as exc:
        logger.warning("TDX corporate_actions failed: %s", exc)

    try:
        if not primary_only:
            em_df = fetch_corporate_actions_eastmoney(trade_date, backfill=backfill)
            if em_df.height:
                frames.append(em_df.with_columns(pl.lit("eastmoney").alias("source")))
    except Exception as exc:
        logger.warning("EastMoney corporate_actions backup failed: %s", exc)

    if frames:
        out = pl.concat(frames, how="diagonal_relaxed")
        if "source" not in out.columns:
            out = out.with_columns(pl.lit("tdx_protocol").alias("source"))
        else:
            out = out.with_columns(
                pl.when(pl.col("source").is_null())
                .then(pl.lit("tdx_protocol"))
                .otherwise(pl.col("source"))
                .alias("source")
            )
        if not backfill:
            out = out.filter(pl.col("ex_date") == trade_date)
        return out.unique(subset=["symbol", "ex_date", "action_type"], keep="last")

    return _fail_or_mock(
        "corporate_actions",
        "no corporate actions from TDX or EastMoney",
        allow_mock,
        empty,
    )


def fetch_trading_status(
    symbols: list[str],
    trade_date: date,
    *,
    rate_limit: RateLimitSpec | None = None,
    allow_mock: bool = False,
) -> pl.DataFrame:
    wait_spec(rate_limit)
    try:
        df = fetch_trading_status_eastmoney(symbols, trade_date)
        if df.height:
            return df
        reason = "EastMoney returned no trading status rows"
    except Exception as exc:
        reason = f"EastMoney trading_status failed: {exc}"

    rows = [
        {
            "symbol": sym,
            "trade_date": trade_date,
            "is_trading": True,
            "status": "normal",
        }
        for sym in symbols
    ]
    return _fail_or_mock(
        "trading_status",
        reason,
        allow_mock,
        _mark_mock(pl.DataFrame(rows)),
    )


def normalize_with_source(df: pl.DataFrame, source: str = "tdx_protocol") -> pl.DataFrame:
    return with_provenance(df, source=source, data_version="v1")
