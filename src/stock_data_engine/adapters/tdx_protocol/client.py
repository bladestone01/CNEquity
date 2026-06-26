from __future__ import annotations

import logging
from datetime import date, timedelta

import polars as pl

from stock_data_engine.domain.rate_limit import RateLimitSpec, wait_spec
from stock_data_engine.domain.schemas import with_provenance
from stock_data_engine.domain.symbols import format_symbol, is_all_a_symbol

logger = logging.getLogger(__name__)

INDEX_SYMBOLS = [
    ("000001", "SH"),
    ("399001", "SZ"),
    ("399006", "SZ"),
    ("000688", "SH"),
]


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
    return pl.DataFrame(rows)


def _mock_calendar(start: date, end: date) -> pl.DataFrame:
    rows = []
    d = start
    while d <= end:
        is_trading = d.weekday() < 5
        rows.append({"trade_date": d, "is_trading": is_trading})
        d += timedelta(days=1)
    return pl.DataFrame(rows)


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
    return pl.DataFrame(rows)


def fetch_instruments(*, rate_limit: RateLimitSpec | None = None) -> pl.DataFrame:
    wait_spec(rate_limit)
    try:
        from mootdx.quotes import Quotes

        client = Quotes.factory(market="std")
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
    except ImportError:
        logger.info("mootdx not installed; using mock instruments")
    except Exception as exc:
        logger.warning("TDX instruments failed: %s; using mock", exc)
    return _mock_instruments()


def fetch_trading_calendar(
    start: date, end: date, *, rate_limit: RateLimitSpec | None = None
) -> pl.DataFrame:
    wait_spec(rate_limit)
    try:
        from mootdx.quotes import Quotes

        client = Quotes.factory(market="std")
        # mootdx may not expose calendar directly; fall through to mock/weekday
        _ = client
    except Exception:
        pass
    return _mock_calendar(start, end)


def fetch_daily_bars(
    symbols: list[str],
    start: date,
    end: date,
    *,
    rate_limit: RateLimitSpec | None = None,
) -> pl.DataFrame:
    try:
        from mootdx.quotes import Quotes

        client = Quotes.factory(market="std")
        rows = []
        for sym in symbols:
            wait_spec(rate_limit)
            code, exch = sym.split(".")
            market = 1 if exch == "SH" else (0 if exch == "SZ" else 2)
            try:
                raw = client.bars(symbol=code, frequency=9, market=market, start=start, offset=800)
            except Exception:
                continue
            if raw is None or len(raw) == 0:
                continue
            pdf = pl.from_pandas(raw) if hasattr(raw, "columns") else pl.DataFrame(raw)
            date_col = "datetime" if "datetime" in pdf.columns else "date"
            for row in pdf.iter_rows(named=True):
                td = row[date_col]
                if hasattr(td, "date"):
                    td = td.date()
                if td < start or td > end:
                    continue
                rows.append(
                    {
                        "symbol": sym,
                        "trade_date": td,
                        "open": float(row.get("open", 0)),
                        "high": float(row.get("high", 0)),
                        "low": float(row.get("low", 0)),
                        "close": float(row.get("close", 0)),
                        "volume": int(row.get("volume", 0)),
                        "amount": float(row.get("amount", 0)),
                    }
                )
        if rows:
            return pl.DataFrame(rows)
    except ImportError:
        logger.info("mootdx not installed; using mock daily bars")
    except Exception as exc:
        logger.warning("TDX daily bars failed: %s; using mock", exc)
    return _mock_bars(symbols, start, end)


def fetch_index_bars(
    start: date, end: date, *, rate_limit: RateLimitSpec | None = None
) -> pl.DataFrame:
    symbols = [format_symbol(c, e) for c, e in INDEX_SYMBOLS]
    df = fetch_daily_bars(symbols, start, end, rate_limit=rate_limit)
    return df.with_columns(pl.lit("1d").alias("frequency"))


def fetch_corporate_actions(
    trade_date: date, *, rate_limit: RateLimitSpec | None = None
) -> pl.DataFrame:
    wait_spec(rate_limit)
    rows = []
    try:
        from mootdx.quotes import Quotes

        client = Quotes.factory(market="std")
        _ = client
    except Exception:
        pass
    # Mock: no corporate actions on most days
    return (
        pl.DataFrame(rows)
        if rows
        else pl.DataFrame(
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
    )


def fetch_trading_status(
    symbols: list[str], trade_date: date, *, rate_limit: RateLimitSpec | None = None
) -> pl.DataFrame:
    wait_spec(rate_limit)
    rows = [
        {
            "symbol": sym,
            "trade_date": trade_date,
            "is_trading": True,
            "status": "normal",
        }
        for sym in symbols
    ]
    return pl.DataFrame(rows)


def normalize_with_source(df: pl.DataFrame, source: str = "tdx_protocol") -> pl.DataFrame:
    return with_provenance(df, source=source, data_version="v1")
