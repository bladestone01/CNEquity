from __future__ import annotations

import logging
from datetime import date, timedelta

import polars as pl

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
) -> pl.DataFrame:
    wait_spec(rate_limit)
    # mootdx does not expose a trading calendar; a real source (exchange CSV /
    # derived from index bars) lands in M2. Until then this dataset is mock-only.
    return _fail_or_mock(
        "trading_calendar",
        "no real calendar source implemented yet (M2)",
        allow_mock,
        _mock_calendar(start, end),
    )


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
    rate_limit: RateLimitSpec | None = None,
    allow_mock: bool = False,
) -> pl.DataFrame:
    wait_spec(rate_limit)
    # mootdx xdxr integration lands in M2; an empty frame here would silently
    # disable ex-date rebackfill, so treat "not implemented" as a failure.
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
    return _fail_or_mock(
        "corporate_actions",
        "TDX xdxr fetch not implemented yet (M2)",
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
    # No real suspension/ST source yet (M2); the all-normal frame is fabricated.
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
        "no real suspension/ST source implemented yet (M2)",
        allow_mock,
        _mark_mock(pl.DataFrame(rows)),
    )


def normalize_with_source(df: pl.DataFrame, source: str = "tdx_protocol") -> pl.DataFrame:
    return with_provenance(df, source=source, data_version="v1")
