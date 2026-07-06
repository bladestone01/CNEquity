"""Shared helpers for step implementations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

import polars as pl

from stock_data_engine.adapters.tdx_protocol.client import fetch_instruments
from stock_data_engine.config import Config
from stock_data_engine.storage import StagingWriter
from stock_data_engine.storage.state import StateStore

INCREMENTAL_LOOKBACK_DAYS = 5
BACKFILL_START = date(2016, 1, 1)


def write_simple(config: Config, run_id: str, dataset: str, df: pl.DataFrame) -> dict:
    writer = StagingWriter(config.staging_root)
    writer.write_batch(dataset, run_id, "batch-0", df)
    return {"rows_read": df.height, "rows_written": df.height}


def incremental_window(config: Config, dataset: str, trade_date: date) -> date:
    """Start date for incremental fetch: day after watermark, or lookback window."""
    state = StateStore(config.meta_root)
    watermark = state.get_date(dataset)
    if watermark is not None:
        return min(watermark + timedelta(days=1), trade_date)
    return trade_date - timedelta(days=INCREMENTAL_LOOKBACK_DAYS)


def _load_trading_calendar_df(config: Config) -> pl.DataFrame | None:
    curated = config.curated_root / "trading_calendar"
    if curated.exists():
        files = list(curated.glob("**/*.parquet"))
        if files:
            return pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")
    staging = list(config.staging_root.glob("trading_calendar/**/*.parquet"))
    if staging:
        latest = max(staging, key=lambda p: p.stat().st_mtime)
        return pl.read_parquet(latest)
    return None


def list_trading_dates(config: Config, start: date, end: date) -> list[date]:
    """Trading days in [start, end] from curated/staging calendar, else Mon–Fri."""
    if start > end:
        return []
    cal = _load_trading_calendar_df(config)
    if cal is not None and not cal.is_empty() and "trade_date" in cal.columns:
        out = (
            cal.filter(
                pl.col("is_trading")
                & (pl.col("trade_date") >= start)
                & (pl.col("trade_date") <= end)
            )["trade_date"]
            .sort()
            .to_list()
        )
        if out:
            return out
    dates: list[date] = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    return dates


def incremental_trade_dates(config: Config, dataset: str, trade_date: date) -> list[date]:
    """Trading days to fetch for a daily dataset: [watermark+1, trade_date]."""
    start = incremental_window(config, dataset, trade_date)
    return list_trading_dates(config, start, trade_date)


def is_trading_day(config: Config, trade_date: date) -> bool:
    """Return whether *trade_date* is a trading day per curated calendar or seed."""
    cal = _load_trading_calendar_df(config)
    if cal is not None and not cal.is_empty():
        row = cal.filter(pl.col("trade_date") == trade_date)
        if not row.is_empty():
            return bool(row["is_trading"][0])

    from stock_data_engine.adapters.calendar.exchange_calendar import (
        build_trading_calendar,
        ensure_seed_csv,
    )

    seed_path = config.meta_root / "seeds" / "trading_calendar.csv"
    effective_seed = seed_path if seed_path.exists() else ensure_seed_csv()
    day_cal = build_trading_calendar(
        trade_date,
        trade_date,
        seed_path=effective_seed,
        curated_root=config.curated_root if config.curated_root.exists() else None,
    )
    if not day_cal.is_empty():
        return bool(day_cal["is_trading"][0])
    return trade_date.weekday() < 5


def fetch_incremental_daily(
    config: Config,
    dataset: str,
    trade_date: date,
    fetch_fn: Callable[[date], pl.DataFrame],
    *,
    allow_empty: bool = False,
) -> pl.DataFrame:
    """Fetch one or more trading days from watermark+1 through *trade_date*."""
    if getattr(config, "_backfill", False):
        return fetch_fn(trade_date)

    dates = incremental_trade_dates(config, dataset, trade_date)
    if not dates:
        return pl.DataFrame()

    frames: list[pl.DataFrame] = []
    for d in dates:
        part = fetch_fn(d)
        if part.is_empty():
            if not allow_empty:
                raise RuntimeError(f"{dataset}: no rows returned for {d.isoformat()}")
            continue
        frames.append(part)
    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="diagonal_relaxed")


def load_symbols(config: Config) -> list[str]:
    """Universe symbols: curated instruments first, then staging, then source."""
    curated = config.curated_root / "instruments" / "part-merged.parquet"
    staging_glob = list(config.staging_root.glob("instruments/run_id=*/part-*.parquet"))
    if curated.exists():
        return pl.read_parquet(curated)["symbol"].to_list()
    if staging_glob:
        latest = max(staging_glob, key=lambda p: p.stat().st_mtime)
        return pl.read_parquet(latest)["symbol"].to_list()
    df = fetch_instruments(
        rate_limit=config.tdx_rate_limit_spec(), allow_mock=config.tdx_allow_mock
    )
    return df["symbol"].to_list()
