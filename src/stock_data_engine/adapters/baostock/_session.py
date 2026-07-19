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
import threading
import time
from collections.abc import Callable
from datetime import date

from stock_data_engine.domain.symbols import parse_symbol

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_BACKOFF_SECONDS = (1.0, 3.0, 8.0)
# Refresh the session periodically; a single long-held session dies mid-sweep.
_RELOGIN_EVERY = 300
# Login itself is flaky on long sweeps (observed: "网络接收错误" after ~3h).
# Retry before failing so a transient blip does not abort thousands of symbols.
_LOGIN_RETRIES = 5
_LOGIN_BACKOFF_SECONDS = (2.0, 5.0, 10.0, 20.0, 30.0)
# Free-API defaults when Config is not wired (tests / ad-hoc calls).
_DEFAULT_MIN_INTERVAL = 1.0
_DEFAULT_BATCH_SIZE = 50
_DEFAULT_BATCH_REST = 45.0
# Hard per-symbol wall-clock deadline. The socket timeout below catches a fully
# blocked read, but baostock can *slowloris* a query — trickle keepalive bytes so
# every recv returns just before the timeout yet the terminator never arrives, so
# the read loops forever at ~0 CPU (observed: 8h hang). A watchdog closes the
# live socket past this deadline, unblocking the read so it raises and retries.
# Set well above the ~6s normal query so a slow-but-alive fetch is never killed.
_PER_SYMBOL_DEADLINE_SECONDS = 45.0
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


def _login(bs, *, sleep=time.sleep) -> None:
    last_msg = "unknown"
    for attempt in range(_LOGIN_RETRIES):
        login = bs.login()
        if getattr(login, "error_code", "0") == "0":
            return
        last_msg = getattr(login, "error_msg", "unknown")
        if attempt + 1 < _LOGIN_RETRIES:
            sleep(_LOGIN_BACKOFF_SECONDS[min(attempt, len(_LOGIN_BACKOFF_SECONDS) - 1)])
    raise RuntimeError(f"baostock login failed: {last_msg}")


def _relogin(bs, *, sleep=time.sleep) -> None:
    try:
        bs.logout()
    except Exception:  # noqa: BLE001 - logout on a dead socket may raise; ignore
        pass
    _login(bs, sleep=sleep)


