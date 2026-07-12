"""Offline tests for the shared baostock session driver (retry + watchdog)."""

from __future__ import annotations

import threading
from datetime import date

from stock_data_engine.adapters.baostock._session import fetch_per_symbol


class _NoQueryBaostock:
    """A bs stub with just login/logout — queries are driven by the injected fetch."""

    def __init__(self):
        self.logins = 0
        self.logged_out = False

    def login(self):
        self.logins += 1
        return type("R", (), {"error_code": "0", "error_msg": ""})()

    def logout(self):
        self.logged_out = True


def test_completes_normally_without_tripping_the_watchdog():
    bs = _NoQueryBaostock()

    def fetch(_bs, symbol, _s, _e):
        return [{"symbol": symbol}]

    fired = []
    rows, failed = fetch_per_symbol(
        ["600000.SH", "600001.SH"], date(2020, 1, 1), date(2020, 12, 31), fetch,
        bs=bs, sleep=lambda _s: None, deadline=5.0, on_deadline=lambda: fired.append(1),
    )
    assert failed == []
    assert {r["symbol"] for r in rows} == {"600000.SH", "600001.SH"}
    assert fired == []  # fast fetches never reach the deadline
    assert bs.logged_out is True


def test_watchdog_interrupts_a_stalled_symbol_and_retries():
    # fetch_one blocks as if on a slowloris recv; the watchdog "closes the socket"
    # (on_deadline), which unblocks it into a raise — retried, then reported failed.
    bs = _NoQueryBaostock()
    unblock = threading.Event()
    calls = {"fetch": 0, "deadline": 0}

    def stalled_fetch(_bs, _symbol, _s, _e):
        calls["fetch"] += 1
        if unblock.wait(timeout=2.0):
            unblock.clear()
            raise ConnectionError("socket closed by watchdog")
        return [{"symbol": _symbol}]  # would mean the stall never resolved

    def on_deadline():
        calls["deadline"] += 1
        unblock.set()

    rows, failed = fetch_per_symbol(
        ["600000.SH"], date(2020, 1, 1), date(2020, 12, 31), stalled_fetch,
        bs=bs, sleep=lambda _s: None, deadline=0.05, on_deadline=on_deadline,
    )
    assert calls["deadline"] >= 1  # watchdog fired on the stall
    assert calls["fetch"] == 3  # retried the full _MAX_RETRIES
    assert failed == ["600000.SH"]
    assert rows == []
