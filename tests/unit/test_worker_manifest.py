from datetime import date

import pytest

import stock_data_engine.steps  # noqa: F401
from stock_data_engine.config import load_config
from stock_data_engine.orchestrator.engine import JobEngine
from stock_data_engine.orchestrator.manifest import Manifest
from stock_data_engine.orchestrator.worker_pool import fetch_daily_bars_parallel
from stock_data_engine.storage.layout import init_data_layout


@pytest.fixture
def worker_config(tmp_path, monkeypatch):
    cfg_path = tmp_path / "test.toml"
    cfg_path.write_text(
        f"""
[data]
root = "{tmp_path / "data"}"

[orchestrator]
workers = 1
batch_size = 1

[tdx_protocol]
allow_mock = true

[[job.daily.waves]]
name = "bars"
parallel = false
steps = ["daily_bars"]
"""
    )
    return load_config(cfg_path)


def test_worker_pool_records_symbol_batches(worker_config, monkeypatch):
    init_data_layout(worker_config)
    manifest = Manifest(worker_config.manifest_path)
    run_id = manifest.start_run("test")

    fetch_daily_bars_parallel(
        worker_config,
        ["600519.SH", "000001.SZ"],
        date(2024, 6, 27),
        date(2024, 6, 28),
        run_id,
        "daily_bars",
    )

    batches = manifest.get_batches_for_run(run_id)
    assert len(batches) == 2
    assert {b["batch_id"] for b in batches} == {"batch-0", "batch-1"}


def test_retry_reruns_failed_symbol_batch_only(worker_config, monkeypatch):
    from stock_data_engine.adapters.tdx_protocol import client as tdx

    calls: list[list[str]] = []

    attempts: dict[str, int] = {}

    def _fetch(symbols, start, end, **kwargs):
        calls.append(list(symbols))
        sym = symbols[0]
        attempts[sym] = attempts.get(sym, 0) + 1
        if sym == "600519.SH" and attempts[sym] == 1:
            raise tdx.TdxSourceError("simulated failure")
        return tdx._mock_bars(symbols, start, end)

    monkeypatch.setattr("stock_data_engine.orchestrator.worker_pool.fetch_daily_bars", _fetch)
    monkeypatch.setattr(
        "stock_data_engine.steps.bars.load_symbols",
        lambda _cfg: ["600519.SH", "000001.SZ"],
    )
    worker_config.tdx_allow_mock = False

    init_data_layout(worker_config)
    engine = JobEngine(worker_config)
    result = engine.run_job("daily", date(2024, 6, 28), steps=["daily_bars"])
    assert result["status"] == "failed"
    assert list(worker_config.staging_root.glob("daily_bars/**/*.parquet"))
    assert not list(worker_config.curated_root.glob("daily_bars/**/*.parquet"))

    retry = engine.run_job(
        "daily",
        date(2024, 6, 28),
        run_id=result["run_id"],
        retry_failed_only=True,
    )
    assert retry["retried"] >= 1
    assert retry["status"] == "success"
    assert calls[-1] == ["600519.SH"]
    curated = worker_config.curated_root / "daily_bars" / "trade_date=2024-06-28" / "part-merged.parquet"
    assert curated.exists()
    assert any(r.get("step") == "compact" for r in retry["results"])
