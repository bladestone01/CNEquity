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
