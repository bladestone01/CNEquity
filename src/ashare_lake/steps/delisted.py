"""Reconstruct the delisted universe by sweeping the exchange code space.

The lake's history was backfilled from the *current* listing snapshot, so every
name that ever left the market is missing and every return series it produces is
survivorship-biased (audit: ``universe_survivorship_absent``). Closing that needs
two things no primary source provides: a list of the codes that used to trade,
and their price history.

Neither vendor list is reliably available — baostock's ``query_stock_basic``
answers it in one query but blacklists an IP that has swept it, and EastMoney's
kline host is unreachable from many networks. What is always available is Sina,
and Sina will answer "did this code ever trade" one code at a time. So the
delisted set is reconstructed from the outside: enumerate the issued code space,
subtract what is listed today, and ask about the remainder. A code that answers
is a former listing; one that does not was never issued.

That is ~9,000 requests, so the sweep checkpoints after every batch and resumes
from where it stopped. It is deliberately a separate command from the ingest:
the catalogue is worth reading before committing to a bulk backfill.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import httpx
import polars as pl

from ashare_lake.config import Config
from ashare_lake.domain.symbols import issued_code_space
from ashare_lake.steps.common import load_symbols

logger = logging.getLogger(__name__)

_CATALOG_FILE = "delisted_catalog.json"
# Checkpoint cadence. Small enough that an interrupted sweep loses seconds of
# work, large enough that the state file is not rewritten on every request.
_CHECKPOINT_EVERY = 100


@dataclass
class DiscoveryResult:
    probed: int = 0
    delisted: int = 0
    never_issued: int = 0
    failed: list[str] = field(default_factory=list)
    remaining: int = 0

    @property
    def complete(self) -> bool:
        return self.remaining == 0


def catalog_path(config: Config) -> Path:
    return config.meta_root / "state" / _CATALOG_FILE


def _read_catalog(config: Config) -> dict:
    path = catalog_path(config)
    if not path.exists():
        return {"delisted": {}, "never_issued": [], "version": 1}
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.setdefault("delisted", {})
    payload.setdefault("never_issued", [])
    return payload


def _write_catalog(config: Config, payload: dict) -> None:
    path = catalog_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except Exception:
        with suppress(OSError):
            os.unlink(tmp)
        raise


def load_delisted_catalog(config: Config) -> dict[str, date]:
    """Discovered delisted symbols -> their last trading date."""
    raw = _read_catalog(config)["delisted"]
    return {sym: date.fromisoformat(d) for sym, d in raw.items()}


def pending_codes(config: Config) -> list[str]:
    """Issued codes neither listed today nor already classified by a prior sweep."""
    live = set(load_symbols(config))
    catalog = _read_catalog(config)
    done = set(catalog["delisted"]) | set(catalog["never_issued"])
    return [s for s in issued_code_space() if s not in live and s not in done]


def discover_delisted(
    config: Config,
    *,
    limit: int | None = None,
    probe=None,
) -> DiscoveryResult:
    """Classify unlisted codes as former listings or never-issued, resumably.

    ``probe(symbol, client) -> date | None`` is injectable for tests; the default
    asks Sina for a single bar. A probe that raises is recorded as failed and
    left pending, so a transient outage never gets misfiled as "never issued" —
    that misfiling would silently and permanently shrink the universe.
    """
    from ashare_lake.adapters.sina.bars import symbol_exists

    probe = probe or (lambda sym, client: symbol_exists(sym, client=client))
    todo = pending_codes(config)
    if limit is not None:
        todo = todo[:limit]

    catalog = _read_catalog(config)
    result = DiscoveryResult()
    logger.info("delisted discovery: %d code(s) to probe", len(todo))

    with httpx.Client(timeout=20.0) as client:
        for index, symbol in enumerate(todo, start=1):
            config.rate_limit("sina")
            try:
                last_seen = probe(symbol, client)
            except Exception as exc:  # noqa: BLE001 — never misfile an outage
                logger.warning("delisted discovery: probe failed for %s: %s", symbol, exc)
                result.failed.append(symbol)
                continue
            result.probed += 1
            if last_seen is None:
                catalog["never_issued"].append(symbol)
                result.never_issued += 1
            else:
                catalog["delisted"][symbol] = last_seen.isoformat()
                result.delisted += 1
                logger.info("delisted discovery: %s last traded %s", symbol, last_seen)

            if index % _CHECKPOINT_EVERY == 0:
                _write_catalog(config, catalog)
                logger.info(
                    "delisted discovery: %d/%d probed (%d delisted so far)",
                    index,
                    len(todo),
                    result.delisted,
                )

    _write_catalog(config, catalog)
    result.remaining = len(pending_codes(config))
    logger.info(
        "delisted discovery: probed=%d delisted=%d never_issued=%d failed=%d remaining=%d",
        result.probed,
        result.delisted,
        result.never_issued,
        len(result.failed),
        result.remaining,
    )
    return result


# --- ingest -----------------------------------------------------------------

_INGESTED_STATE = "delisted_ingested"
# Stage every N symbols so a long sweep that dies keeps what it already fetched.
_INGEST_CHUNK = 50


def _ingested_symbols(config: Config) -> set[str]:
    path = config.meta_root / "state" / f"{_INGESTED_STATE}.json"
    if not path.exists():
        return set()
    return set(json.loads(path.read_text(encoding="utf-8")).get("completed", []))


def _mark_ingested(config: Config, symbols: list[str]) -> None:
    path = config.meta_root / "state" / f"{_INGESTED_STATE}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    completed = sorted(_ingested_symbols(config) | set(symbols))
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"completed": completed}, handle, indent=2)
        os.replace(tmp, path)
    except Exception:
        with suppress(OSError):
            os.unlink(tmp)
        raise


def _asset_type(symbol: str) -> str:
    from ashare_lake.domain.symbols import is_cdr_symbol, is_etf_symbol, parse_symbol

    info = parse_symbol(symbol)
    if is_etf_symbol(info.code, info.exchange):
        return "etf"
    if is_cdr_symbol(info.code, info.exchange):
        return "cdr"
    return "stock"


def delisted_symbols_in_window(config: Config, start: date) -> list[str]:
    """Catalogued delistings whose trading overlapped the lake's window.

    A name that stopped trading before the lake starts contributes nothing to a
    backtest over it, so fetching its history would be pure cost.
    """
    catalog = load_delisted_catalog(config)
    already = _ingested_symbols(config)
    return sorted(s for s, last in catalog.items() if last >= start and s not in already)


def _instruments_rows(config: Config, spans: dict[str, tuple[date, date]]) -> pl.DataFrame:
    """instruments rows for the recovered names, unioned with the live snapshot.

    The union matters: ``compact_instruments`` treats symbols missing from the
    incoming frame as candidate delistings, and staging only the recovered names
    would present all ~7,400 live symbols as absent — tripping the partial-fetch
    guard and emitting a spurious error. Sina's kline carries no company name, so
    ``name`` stays null rather than being invented.
    """
    from ashare_lake.domain.schemas import INSTRUMENTS_SCHEMA

    cols = ["symbol", "name", "exchange", "asset_type", "list_date", "delist_date", "prev_symbol"]
    rows = [
        {
            "symbol": symbol,
            "name": None,
            "exchange": symbol.split(".")[1],
            "asset_type": _asset_type(symbol),
            "list_date": first,
            # The last day it traded. Universe filters keep a symbol through its
            # delist_date, so this includes the final session.
            "delist_date": last,
            "prev_symbol": None,
        }
        for symbol, (first, last) in sorted(spans.items())
    ]
    if not rows:
        return pl.DataFrame()

    recovered = pl.DataFrame(rows, schema={c: INSTRUMENTS_SCHEMA[c] for c in cols})
    live_path = config.curated_root / "instruments" / "part-merged.parquet"
    if not live_path.exists():
        return recovered
    live = pl.read_parquet(live_path).drop(["source", "data_version", "fetched_at"], strict=False)
    # keep="first" so a live row always wins over a recovered one for the same
    # code — a code reissued after a delisting must stay listed.
    return pl.concat([live, recovered], how="diagonal_relaxed").unique(
        subset=["symbol"], keep="first"
    )


def backfill_delisted_bars(
    config: Config,
    run_id: str,
    start: date,
    *,
    fetch=None,
) -> dict:
    """Fetch full price history for catalogued delistings and stage it.

    Bars land in ``daily_bars`` alongside the live names with ``source='sina'``:
    the same kind of fact from a different vendor, which is what the provenance
    columns exist for. ``adj_factors`` picks them up on the next derive because
    it iterates the symbols present in ``daily_bars``.

    Staged in chunks so an interrupted multi-hour sweep keeps what it fetched,
    with the per-symbol date spans accumulated across chunks — they are what the
    ``instruments`` rows are built from at the end.
    """
    from ashare_lake.adapters.sina.bars import fetch_daily_bars_sina
    from ashare_lake.steps.http_common import write_fetched

    fetch = fetch or (
        lambda symbol, client: fetch_daily_bars_sina(symbol, start=start, client=client)
    )
    todo = delisted_symbols_in_window(config, start)
    if not todo:
        return {"rows_read": 0, "rows_written": 0, "note": "no catalogued delistings to ingest"}

    logger.info("delisted bars: %d symbol(s) to fetch from %s", len(todo), start.isoformat())
    rows_written = 0
    failed: list[str] = []
    spans: dict[str, tuple[date, date]] = {}
    pending_frames: list[pl.DataFrame] = []
    pending_symbols: list[str] = []

    def flush(chunk_index: int) -> None:
        nonlocal rows_written
        if pending_frames:
            merged = pl.concat(pending_frames, how="diagonal_relaxed")
            out = write_fetched(
                config,
                run_id,
                "daily_bars",
                merged,
                source="sina",
                batch_id=f"delisted-{chunk_index:04d}",
            )
            rows_written += int(out.get("rows_written", 0))
        if pending_symbols:
            _mark_ingested(config, pending_symbols)
        pending_frames.clear()
        pending_symbols.clear()

    with httpx.Client(timeout=30.0) as client:
        for index, symbol in enumerate(todo, start=1):
            config.rate_limit("sina")
            try:
                bars = fetch(symbol, client)
            except Exception as exc:  # noqa: BLE001 — one dead symbol must not stop the sweep
                logger.warning("delisted bars: fetch failed for %s: %s", symbol, exc)
                failed.append(symbol)
                continue
            if not bars.is_empty():
                pending_frames.append(bars)
                spans[symbol] = (bars["trade_date"].min(), bars["trade_date"].max())
            pending_symbols.append(symbol)
            if index % _INGEST_CHUNK == 0:
                flush(index // _INGEST_CHUNK)
                logger.info(
                    "delisted bars: %d/%d fetched (%d rows)", index, len(todo), rows_written
                )
        flush(0)

    instruments = _instruments_rows(config, spans)
    if not instruments.is_empty():
        write_fetched(config, run_id, "instruments", instruments, source="sina")

    result: dict = {
        "rows_read": rows_written,
        "rows_written": rows_written,
        "symbols": len(todo),
        "recovered": len(spans),
    }
    if failed:
        result["failed_symbols"] = len(failed)
        result.setdefault("context_updates", {})["audit_findings"] = [
            {
                "dataset": "daily_bars",
                "severity": "warning",
                "check": "delisted_backfill_incomplete",
                "message": (
                    f"{len(failed)}/{len(todo)} delisted symbols failed to fetch; re-run to resume"
                ),
            }
        ]
    return result