def to_baostock_symbol(symbol: str) -> str:
    """``600519.SH`` -> ``sh.600519`` (baostock's market-prefixed form)."""
    info = parse_symbol(symbol)
    prefix = {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(info.exchange, info.exchange.lower())
    return f"{prefix}.{info.code}"


def _ensure_socket_timeout(timeout: float = _SOCKET_TIMEOUT_SECONDS) -> None:
    """Pin timeout on baostock's live socket (setdefaulttimeout is not enough).

    ``SocketUtil.connect`` creates the socket without ``settimeout``; on some
    platforms the module-global default is ignored once the fd is in a blocking
    ``recv`` loop waiting for baostock's ``<![CDATA[]]>`` terminator.
    """
    try:
        import baostock.common.context as bctx  # noqa: PLC0415 — optional dep, lazy

        sock = getattr(bctx, "default_socket", None)
        if sock is not None:
            sock.settimeout(timeout)
    except Exception:  # noqa: BLE001 — best-effort; never break the sweep
        pass


def _force_close_baostock_socket() -> None:
    """Interrupt baostock's live socket so a blocked/slowloris read raises at once.

    baostock keeps the connection as a module global. ``shutdown(SHUT_RDWR)`` —
    not ``close()`` — is what reliably wakes a ``recv`` blocked in another thread:
    ``close()`` only drops this thread's reference and the peer's read can keep
    hanging. Shut down then close; the next retry relogins onto a fresh socket.
    """
    try:
        import baostock.common.context as bctx  # noqa: PLC0415 — optional dep, lazy

        sock = getattr(bctx, "default_socket", None)
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass  # already torn down / not connected
        sock.close()
    except Exception:  # noqa: BLE001 — best-effort interrupt; never raise from the timer
        pass


def _pace_before_symbol(config, *, sleep=time.sleep) -> None:
    """Cross-process min_interval when Config is set; else local default sleep."""
    if config is not None:
        config.rate_limit("baostock")
        return
    sleep(_DEFAULT_MIN_INTERVAL)


def _batch_rest(config, index: int, *, sleep=time.sleep) -> None:
    """Extra pause every N symbols so free APIs cool down between bursts."""
    batch = (
        int(getattr(config, "baostock_batch_size", _DEFAULT_BATCH_SIZE))
        if config is not None
        else _DEFAULT_BATCH_SIZE
    )
    rest = (
        float(getattr(config, "baostock_batch_rest_seconds", _DEFAULT_BATCH_REST))
        if config is not None
        else _DEFAULT_BATCH_REST
    )
    if batch <= 0 or rest <= 0:
        return
    # Rest after completing a batch (index is 0-based; rest when about to start next).
    if index > 0 and index % batch == 0:
        logger.info("baostock batch rest %.0fs after %d symbols", rest, index)
        sleep(rest)


def fetch_per_symbol(
    symbols: list[str],
    start: date,
    end: date,
    fetch_one: Callable[[object, str, date, date], list[dict] | None],
    *,
    bs=None,
    sleep=time.sleep,
    label: str = "baostock",
    deadline: float = _PER_SYMBOL_DEADLINE_SECONDS,
    on_deadline: Callable[[], None] = _force_close_baostock_socket,
    config=None,
) -> tuple[list[dict], list[str]]:
    """Drive ``fetch_one(bs, symbol, start, end)`` over ``symbols`` with retry/relogin.

    ``fetch_one`` returns a list of row dicts, or ``None`` on a retryable query
    error (an ``error_code == '0'`` result with zero rows is a legitimate empty
    and must be returned as ``[]``). Returns ``(rows, failed_symbols)``. Fail-loud
    on login failure. ``bs`` / ``sleep`` are injectable for offline tests.

    Pacing (anti-blacklist for free baostock):
    - ``config.rate_limit("baostock")`` before each symbol (cross-process), or
      ``_DEFAULT_MIN_INTERVAL`` when ``config`` is None;
    - batch rest every ``baostock_batch_size`` symbols.
    """
    if bs is None:
        bs = import_baostock()

    prev_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(_SOCKET_TIMEOUT_SECONDS)
    _login(bs, sleep=sleep)
    _ensure_socket_timeout()
    rows: list[dict] = []
    failed: list[str] = []
    n_symbols = len(symbols)
    try:
        for i, symbol in enumerate(symbols):
            _batch_rest(config, i, sleep=sleep)
            _pace_before_symbol(config, sleep=sleep)
            if i and _RELOGIN_EVERY and i % _RELOGIN_EVERY == 0:
                try:
                    _relogin(bs, sleep=sleep)
                    _ensure_socket_timeout()
                except RuntimeError as exc:
                    # Keep rows already collected so the caller can checkpoint;
                    # remaining symbols stay on the resume set.
                    logger.error(
                        "%s mid-sweep login failed at %d/%d: %s; returning partial",
                        label,
                        i + 1,
                        n_symbols,
                        exc,
                    )
                    failed.extend(symbols[i:])
                    break
            # Heartbeat every 10 symbols so multi-hour sweeps look alive on stdout.
            if i == 0 or (i + 1) % 10 == 0 or i + 1 == n_symbols:
                logger.info(
                    "%s progress %d/%d symbol=%s ok_rows=%d failed=%d",
                    label,
                    i + 1,
                    n_symbols,
                    symbol,
                    len(rows),
                    len(failed),
                )
            got: list[dict] | None = None
            abort_remaining = False
            for attempt in range(_MAX_RETRIES):
                watchdog = threading.Timer(deadline, on_deadline)
                watchdog.start()
                try:
                    got = fetch_one(bs, symbol, start, end)
                except Exception as exc:  # noqa: BLE001 — stalled socket / broken pipe
                    # A socket timeout, dropped connection, or watchdog-closed
                    # socket raises here; treat it like a query error so the
                    # symbol is retried on a fresh login.
                    logger.warning("%s query error for %s: %s", label, symbol, exc)
                    got = None
                finally:
                    watchdog.cancel()
                if got is not None:
                    break
                sleep(_BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)])
                try:
                    _relogin(bs, sleep=sleep)
                    _ensure_socket_timeout()
                except RuntimeError as exc:
                    logger.error(
                        "%s login failed while retrying %s: %s; returning partial",
                        label,
                        symbol,
                        exc,
                    )
                    got = None
                    abort_remaining = True
                    break
            if got is None:
                logger.warning("%s failed for %s after retries", label, symbol)
                failed.append(symbol)
                if abort_remaining:
                    failed.extend(symbols[i + 1 :])
                    break
            else:
                rows.extend(got)
    finally:
        socket.setdefaulttimeout(prev_timeout)
        try:
            bs.logout()
        except Exception:  # noqa: BLE001
            pass

    return rows, failed
