import time
from concurrent.futures import ProcessPoolExecutor

from stock_data_engine.domain.rate_limit import RateLimiter, wait_source


def test_rate_limiter_enforces_minimum_interval(tmp_path):
    state_dir = tmp_path / "rate_limits"
    limiter = RateLimiter("test", 0.05, state_dir)
    limiter.wait()
    t0 = time.perf_counter()
    limiter.wait()
    assert time.perf_counter() - t0 >= 0.04


def _worker_wait(state_dir: str) -> float:
    t0 = time.perf_counter()
    wait_source(state_dir, "test", 0.05)
    return time.perf_counter() - t0


def test_rate_limiter_serializes_cross_process_requests(tmp_path):
    state_dir = tmp_path / "rate_limits"
    with ProcessPoolExecutor(max_workers=2) as pool:
        durations = list(pool.map(_worker_wait, [str(state_dir), str(state_dir)]))
    assert max(durations) >= 0.04
