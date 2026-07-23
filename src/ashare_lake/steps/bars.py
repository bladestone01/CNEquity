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

    _reject_preopen_placeholder(config, run_id, trade_date)
    return result


# A bar captured before the session opens is the previous close stamped on every
# field: open==high==low==close and zero volume. A handful of these on any day
# are genuine suspensions, but a whole universe of them means the fetch ran too
# early — 2026-07-22 arrived that way from a pre-open run. Below this share it is
# suspensions; at or above it, it is a mis-timed capture.
_PLACEHOLDER_SHARE_LIMIT = 0.5


def _reject_preopen_placeholder(config: Config, run_id: str, trade_date: date) -> None:
    """Fail the step if the freshest staged day is mostly pre-open placeholders.

    Checked against staging, before compact promotes anything, so a mis-timed
    run stays quarantined in staging instead of overwriting a good curated
    partition. `by_date` semantics mean the fix is simply to re-run after the
    close, which a failed step invites rather than hides.
    """
    import polars as pl

    from ashare_lake.storage import StagingWriter

    files = StagingWriter(config.staging_root).list_run_files("daily_bars", run_id)
    if not files:
        return
    df = (
        pl.scan_parquet([str(f) for f in files])
        .filter(pl.col("trade_date") == trade_date)
        .select("open", "high", "low", "close", "volume")
        .collect()
    )
    if df.is_empty():
        return
    placeholder = df.filter(
        (pl.col("open") == pl.col("close"))
        & (pl.col("high") == pl.col("low"))
        & (pl.col("open") == pl.col("high"))
        & (pl.col("volume") == 0)
    ).height
    share = placeholder / df.height
    if share >= _PLACEHOLDER_SHARE_LIMIT:
        raise RuntimeError(
            f"daily_bars {trade_date}: {placeholder}/{df.height} rows "
            f"({share:.0%}) are pre-open placeholders (OHLC flat, zero volume) — "
            "the capture ran before the close. Re-run after the session closes."
        )


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


# The primary vendor serves 2016 onward; 同花顺 keeps per-year files back to each
# listing. Deep history is a separate step, not a wider window on the daily one:
# it uses a different source, runs for hours, and must never be on the daily path.
HISTORY_BACKFILL_START = date(2001, 1, 1)


