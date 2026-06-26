from __future__ import annotations

import logging
from datetime import date

import httpx
import polars as pl

from stock_data_engine.adapters.sina.adj_factors import fetch_adj_factor_series
from stock_data_engine.config import Config
from stock_data_engine.domain.rate_limit import wait_source
from stock_data_engine.domain.schemas import with_provenance

logger = logging.getLogger(__name__)


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


def compute_adj_factors(config: Config, adjust_type: str | None = None) -> int:
    bars = _load_daily_bar_dates(config)
    if bars.is_empty():
        return 0

    adjust_types = [adjust_type] if adjust_type else list(config.adj_factors_types)
    source = config.adj_factors_source
    interval = config.source_intervals.get(source, 0.2)
    rate_state_dir = config.meta_root / "rate_limits"
    symbols = bars["symbol"].unique().to_list()

    frames: list[pl.DataFrame] = []
    with httpx.Client(timeout=20.0) as client:
        for sym in symbols:
            for adj in adjust_types:
                try:
                    wait_source(rate_state_dir, source, interval)
                    if source == "sina":
                        factors = fetch_adj_factor_series(sym, adj, client=client)
                    else:
                        logger.warning("Unknown adj_factors source %s; skipping %s", source, sym)
                        continue
                    aligned = _align_factors_to_bars(bars, sym, factors, adj)
                    if aligned.height:
                        frames.append(aligned)
                except Exception as exc:
                    logger.warning("External adj factors failed for %s (%s): %s", sym, adj, exc)

    if not frames:
        return 0

    out = pl.concat(frames, how="diagonal_relaxed")
    out = with_provenance(out, source=source, data_version="v1")

    total = 0
    for key, group in out.partition_by("trade_date", as_dict=True).items():
        td = key[0] if isinstance(key, tuple) else key
        td_str = td.isoformat() if isinstance(td, date) else str(td)
        out_dir = config.derived_root / "adj_factors" / f"trade_date={td_str}"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "part-0.parquet"
        group.write_parquet(path, compression="zstd")
        total += group.height
    return total
