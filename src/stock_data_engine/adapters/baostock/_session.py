"""Shared baostock session driver: login, per-symbol retry, periodic relogin.

Extracted from the A2 valuation backfill and reused by the C4 ST-history
backfill. baostock throttles or drops a long-held session under a full-market
sweep, so each symbol is retried with a fresh session and backoff, and the
session is refreshed every ``_RELOGIN_EVERY`` symbols. Symbols still failing
after retries are returned so the caller can surface them (fail-loud) and resume
rather than ship a silent partial backfill.
"""

from __future__ import annotations

import logging
import socket
import time
from collections.abc import Callable
from datetime import date

from stock_data_engine.domain.symbols import parse_symbol

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_BACKOFF_SECONDS = (1.0, 3.0, 8.0)
# Refresh the session periodically; a single long-held session dies mid-sweep.
_RELOGIN_EVERY = 300
# A single k-data query returns a few thousand rows and should finish in
# seconds. baostock can silently drop the connection yet leave the socket
# ESTABLISHED, so a blocking read never returns and the whole sweep hangs
# indefinitely (observed: 8h of wall time, 13s of CPU). Bound every socket op so
# a stall raises instead of hanging; the retry loop then relogins and continues.
_SOCKET_TIMEOUT_SECONDS = 30.0


def import_baostock():
    try:
        import baostock as bs  # noqa: PLC0415 — optional dependency, imported lazily
    except ImportError as exc:  # pragma: no cover - optional dep
        raise RuntimeError(
            "baostock is not installed; historical backfill requires it. "
            "Install with `pip install -e '.[valuation]'`."
        ) from exc
    return bs


def _login(bs) -> None:
    login = bs.login()
    if getattr(login, "error_code", "0") != "0":
        raise RuntimeError(f"baostock login failed: {getattr(login, 'error_msg', 'unknown')}")


def _relogin(bs) -> None:
    try:
        bs.logout()
    except Exception:  # noqa: BLE001 - logout on a dead socket may raise; ignore
        pass
    _login(bs)


def to_baostock_symbol(symbol: str) -> str:
    """``600519.SH`` -> ``sh.600519`` (baostock's market-prefixed form)."""
    info = parse_symbol(symbol)
    prefix = {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(info.exchange, info.exchange.lower())
    return f"{prefix}.{info.code}"


def fetch_per_symbol(
    symbols: list[str],
    start: date,
    end: date,
    fetch_one: Callable[[object, str, date, date], list[dict] | None],
    *,
    bs=None,
    sleep=time.sleep,
    label: str = "baostock",
) -> tuple[list[dict], list[str]]:
    """Drive ``fetch_one(bs, symbol, start, end)`` over ``symbols`` with retry/relogin.

    ``fetch_one`` returns a list of row dicts, or ``None`` on a retryable query
    error (an ``error_code == '0'`` result with zero rows is a legitimate empty
    and must be returned as ``[]``). Returns ``(rows, failed_symbols)``. Fail-loud
    on login failure. ``bs`` / ``sleep`` are injectable for offline tests.
    """
    if bs is None:
        bs = import_baostock()

    prev_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(_SOCKET_TIMEOUT_SECONDS)
    _login(bs)
    rows: list[dict] = []
    failed: list[str] = []
    try:
        for i, symbol in enumerate(symbols):
            if i and _RELOGIN_EVERY and i % _RELOGIN_EVERY == 0:
                _relogin(bs)
            got: list[dict] | None = None
            for attempt in range(_MAX_RETRIES):
                try:
                    got = fetch_one(bs, symbol, start, end)
                except Exception as exc:  # noqa: BLE001 — stalled socket / broken pipe
                    # A socket timeout or dropped connection raises here; treat it
                    # like a query error so the symbol is retried on a fresh login.
                    logger.warning("%s query error for %s: %s", label, symbol, exc)
                    got = None
                if got is not None:
                    break
                sleep(_BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)])
                _relogin(bs)
            if got is None:
                logger.warning("%s failed for %s after retries", label, symbol)
                failed.append(symbol)
            else:
                rows.extend(got)
    finally:
        socket.setdefaulttimeout(prev_timeout)
        try:
            bs.logout()
        except Exception:  # noqa: BLE001
            pass

    return rows, failed
