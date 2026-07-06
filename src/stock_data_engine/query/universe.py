"""Universe filtering for the query reader."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from stock_data_engine.config import Config
from stock_data_engine.domain.symbols import is_all_a_symbol, parse_symbol

EXCLUDED_STATUSES = frozenset({"st", "*st", "suspended"})


def _scan_parquet(root: Path, dataset: str) -> pl.DataFrame:
    direct = root / dataset
    if not direct.exists():
        return pl.DataFrame()
    files = list(direct.glob("**/*.parquet"))
    if not files:
        return pl.DataFrame()
    return pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")


def coverage_start_date(
    config: Config,
    dataset: str,
    *,
    date_col: str = "trade_date",
) -> date | None:
    """Earliest *date_col* present in curated *dataset*, if any."""
    root = config.curated_root / dataset
    if not root.exists():
        return None

    prefix = f"{date_col}="
    min_dt: date | None = None
    for entry in root.iterdir():
        if entry.is_dir() and entry.name.startswith(prefix):
            try:
                candidate = date.fromisoformat(entry.name[len(prefix) :])
            except ValueError:
                continue
            if min_dt is None or candidate < min_dt:
                min_dt = candidate
    if min_dt is not None:
        return min_dt

    files = list(root.glob("**/*.parquet"))
    if not files:
        return None
    combined = pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")
    if date_col not in combined.columns or combined.is_empty():
        return None
    return combined[date_col].min()


def trading_status_coverage_start(config: Config) -> date | None:
    """First trade_date with curated trading_status rows."""
    return coverage_start_date(config, "trading_status")


def tradable_symbols_on_date(
    config: Config,
    trade_date: date,
    *,
    universe: str = "all_a",
) -> pl.DataFrame | None:
    """Return ``symbol`` rows tradable on *trade_date* for the given universe rule.

    Applies list/delist dates from ``instruments``. ST/suspended filtering uses
    ``trading_status`` only when rows exist for *trade_date*; dates before
    :func:`trading_status_coverage_start` are not ST-filtered.
    """
    if universe != "all_a":
        raise ValueError(f"unsupported universe: {universe!r} (supported: 'all_a')")

    instruments = _scan_parquet(config.curated_root, "instruments")
    if instruments.is_empty():
        return None

    rows = []
    for row in instruments.iter_rows(named=True):
        sym = row["symbol"]
        try:
            info = parse_symbol(sym)
        except ValueError:
            continue
        if not is_all_a_symbol(info.code, info.exchange):
            continue
        list_date = row.get("list_date")
        delist_date = row.get("delist_date")
        if list_date is not None and list_date > trade_date:
            continue
        if delist_date is not None and delist_date < trade_date:
            continue
        rows.append({"symbol": sym})

    if not rows:
        return pl.DataFrame(schema={"symbol": pl.Utf8})

    out = pl.DataFrame(rows)
    status = _scan_parquet(config.curated_root, "trading_status")
    if status.is_empty():
        return out

    day_status = status.filter(pl.col("trade_date") == trade_date)
    if day_status.is_empty():
        return out

    bad = day_status.filter(
        (~pl.col("is_trading")) | pl.col("status").is_in(list(EXCLUDED_STATUSES))
    )["symbol"].to_list()
    if bad:
        out = out.filter(~pl.col("symbol").is_in(bad))
    return out


def apply_universe_filter(
    df: pl.DataFrame,
    config: Config,
    *,
    universe: str,
    date_col: str = "trade_date",
) -> pl.DataFrame:
    """Filter bar-like frames to tradable universe rows per *date_col*.

    ``instruments`` list/delist rules always apply. ST/suspended removal via
    ``trading_status`` only affects dates with status rows; earlier history
    passes through unchanged (see :func:`trading_status_coverage_start`).
    """
    if df.is_empty() or universe != "all_a":
        return df

    instruments = _scan_parquet(config.curated_root, "instruments")
    status = _scan_parquet(config.curated_root, "trading_status")

    if instruments.is_empty():
        return df

    inst = instruments.select(["symbol", "list_date", "delist_date"])
    df = df.join(inst, on="symbol", how="left")
    df = df.filter(
        pl.col("list_date").is_null() | (pl.col("list_date") <= pl.col(date_col))
    ).filter(
        pl.col("delist_date").is_null() | (pl.col("delist_date") >= pl.col(date_col))
    )

    valid_symbols = []
    for row in instruments.iter_rows(named=True):
        try:
            info = parse_symbol(row["symbol"])
        except ValueError:
            continue
        if is_all_a_symbol(info.code, info.exchange):
            valid_symbols.append(row["symbol"])
    if valid_symbols:
        df = df.filter(pl.col("symbol").is_in(valid_symbols))

    if status.is_empty() or date_col not in df.columns:
        return df.drop(["list_date", "delist_date"], strict=False)

    bad = status.filter(
        (~pl.col("is_trading")) | pl.col("status").is_in(list(EXCLUDED_STATUSES))
    ).select(["symbol", pl.col("trade_date").alias(date_col)])
    if bad.is_empty():
        return df.drop(["list_date", "delist_date"], strict=False)

    df = df.join(bad, on=["symbol", date_col], how="anti")
    return df.drop(["list_date", "delist_date"], strict=False)
