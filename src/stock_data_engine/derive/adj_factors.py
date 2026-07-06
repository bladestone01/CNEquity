from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import httpx
import polars as pl

from stock_data_engine.adapters.sina.adj_factors import fetch_adj_factor_series
from stock_data_engine.config import Config
from stock_data_engine.domain.rate_limit import wait_source
from stock_data_engine.domain.schemas import with_provenance
from stock_data_engine.storage.atomic import write_parquet_atomic

logger = logging.getLogger(__name__)


class AdjFactorsFetchError(RuntimeError):
    """Raised when adj factor fetch fails and no cache is available."""


def _load_daily_bar_dates(config: Config) -> pl.DataFrame:
    bars_path = config.curated_root / "daily_bars"
    if not bars_path.exists():
        return pl.DataFrame(schema={"symbol": pl.Utf8, "trade_date": pl.Date})

    bar_files = list(bars_path.glob("**/*.parquet"))
    if not bar_files:
        return pl.DataFrame(schema={"symbol": pl.Utf8, "trade_date": pl.Date})

    bars = pl.concat([pl.read_parquet(f) for f in bar_files], how="diagonal_relaxed")
    return bars.select(["symbol", "trade_date"]).unique().sort(["symbol", "trade_date"])


def _align_factors_to_bars(
    bars: pl.DataFrame, symbol: str, factors: pl.DataFrame, adjust_type: str
) -> pl.DataFrame:
    sym_bars = bars.filter(pl.col("symbol") == symbol).select("trade_date").sort("trade_date")
    if sym_bars.is_empty():
        return pl.DataFrame()

    aligned = sym_bars.join(factors, on="trade_date", how="left").sort("trade_date")
    aligned = aligned.with_columns(pl.col("factor").forward_fill().fill_null(1.0))
    return aligned.with_columns(
        pl.lit(symbol).alias("symbol"),
        pl.lit(adjust_type).alias("adjust_type"),
    )


def _cache_path(config: Config, symbol: str, adjust_type: str) -> Path:
    cache_dir = config.meta_root / "adj_factors_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe = symbol.replace(".", "_")
    return cache_dir / f"{safe}_{adjust_type}.parquet"


def _load_cache(config: Config, symbol: str, adjust_type: str) -> pl.DataFrame | None:
    path = _cache_path(config, symbol, adjust_type)
    if not path.exists():
        return None
    return pl.read_parquet(path).select(["trade_date", "factor"])


def _save_cache(config: Config, symbol: str, adjust_type: str, factors: pl.DataFrame) -> None:
    if factors.is_empty():
        return
    path = _cache_path(config, symbol, adjust_type)
    factors.write_parquet(path, compression="zstd")


def _needs_refresh(
    sym_bars: pl.DataFrame,
    cached: pl.DataFrame | None,
    force: bool,
) -> bool:
    if force:
        return True
    if cached is None or cached.is_empty():
        return True
    return sym_bars["trade_date"].max() > cached["trade_date"].max()


def _resolve_factors(
    config: Config,
    symbol: str,
    adjust_type: str,
    sym_bars: pl.DataFrame,
    *,
    force: bool,
    client: httpx.Client,
) -> pl.DataFrame | None:
    cached = _load_cache(config, symbol, adjust_type)
    if not _needs_refresh(sym_bars, cached, force):
        return cached

    source = config.adj_factors_source
    interval = config.source_intervals.get(source, 0.2)
    rate_state_dir = config.meta_root / "rate_limits"
    try:
        wait_source(rate_state_dir, source, interval)
        if source != "sina":
            logger.warning("Unknown adj_factors source %s; skipping %s", source, symbol)
            return cached
        factors = fetch_adj_factor_series(symbol, adjust_type, client=client)
        _save_cache(config, symbol, adjust_type, factors)
        return factors
    except Exception as exc:
        if cached is None or cached.is_empty():
            logger.warning(
                "No adj factors for %s (%s): %s; using default factor=1.0",
                symbol,
                adjust_type,
                exc,
            )
            return pl.DataFrame(schema={"trade_date": pl.Date, "factor": pl.Float64})
        logger.warning("External adj factors failed for %s (%s): %s", symbol, adjust_type, exc)
        return cached


def compute_adj_factors(
    config: Config,
    adjust_type: str | None = None,
    *,
    refresh_symbols: list[str] | None = None,
) -> int:
    bars = _load_daily_bar_dates(config)
    if bars.is_empty():
        return 0

    adjust_types = [adjust_type] if adjust_type else list(config.adj_factors_types)
    refresh_set = set(refresh_symbols or [])
    symbols = bars["symbol"].unique().to_list()
    workers = max(1, min(config.workers, 16))

    tasks = [
        (sym, adj, bars.filter(pl.col("symbol") == sym), sym in refresh_set)
        for sym in symbols
        for adj in adjust_types
    ]

    frames: list[pl.DataFrame] = []

    def _align_task(args: tuple) -> pl.DataFrame | None:
        sym, adj, sym_bars, force = args
        with httpx.Client(timeout=20.0) as client:
            factors = _resolve_factors(config, sym, adj, sym_bars, force=force, client=client)
        if factors is None or factors.is_empty():
            return None
        aligned = _align_factors_to_bars(bars, sym, factors, adj)
        return aligned if aligned.height else None

    if workers <= 1 or len(tasks) == 1:
        with httpx.Client(timeout=20.0) as client:
            for sym, adj, sym_bars, force in tasks:
                factors = _resolve_factors(config, sym, adj, sym_bars, force=force, client=client)
                if factors is None:
                    continue
                aligned = _align_factors_to_bars(bars, sym, factors, adj)
                if aligned.height:
                    frames.append(aligned)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_align_task, t) for t in tasks]
            for fut in as_completed(futures):
                aligned = fut.result()
                if aligned is not None and aligned.height:
                    frames.append(aligned)

    if not frames:
        return 0

    out = pl.concat(frames, how="diagonal_relaxed").unique(
        subset=["symbol", "trade_date", "adjust_type"], keep="last"
    )
    out = with_provenance(out, source=config.adj_factors_source, data_version="v1")

    total = 0
    for key, group in out.partition_by("trade_date", as_dict=True).items():
        td = key[0] if isinstance(key, tuple) else key
        td_str = td.isoformat() if isinstance(td, date) else str(td)
        out_dir = config.derived_root / "adj_factors" / f"trade_date={td_str}"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "part-0.parquet"
        write_parquet_atomic(path, group, compression="zstd")
        total += group.height
    return total
