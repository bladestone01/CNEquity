"""L3 fundamentals steps: valuation metrics, financial statement items."""

from __future__ import annotations

from datetime import date

from stock_data_engine.adapters.eastmoney.fundamentals import fetch_financial_statement_items
from stock_data_engine.adapters.eastmoney.valuation import fetch_valuation_metrics
from stock_data_engine.config import Config
from stock_data_engine.domain.symbols import is_all_a_symbol, parse_symbol
from stock_data_engine.orchestrator.registry import register_step
from stock_data_engine.steps.common import load_bar_universe, load_symbols
from stock_data_engine.steps.http_common import run_incremental_fetched, write_fetched

# EastMoney's valuation clist is a live snapshot only; history comes from baostock.
_VALUATION_BACKFILL_START = date(2016, 1, 1)
# Checkpoint every N symbols so a mid-sweep kill still keeps prior chunks in
# curated (resume via ``_symbols_needing_backfill`` / float_mv fill ratio).
_VALUATION_BACKFILL_CHUNK = 50


@register_step("valuation_metrics", group="capital", depends_on=["instruments"])
def step_valuation_metrics(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    if getattr(config, "_backfill", False):
        return _backfill_valuation_metrics(config, trade_date, run_id)
    if not config.sources.get("eastmoney", True):
        raise RuntimeError("valuation_metrics: eastmoney source disabled in config")
    # The EastMoney clist snapshot returns delisted / non-tradable names that
    # never have a price bar (audit: valuation_bars_orphan_symbol). Pin the daily
    # snapshot to the same universe daily_bars actually realises so PE/PB rows are
    # only written for symbols that trade.
    return run_incremental_fetched(
        config,
        trade_date,
        run_id,
        "valuation_metrics",
        lambda d: fetch_valuation_metrics(d, config=config),
        source="eastmoney",
        allow_empty=True,
        universe=load_bar_universe(config),
    )


def _backfill_valuation_metrics(config: Config, trade_date: date, run_id: str) -> dict:
    """Historical PE/PB/PS + market cap from baostock over all_a (2016 → today).

    Resumable: symbols that already have baostock rows *with* ``float_mv`` filled
    densely (≥80%) are skipped. Progress is written every
    ``_VALUATION_BACKFILL_CHUNK`` symbols so a mid-sweep kill still keeps prior
    chunks. Failures are surfaced as audit findings (fail-loud).
    """
    from stock_data_engine.adapters.baostock.valuation import fetch_valuation_history
    from stock_data_engine.storage.valuation_orphans import purge_valuation_orphan_symbols

    # Drop leftover PE/PB for names that never have bars (pre-filter backfills).
    purge_summary = purge_valuation_orphan_symbols(config)

    universe = [s for s in load_symbols(config) if _is_all_a(s)]
    # Only backfill symbols that actually have price bars: a delisted name still
    # sitting in the instruments list (e.g. 退市创兴) otherwise gets years of
    # baostock PE/PB with no bar to join against (audit: orphan symbol). Skip the
    # constraint on a bars-less lake so a first-time backfill still runs.
    bar_universe = load_bar_universe(config)
    if bar_universe:
        universe = [s for s in universe if s in bar_universe]
    todo = _symbols_needing_backfill(config, universe)
    if not todo:
        return {
            "rows_read": 0,
            "rows_written": 0,
            "note": "all symbols already backfilled",
            "orphan_purge": purge_summary,
        }

    rows_read = 0
    rows_written = 0
    all_failed: list[str] = []
    for offset in range(0, len(todo), _VALUATION_BACKFILL_CHUNK):
        batch = todo[offset : offset + _VALUATION_BACKFILL_CHUNK]
        df, failed = fetch_valuation_history(
            batch, _VALUATION_BACKFILL_START, trade_date, config=config
        )
        all_failed.extend(failed)
        if not df.is_empty():
            # Unique part name per chunk — write_simple's default batch-0 would
            # overwrite prior chunks in the same run_id before compact.
            chunk = write_fetched(
                config,
                run_id,
                "valuation_metrics",
                df,
                source="baostock",
                batch_id=f"batch-{offset:05d}",
            )
            rows_read += int(chunk.get("rows_read", 0))
            rows_written += int(chunk.get("rows_written", 0))

    result: dict = {
        "rows_read": rows_read,
        "rows_written": rows_written,
        "orphan_purge": purge_summary,
        "symbols_todo": len(todo),
    }
    if all_failed:
        result["failed_symbols"] = len(all_failed)
        finding = {
            "dataset": "valuation_metrics",
            "severity": "warning",
            "code": "baostock_backfill_incomplete",
            "message": (
                f"{len(all_failed)}/{len(todo)} symbols failed baostock backfill "
                f"(throttled/dropped); re-run `sde backfill valuation_metrics` to resume."
            ),
        }
        result.setdefault("context_updates", {})["audit_findings"] = [finding]
    return result


# Require dense MV coverage before skipping a symbol — a single non-null day
# must not mark a decade of null float_mv/total_mv as "done".
_MV_FILL_DONE_RATIO = 0.80


def _symbols_needing_backfill(config: Config, universe: list[str]) -> list[str]:
    """Symbols missing baostock history, or with sparse market-cap fill."""
    import polars as pl

    part = config.curated_root / "valuation_metrics"
    files = list(part.glob("**/*.parquet")) if part.exists() else []
    if not files:
        return universe
    stats = (
        pl.scan_parquet(files)
        .filter(pl.col("source") == "baostock")
        .group_by("symbol")
        .agg(
            pl.len().alias("n"),
            pl.col("float_mv").null_count().alias("float_nulls"),
        )
        .collect()
    )
    # Done when ≥80% of baostock rows have float_mv (MV fill landed densely).
    done = set(
        stats.filter(
            (pl.col("n") > 0)
            & ((pl.col("n") - pl.col("float_nulls")) / pl.col("n") >= _MV_FILL_DONE_RATIO)
        )
        .get_column("symbol")
        .to_list()
    )
    return [s for s in universe if s not in done]


def _is_all_a(symbol: str) -> bool:
    try:
        info = parse_symbol(symbol)
    except ValueError:
        return False
    return is_all_a_symbol(info.code, info.exchange)


@register_step("financial_statement_items", group="fundamentals", depends_on=["instruments"])
def step_financial_statement_items(
    config: Config, trade_date: date, run_id: str, context: dict
) -> dict:
    if not config.sources.get("eastmoney", True):
        raise RuntimeError("financial_statement_items: eastmoney source disabled in config")
    # Quarterly data: daily runs pick up same-day announcements; backfill walks
    # every report period 2016+ (NOTICE_DATE incremental cannot reach history).
    backfill = getattr(config, "_backfill", False)
    df = fetch_financial_statement_items(trade_date, backfill=backfill, config=config)
    if df.is_empty():
        return {"rows_read": 0, "rows_written": 0}
    return write_fetched(config, run_id, "financial_statement_items", df, source="eastmoney")
