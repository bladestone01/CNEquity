import os
import sys

import pytest

from cnequity.config import load_config
from cnequity.config.bootstrap import path_for_toml


def pytest_configure(config):
    """Keep Windows ProcessPoolExecutor teardown from aborting the suite.

    A spawned worker that exits on Windows can inject ``CTRL_C_EVENT`` into
    the parent's console group. pytest then raises ``KeyboardInterrupt``
    mid-session — CI has seen this right after the rate-limiter process
    tests, with every earlier test already green. Ignoring the console
    signal is the usual workaround (CPython issue 33725).
    """
    if sys.platform != "win32":
        return
    import ctypes

    ctypes.windll.kernel32.SetConsoleCtrlHandler(None, True)


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    """Keep a green Windows suite from exiting 1 in multiprocessing atexit.

    After 2589 passed / 0 failed, CI still reported exit code 1: leftover
    ``ProcessPoolExecutor`` workers run atexit handlers that replace pytest's
    status. Reap them, and on Actions skip those handlers once the session
    is already green.
    """
    if sys.platform != "win32":
        return
    try:
        import multiprocessing.process as mp_process

        children = list(getattr(mp_process, "_children", set()))
        for proc in children:
            try:
                if proc.is_alive():
                    proc.terminate()
                proc.join(timeout=1)
            except Exception:
                pass
    except Exception:
        pass
    if os.environ.get("CI") == "true" and int(exitstatus or 0) == 0:
        os._exit(0)


@pytest.fixture
def config(tmp_path):
    """Minimal offline config wiring a daily Wave over mock adapters."""
    cfg_path = tmp_path / "test.toml"
    cfg_path.write_text(
        f"""
[data]
root = "{path_for_toml(tmp_path / "data")}"

[orchestrator]
workers = 1
batch_size = 2

[tdx_protocol]
allow_mock = true

[[job.daily.waves]]
name = "reference"
parallel = true
steps = ["instruments", "trading_calendar"]

[[job.daily.waves]]
name = "bars"
parallel = false
steps = ["daily_bars", "compact", "derive_adj_factors", "audit"]

[job.init.phases]
names = ["phase1_reference"]
""",
        encoding="utf-8",
    )
    return load_config(cfg_path)
