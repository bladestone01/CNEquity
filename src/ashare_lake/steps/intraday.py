"""Intraday steps: minute_bars.

Kept out of ``steps/bars.py`` because it shares almost nothing with the daily
path — different horizon, different scope, different schedule — and because a
reader looking for what runs on the daily waves should not have to skip past a
step that never does.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import polars as pl

from ashare_lake.adapters.tdx_protocol.client import fetch_minute_bars, normalize_with_source
from ashare_lake.adapters.tdx_protocol.minute_bars import FREQUENCIES, pages_for_window
from ashare_lake.config import Config
from ashare_lake.domain.datasets import get_dataset
from ashare_lake.orchestrator.registry import register_step
from ashare_lake.steps.common import incremental_window, load_symbols
from ashare_lake.storage import StagingWriter

logger = logging.getLogger(__name__)

# Symbols per staged batch. Small enough that a killed backfill loses minutes
# rather than hours, large enough that the parquet footers stay negligible.
_BATCH_SYMBOLS = 50


class MinuteBarsScopeError(RuntimeError):
    """Raised when the configured scope cannot be resolved to symbols."""


def _index_members(config: Config, index_symbol: str) -> list[str]:
    """Latest known constituents of *index_symbol* from ``index_constituents``."""
    from ashare_lake.query.parquet_scan import dataset_has_parquet, parquet_glob

    root = config.curated_root / "index_constituents"
    if not dataset_has_parquet(root):
        raise MinuteBarsScopeError(
            f"minute_bars scope 'index:{index_symbol}' needs the index_constituents "
            "dataset, which is empty — run `asl run daily` (or `asl backfill "
            "index_constituents`) first, or set [minute_bars].scope = 'watchlist'"
        )
    df = (
        pl.scan_parquet(parquet_glob(root))
        .filter(pl.col("index_symbol") == index_symbol)
        .select("symbol", "as_of_date")
        .collect()
    )
    if df.is_empty():
        raise MinuteBarsScopeError(
            f"index_constituents holds no rows for {index_symbol!r}; "
            "check the index symbol or pick another scope"
        )
    latest = df["as_of_date"].max()
    return sorted(df.filter(pl.col("as_of_date") == latest)["symbol"].unique().to_list())


def resolve_scope(config: Config) -> list[str]:
    """Symbols the intraday capture covers, per ``[minute_bars].scope``.

    ``index:<symbol>`` — that index's latest constituents (the default;
    沪深300 is ~300 names, about 2MB a day at 1m).
    ``watchlist`` — exactly ``[minute_bars].symbols``.
    ``all`` — the whole universe. ~1.3M rows and ~30MB a day; opt in knowingly.
    """
    scope = (config.minute_bars_scope or "").strip()
    if scope == "all":
        # BJ has no TDX intraday route at all, so it would be all failures.
        return [s for s in load_symbols(config) if not s.endswith(".BJ")]
    if scope == "watchlist":
        symbols = [s.strip() for s in config.minute_bars_symbols if s.strip()]
        if not symbols:
            raise MinuteBarsScopeError(
                "[minute_bars].scope = 'watchlist' but [minute_bars].symbols is empty"
            )
        return symbols
    if scope.startswith("index:"):
        return _index_members(config, scope.split(":", 1)[1].strip())
    raise MinuteBarsScopeError(
        f"unknown [minute_bars].scope {scope!r} (expected 'all', 'watchlist', or 'index:<symbol>')"
    )


def horizon_start(config: Config, today: date) -> date | None:
    """Earliest date the source still serves, or None when unbounded."""
    return get_dataset("minute_bars").earliest_available(today)


def _window(config: Config, trade_date: date) -> tuple[date, date]:
    """Fetch window, clamped to the source's retention horizon.

    Clamping rather than failing: a first run legitimately asks for more than
    the source has, and the honest answer is "here is everything that exists",
    with the clamp logged so it is not mistaken for complete history.
    """
    if getattr(config, "_backfill", False):
        end = getattr(config, "_backfill_end", None) or trade_date
        start = getattr(config, "_backfill_start", None) or (end - timedelta(days=365))
    else:
        start = incremental_window(config, "minute_bars", trade_date)
        end = trade_date

    earliest = horizon_start(config, trade_date)
    if earliest is not None and start < earliest:
        logger.warning(
            "minute_bars: requested start %s is older than the source horizon "
            "(~%s, %d trading days); clamping to %s",
            start,
            earliest,
            get_dataset("minute_bars").history_horizon_days,
            earliest,
        )
        start = earliest
    return start, min(end, trade_date)


@register_step(
    "minute_bars",
    group="intraday",
    depends_on=["instruments"],
)
def step_minute_bars(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    """Capture intraday bars for the configured scope.

    Never on the default daily waves. Full-market 1m is ~30MB a day and would
    change what `asl init` costs a user who never asked for it, so this runs
    only when a config opts in and only over the scope that config names.
    """
    if not config.minute_bars_enabled:
        return {
            "rows_read": 0,
            "rows_written": 0,
            "note": "minute_bars disabled ([minute_bars].enabled = false)",
        }

    frequency = config.minute_bars_frequency
    if frequency not in FREQUENCIES:
        raise ValueError(
            f"[minute_bars].frequency {frequency!r} is not supported "
            f"(known: {', '.join(FREQUENCIES)})"
        )

    symbols = resolve_scope(config)
    start, end = _window(config, trade_date)
    if start > end:
        return {"rows_read": 0, "rows_written": 0, "note": f"empty window {start}..{end}"}

    # Bound the page walk: without it, every symbol is paged back to its full
    # retention depth and the extra pages are then discarded by the window
    # filter — 29 requests where 1 would do on the daily path.
    trading_days = max(1, _approx_trading_days(config, start, end))
    max_pages = pages_for_window(frequency, trading_days)

    logger.info(
        "minute_bars: %d symbol(s) %s, %s..%s (~%d trading days, ≤%d page(s)/symbol)",
        len(symbols),
        frequency,
        start,
        end,
        trading_days,
        max_pages,
    )

    writer = StagingWriter(config.staging_root)
    rate_limit = config.tdx_rate_limit_spec()
    written = 0
    failed: list[str] = []

    for index in range(0, len(symbols), _BATCH_SYMBOLS):
        chunk = symbols[index : index + _BATCH_SYMBOLS]
        df, chunk_failed = fetch_minute_bars(
            chunk,
            start,
            end,
            frequency=frequency,
            rate_limit=rate_limit,
            backfill=getattr(config, "_backfill", False),
            config=config,
            max_pages=max_pages,
        )
        failed.extend(chunk_failed)
        if df.is_empty():
            continue
        df = normalize_with_source(df, dataset="minute_bars")
        writer.write_batch("minute_bars", run_id, f"minute-{index // _BATCH_SYMBOLS:04d}", df)
        written += df.height
        logger.info(
            "minute_bars: %d/%d symbols, %d rows staged",
            min(index + _BATCH_SYMBOLS, len(symbols)),
            len(symbols),
            written,
        )

    result: dict = {
        "rows_read": written,
        "rows_written": written,
        "symbols": len(symbols),
        "failed_symbols": len(failed),
        "note": f"{frequency} {start}..{end} scope={config.minute_bars_scope}",
    }
    if failed:
        result["context_updates"] = {
            "audit_findings": [
                {
                    "dataset": "minute_bars",
                    "severity": "warning",
                    "check": "minute_bars_symbol_fetch",
                    "message": (
                        f"{len(failed)}/{len(symbols)} symbol(s) returned no intraday "
                        f"bars for {start}..{end} (e.g. {', '.join(failed[:5])})"
                    ),
                }
            ]
        }
    if written == 0 and symbols:
        raise RuntimeError(
            f"minute_bars: no rows for any of {len(symbols)} symbol(s) over {start}..{end} "
            "— check TDX reachability and that the window is inside the source horizon"
        )
    return result


def _approx_trading_days(config: Config, start: date, end: date) -> int:
    """Trading days in [start, end] from the calendar, or a 5/7 estimate."""
    from ashare_lake.steps.common import _load_trading_calendar_df

    cal = _load_trading_calendar_df(config, start=start, end=end)
    if cal is not None and not cal.is_empty() and "is_trading" in cal.columns:
        return int(cal.filter(pl.col("is_trading")).height)
    return max(1, round((end - start).days * 5 / 7) + 1)
