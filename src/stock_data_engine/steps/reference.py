"""L0 reference steps: instruments, trading_calendar, trading_status."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from datetime import date, timedelta

from stock_data_engine.adapters.eastmoney.instruments import enrich_instrument_list_dates
from stock_data_engine.adapters.tdx_protocol.client import (
    fetch_instruments,
    fetch_trading_calendar,
    fetch_trading_status,
    normalize_with_source,
)
from stock_data_engine.config import Config
from stock_data_engine.domain.schemas import with_provenance
from stock_data_engine.domain.symbols import is_all_a_symbol, parse_symbol
from stock_data_engine.orchestrator.registry import register_step
from stock_data_engine.steps.common import (
    BACKFILL_START,
    fetch_incremental_daily,
    load_bar_universe,
    load_symbols,
    write_simple,
)
from stock_data_engine.steps.http_common import write_fetched

logger = logging.getLogger(__name__)

# Resume marker for the ST-history backfill: which symbols baostock has already
# been swept for. Needed because ~85% of names are never ST and so contribute no
# rows — data-presence alone (as valuation uses) cannot tell "done" from "todo".
_ST_BACKFILL_STATE = "trading_status_st_backfill"
# Flush + checkpoint every chunk so a mid-sweep baostock login death does not
# discard hours of already-fetched ST rows (observed ~2950/5204 lost on one run).
_ST_BACKFILL_CHUNK = 200


@register_step("instruments", group="core", requires_workers=False)
def step_instruments(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    rl = config.tdx_rate_limit_spec()
    df = fetch_instruments(rate_limit=rl, allow_mock=config.tdx_allow_mock, config=config)
    df = normalize_with_source(df)
    df = enrich_instrument_list_dates(config, df)
    return write_simple(config, run_id, "instruments", df)


@register_step("trading_calendar", group="core")
def step_trading_calendar(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    if getattr(config, "_backfill", False):
        start = BACKFILL_START
    else:
        start = trade_date - timedelta(days=30)
    end = trade_date + timedelta(days=365)
    rl = config.tdx_rate_limit_spec()
    seed_path = config.meta_root / "seeds" / "trading_calendar.csv"
    df = fetch_trading_calendar(
        start,
        end,
        rate_limit=rl,
        allow_mock=config.tdx_allow_mock,
        curated_root=config.curated_root,
        seed_path=seed_path if seed_path.exists() else None,
    )
    df = normalize_with_source(df, source="exchange_calendar")
    return write_simple(config, run_id, "trading_calendar", df)


@register_step("trading_status", group="core")
def step_trading_status(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    if getattr(config, "_backfill", False):
        return _backfill_trading_status_st(config, trade_date, run_id)

    symbols = context.get("symbols") or load_symbols(config)
    rl = config.tdx_rate_limit_spec()

    # Supplement EM's ST list with AKShare's when enabled (robustness).
    extra_st: set[str] = set()
    if config.sources.get("akshare", False):
        from stock_data_engine.adapters.akshare.trading_status import fetch_st_symbols_akshare

        extra_st = fetch_st_symbols_akshare(config=config)

    def _fetch(day: date):
        return fetch_trading_status(
            symbols,
            day,
            rate_limit=rl,
            allow_mock=config.tdx_allow_mock,
            extra_st_symbols=extra_st,
        )

    df, _findings = fetch_incremental_daily(
        config,
        "trading_status",
        trade_date,
        _fetch,
        allow_empty=True,
    )
    if df.is_empty():
        return {"rows_read": 0, "rows_written": 0}
    if "source" not in df.columns:
        df = normalize_with_source(df)
    else:
        df = with_provenance(df, source="eastmoney", data_version="v1")
    return write_simple(config, run_id, "trading_status", df)


def _is_all_a(symbol: str) -> bool:
    try:
        info = parse_symbol(symbol)
    except ValueError:
        return False
    return is_all_a_symbol(info.code, info.exchange)


def _st_backfill_state_path(config: Config):
    return config.meta_root / "state" / f"{_ST_BACKFILL_STATE}.json"


def _st_backfilled_symbols(config: Config) -> set[str]:
    path = _st_backfill_state_path(config)
    if not path.exists():
        return set()
    return set(json.loads(path.read_text(encoding="utf-8")).get("completed", []))


def _mark_st_backfilled(config: Config, symbols: list[str]) -> None:
    """Atomically add ``symbols`` to the swept-symbol set (resume marker)."""
    path = _st_backfill_state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    completed = sorted(_st_backfilled_symbols(config) | set(symbols))
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"completed": completed}, handle, indent=2)
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _backfill_trading_status_st(config: Config, trade_date: date, run_id: str) -> dict:
    """Historical ST labels from baostock over the all_a universe (2016 → today).

    Fills the ``trading_status`` ST gap so ``universe="all_a"`` excludes names that
    were ST in earlier backtest windows (removes survivorship / look-ahead bias).
    Resumable via a swept-symbol marker: a re-run only retries names a throttled
    session dropped. Progress is checkpointed every ``_ST_BACKFILL_CHUNK`` symbols
    (write staging + mark swept) so a mid-sweep login failure still keeps prior
    chunks. Symbols still failing are surfaced as an audit finding (fail-loud).
    """
    from stock_data_engine.adapters.baostock.st_history import fetch_st_history

    universe = [s for s in load_symbols(config) if _is_all_a(s)]
    # Only sweep names that have price bars: a delisted symbol still sitting in the
    # instruments list would otherwise cost a baostock round-trip with no bar to
    # join its ST label against. Skip the constraint on a bars-less lake so a
    # first-time backfill still runs.
    bar_universe = load_bar_universe(config)
    if bar_universe:
        universe = [s for s in universe if s in bar_universe]
    done = _st_backfilled_symbols(config)
    todo = [s for s in universe if s not in done]
    if not todo:
        return {"rows_read": 0, "rows_written": 0, "note": "all symbols already ST-backfilled"}

    rows_read = 0
    rows_written = 0
    all_failed: list[str] = []
    for offset in range(0, len(todo), _ST_BACKFILL_CHUNK):
        batch = todo[offset : offset + _ST_BACKFILL_CHUNK]
        df, failed = fetch_st_history(batch, BACKFILL_START, trade_date, config=config)
        if not df.is_empty():
            chunk = write_fetched(
                config,
                run_id,
                "trading_status",
                df,
                source="baostock",
                batch_id=f"batch-{offset:05d}",
            )
            rows_read += int(chunk.get("rows_read", 0))
            rows_written += int(chunk.get("rows_written", 0))
        failed_set = set(failed)
        swept = [s for s in batch if s not in failed_set]
        if swept:
            _mark_st_backfilled(config, swept)
        all_failed.extend(failed)

    result: dict = {"rows_read": rows_read, "rows_written": rows_written}
    if all_failed:
        result["failed_symbols"] = len(all_failed)
        finding = {
            "dataset": "trading_status",
            "severity": "warning",
            "code": "baostock_st_backfill_incomplete",
            "message": (
                f"{len(all_failed)}/{len(todo)} symbols failed baostock ST backfill "
                "(throttled/dropped); re-run `sde backfill trading_status` to resume."
            ),
        }
        result.setdefault("context_updates", {})["audit_findings"] = [finding]
    return result