@register_step(
    "daily_bars_history",
    group="backfill",
    depends_on=["instruments"],
)
def step_daily_bars_history(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    """Backfill pre-2016 unadjusted daily bars from 同花顺.

    Writes into ``daily_bars`` like the daily step, so `compact` and every reader
    treat the older rows identically. Only raw prices are fetched — hfq stays
    derived from the Sina factors already in use, which reach back to listing, so
    one adjustment convention spans the whole series (verified continuous across
    the 2015→2016 seam at 0.0bps).
    """
    import polars as pl

    from ashare_lake.domain.schemas import with_provenance
    from ashare_lake.storage import StagingWriter

    start = getattr(config, "_backfill_start", None) or HISTORY_BACKFILL_START
    end = getattr(config, "_backfill_end", None) or date(2015, 12, 31)
    plan = _history_plan(config, start, end)
    resume = set(context.get("_history_done") or [])
    if resume:
        plan = [p for p in plan if p[0] not in resume]

    requests = sum((end.year - s.year + 1) for _, s in plan)
    logger.info(
        "daily_bars_history: %d symbols, %s..%s, ~%d year-requests "
        "(ETF and 北交所 excluded — neither has adjustment factors)",
        len(plan),
        start,
        end,
        requests,
    )
    writer = StagingWriter(config.staging_root)
    written = 0
    batch_no = 0

    def _flush(rows: list[dict], done: list[str]) -> None:
        nonlocal written, batch_no
        if not rows:
            return
        batch_no += 1
        df = with_provenance(pl.DataFrame(rows), source="ths", data_version="v1")
        writer.write_batch("daily_bars", run_id, f"history-{batch_no:04d}", df)
        written += df.height
        logger.info(
            "daily_bars_history: batch %d — %d rows, %d symbols", batch_no, df.height, len(done)
        )

    failed = sweep_stock_bars_planned(plan, end, config=config, on_batch=_flush)
    return {
        "rows_read": written,
        "rows_written": written,
        "symbols": len(plan),
        "failed_symbols": len(failed),
        "note": f"{start}..{end} via 同花顺 (raw only; hfq derives from Sina factors)",
    }


def _history_plan(config: Config, start: date, end: date) -> list[tuple[str, date]]:
    """``[(symbol, fetch_start)]`` for the symbols worth fetching.

    Two filters and a per-symbol window, which together cut the sweep by ~78%:

    * Stocks only. ETFs dominate the symbols with no ``list_date`` (2189 of
      2195) and have no adjustment factors, so deeper raw bars for them could
      never be served as hfq — fetching them would spend hours on data the
      research path must refuse anyway. 北交所 is excluded for the same reason.
    * Nothing listed after the window. A 2016 IPO has no pre-2016 history, and
      asking for it is ~2600 symbols' worth of empty year files.
    * The rest start at their listing year rather than at ``start``.
    """
    import glob

    import polars as pl

    symbols = [s for s in load_symbols(config) if not s.startswith("92")]
    files = glob.glob(f"{config.curated_root}/instruments/**/*.parquet", recursive=True)
    if not files:
        # No instruments to plan against: fall back to the full window rather
        # than silently fetching nothing.
        return [(s, start) for s in symbols]
    inst = pl.read_parquet(files).select("symbol", "list_date", "asset_type")
    meta = {r["symbol"]: r for r in inst.to_dicts()}

    plan: list[tuple[str, date]] = []
    for sym in symbols:
        row = meta.get(sym)
        if row is None or row.get("asset_type") != "stock":
            continue
        listed = row.get("list_date")
        if listed is not None:
            if listed > end:
                continue
            if listed > start:
                plan.append((sym, date(listed.year, 1, 1)))
                continue
        plan.append((sym, start))
    return plan


def sweep_stock_bars_planned(
    plan: list[tuple[str, date]],
    end: date,
    *,
    config: Config,
    on_batch,
    batch_size: int = 50,
) -> list[str]:
    """Sweep a per-symbol plan, batching writes. Returns failed symbols."""
    from ashare_lake.adapters.ths.stock_bars import fetch_stock_bars

    rows: list[dict] = []
    batch: list[str] = []
    failed: list[str] = []
    streak = 0
    for i, (symbol, sym_start) in enumerate(plan, start=1):
        try:
            rows.extend(fetch_stock_bars(symbol, sym_start, end, config=config))
            batch.append(symbol)
            streak = 0
        except Exception as exc:  # noqa: BLE001 — recorded, sweep continues
            logger.warning("THS history failed for %s: %s", symbol, exc)
            failed.append(symbol)
            streak += 1
            if streak >= 10:
                logger.error("THS: %d consecutive failures at %s — aborting", streak, symbol)
                break
        if i % batch_size == 0 or i == len(plan):
            on_batch(rows, batch)
            rows, batch = [], []
    if batch:
        on_batch(rows, batch)
    return failed


# Rosters are sampled rather than walked day by day: a stock that traded at all
# appears on some quarter-end, and 40 roster queries beat 2,500.
_ROSTER_SAMPLE_MONTHS = (3, 6, 9, 12)


def _delisted_universe(config: Config, start: date, end: date) -> list[str]:
    """Symbols that traded in the window but hold no bars in the lake.

    Compares baostock's historical rosters against what daily_bars actually
    carries. Anything present then and absent now is a name the current-roster
    snapshot lost — the survivorship gap, 16.8% of the cross-section on
    2016-06-30 and still 6.0% on 2020-06-30.
    """
    import polars as pl

    from ashare_lake.adapters.baostock._session import _login, import_baostock
    from ashare_lake.adapters.baostock.delisted_bars import roster_on

    bars_root = config.curated_root / "daily_bars"
    have = set(
        pl.scan_parquet(str(bars_root / "**" / "*.parquet"))
        .select("symbol")
        .unique()
        .collect()["symbol"]
        .to_list()
    )

    bs = import_baostock()
    _login(bs)
    missing: set[str] = set()
    try:
        for year in range(start.year, end.year + 1):
            for month in _ROSTER_SAMPLE_MONTHS:
                day = date(year, month, 28)
                if not (start <= day <= end):
                    continue
                roster = roster_on(day, bs=bs, login=False)
                if not roster:
                    continue
                gap = roster - have
                if gap:
                    logger.info(
                        "roster %s: %d stocks, %d absent from daily_bars",
                        day,
                        len(roster),
                        len(gap),
                    )
                missing |= gap
    finally:
        bs.logout()
    return sorted(missing)


@register_step(
    "daily_bars_delisted",
    group="backfill",
    depends_on=["instruments"],
)
def step_daily_bars_delisted(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    """Recover bars for stocks that delisted inside the window.

    The live vendors serve only what currently trades, so this is the one path
    that can close the survivorship gap; baostock keeps each delisted name
    through its final session. Rows land in ``daily_bars`` like any other, and
    hfq keeps deriving from the Sina factors, which still cover these symbols.
    """
    import polars as pl

    from ashare_lake.adapters.baostock.delisted_bars import fetch_delisted_bars
    from ashare_lake.domain.schemas import with_provenance
    from ashare_lake.storage import StagingWriter

    start = getattr(config, "_backfill_start", None) or date(2016, 1, 1)
    end = getattr(config, "_backfill_end", None) or trade_date
    symbols = context.get("_delisted_symbols") or _delisted_universe(config, start, end)
    if not symbols:
        return {"rows_read": 0, "rows_written": 0, "note": "no survivorship gap found"}

    logger.info("daily_bars_delisted: %d recovered symbols, %s..%s", len(symbols), start, end)
    rows, failed = fetch_delisted_bars(symbols, start, end, config=config)
    written = 0
    if rows:
        df = with_provenance(pl.DataFrame(rows), source="baostock", data_version="v1")
        StagingWriter(config.staging_root).write_batch("daily_bars", run_id, "delisted-0000", df)
        written = df.height
    return {
        "rows_read": written,
        "rows_written": written,
        "symbols": len(symbols),
        "failed_symbols": len(failed),
        "note": f"survivorship repair {start}..{end} via baostock",
    }
