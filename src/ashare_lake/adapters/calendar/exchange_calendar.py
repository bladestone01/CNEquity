"""Exchange trading calendar: bundled seed CSV + index-bars derivation fallback."""

from __future__ import annotations

import csv
import logging
from datetime import date, timedelta
from importlib import resources
from pathlib import Path

import polars as pl

from ashare_lake.adapters.calendar.holidays_cn import CLOSED_DATES, EXTRA_TRADING_DATES

logger = logging.getLogger(__name__)

_SEED_START = date(2016, 1, 1)
_SEED_END = date(2027, 12, 31)
CALENDAR_FORWARD_COVERAGE_WARN_DAYS = 90


def calendar_seed_end() -> date:
    """Last calendar date covered by bundled holiday seed data."""
    return _SEED_END


def calendar_forward_coverage_days(as_of: date) -> int:
    """Days from *as_of* (inclusive) through the bundled seed end date."""
    return (_SEED_END - as_of).days


def _is_trading_day(d: date) -> bool:
    iso = d.isoformat()
    if iso in EXTRA_TRADING_DATES:
        return True
    if d.weekday() >= 5:
        return False
    return iso not in CLOSED_DATES


def _generate_seed_rows(start: date, end: date) -> list[tuple[str, bool]]:
    rows: list[tuple[str, bool]] = []
    d = start
    while d <= end:
        rows.append((d.isoformat(), _is_trading_day(d)))
        d += timedelta(days=1)
    return rows


def _default_seed_path() -> Path:
    return Path(
        resources.files("ashare_lake.adapters.calendar") / "seeds" / "trading_calendar.csv"
    )


def ensure_seed_csv(path: Path | None = None) -> Path:
    """Write bundled seed CSV if missing (idempotent)."""
    target = path or _default_seed_path()
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["trade_date", "is_trading"])
        writer.writerows(_generate_seed_rows(_SEED_START, _SEED_END))
    return target


def load_seed_calendar(path: Path | None = None) -> pl.DataFrame:
    seed_path = path or ensure_seed_csv()
    if not seed_path.exists():
        raise FileNotFoundError(f"trading calendar seed not found: {seed_path}")
    df = pl.read_csv(
        seed_path,
        schema={"trade_date": pl.Utf8, "is_trading": pl.Boolean},
    )
    return df.with_columns(pl.col("trade_date").str.to_date(strict=False))


def _trading_days_from_index_bars(curated_root: Path) -> set[date]:
    bars_root = curated_root / "index_bars"
    if not bars_root.exists():
        return set()
    files = list(bars_root.glob("**/*.parquet"))
    if not files:
        return set()
    frames = [pl.read_parquet(f).select("trade_date") for f in files]
    combined = pl.concat(frames, how="diagonal_relaxed")
    return set(combined["trade_date"].to_list())


def build_trading_calendar(
    start: date,
    end: date,
    *,
    seed_path: Path | None = None,
    curated_root: Path | None = None,
) -> pl.DataFrame:
    """Return calendar rows for [start, end] from seed, extended by index bars.

    The seed is authoritative for every date it covers: index-bars derivation
    only fills dates outside the seed's range (e.g. future dates beyond the
    bundled holiday schedule). This prevents a spurious index_bars row from
    flipping a seed ``is_trading=False`` (a known holiday) to ``True``.
    """
    seed = load_seed_calendar(seed_path)
    seed = seed.filter((pl.col("trade_date") >= start) & (pl.col("trade_date") <= end))

    seed_dates = set(seed["trade_date"].to_list())
    index_trading_days: set[date] = set()
    if curated_root is not None:
        # Only consider index-bars-derived trading days for dates the seed
        # does not cover; within the seed range the seed wins outright.
        index_trading_days = _trading_days_from_index_bars(curated_root) - seed_dates

    rows: list[dict] = []
    d = start
    while d <= end:
        in_seed = seed.filter(pl.col("trade_date") == d)
        if not in_seed.is_empty():
            is_trading = bool(in_seed["is_trading"][0])
        elif d in index_trading_days:
            is_trading = True
        else:
            is_trading = _is_trading_day(d)
        rows.append({"trade_date": d, "is_trading": is_trading})
        d += timedelta(days=1)

    return pl.DataFrame(rows).sort("trade_date")
