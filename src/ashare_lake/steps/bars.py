"""L1 bar steps: daily_bars, index_bars."""

from __future__ import annotations

import logging
from datetime import date

from ashare_lake.adapters.tdx_protocol.client import (
    fetch_index_bars,
    normalize_with_source,
)
from ashare_lake.config import Config
from ashare_lake.domain.symbols import split_by_quote_source
from ashare_lake.orchestrator.registry import register_step
from ashare_lake.orchestrator.worker_pool import fetch_daily_bars_parallel
from ashare_lake.steps.common import BACKFILL_START, incremental_window, load_symbols

logger = logging.getLogger(__name__)


def _backfill_window(config: Config, trade_date: date) -> tuple[date, date]:
    """``--start/--end`` window for a backfill, defaulting to the full history.

    Repairing a single bad session must not mean re-fetching a decade for every
    symbol. A capture that fires before the close writes a truncated bar — right
    open, wrong close, partial volume — and the repair is one day wide.
    """
    end = getattr(config, "_backfill_end", None) or trade_date
    start = getattr(config, "_backfill_start", None) or BACKFILL_START
    return start, end


@register_step(
    "daily_bars",
    group="core",
    depends_on=["instruments", "corporate_actions"],
    requires_workers=True,
)
def step_daily_bars(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    batch_specs = context.get("_retry_batch_specs")
    if batch_specs:
        return fetch_daily_bars_parallel(
            config,
            [],
            trade_date,
            trade_date,
            run_id,
            "daily_bars",
            batch_specs=batch_specs,
        )

    symbols = load_symbols(config)
    rebackfill = context.get("symbols_to_rebackfill") or []
    if rebackfill:
        symbols = list(dict.fromkeys(rebackfill + symbols))

    if getattr(config, "_backfill", False):
        start, end = _backfill_window(config, trade_date)
    else:
        start = incremental_window(config, "daily_bars", trade_date)
        end = trade_date

    # TDX has no Beijing exchange route at all — mootdx rejects the market id —
    # so BJ symbols must come from the fallback vendor or they silently never
    # arrive, which is exactly how the lake ended up with zero BJ coverage.
    tdx_symbols, fallback_symbols = split_by_quote_source(symbols)
    result = fetch_daily_bars_parallel(
        config,
        tdx_symbols,
        start,
        end,
        run_id,
        "daily_bars",
    )
    if fallback_symbols:
        fallback = fetch_bars_via_sina(
            config, fallback_symbols, start, end, run_id, batch_prefix="sina"
        )
        result = {
            "rows_read": result.get("rows_read", 0) + fallback.get("rows_read", 0),
            "rows_written": result.get("rows_written", 0) + fallback.get("rows_written", 0),
            **{k: v for k, v in fallback.items() if k not in ("rows_read", "rows_written")},
        }
    return result


def fetch_bars_via_sina(
    config: Config,
    symbols: list[str],
    start: date,
    end: date,
    run_id: str,
    *,
    batch_prefix: str = "sina",
    fetch=None,
) -> dict:
    """Stage daily bars for symbols the primary protocol cannot serve.

    Failures are collected rather than raised: one unreachable symbol must not
    cost the whole run its Beijing coverage. They surface as an audit finding so
    a persistent gap is visible instead of silently shrinking the universe.
    """
    import httpx
    import polars as pl

    from ashare_lake.adapters.sina.bars import fetch_daily_bars_sina
    from ashare_lake.steps.http_common import write_fetched

    fetch = fetch or (
        lambda symbol, client: fetch_daily_bars_sina(symbol, start=start, end=end, client=client)
    )
    frames: list[pl.DataFrame] = []
    failed: list[str] = []
    with httpx.Client(timeout=30.0) as client:
        for symbol in symbols:
            config.rate_limit("sina")
            try:
                bars = fetch(symbol, client)
            except Exception as exc:  # noqa: BLE001 — keep the rest of the board
                logger.warning("sina bars failed for %s: %s", symbol, exc)
                failed.append(symbol)
                continue
            if not bars.is_empty():
                frames.append(bars)

    rows = 0
    if frames:
        merged = pl.concat(frames, how="diagonal_relaxed")
        out = write_fetched(
            config, run_id, "daily_bars", merged, source="sina", batch_id=f"{batch_prefix}-0000"
        )
        rows = int(out.get("rows_written", 0))

    result: dict = {"rows_read": rows, "rows_written": rows}
    if failed:
        result["failed_symbols"] = len(failed)
        result["context_updates"] = {
            "audit_findings": [
                {
                    "dataset": "daily_bars",
                    "severity": "warning",
                    "check": "fallback_source_incomplete",
                    "message": (
                        f"{len(failed)}/{len(symbols)} symbols without a TDX route "
                        f"failed to fetch from the fallback vendor "
                        f"(e.g. {', '.join(failed[:5])})"
                    ),
                }
            ]
        }
    return result


@register_step("index_bars", group="core", depends_on=["instruments"])
def step_index_bars(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    if getattr(config, "_backfill", False):
        start, end = _backfill_window(config, trade_date)
    else:
        start = incremental_window(config, "index_bars", trade_date)
        end = trade_date
    rl = config.tdx_rate_limit_spec()
    df = fetch_index_bars(
        start,
        end,
        rate_limit=rl,
        allow_mock=config.tdx_allow_mock,
        backfill=getattr(config, "_backfill", False),
        config=config,
    )
    df = normalize_with_source(df)
    from ashare_lake.steps.common import write_simple

    return write_simple(config, run_id, "index_bars", df)
