from datetime import date

import stock_data_engine.steps  # noqa: F401
from stock_data_engine.config import Config
from stock_data_engine.orchestrator.engine import JobEngine
from stock_data_engine.orchestrator.manifest import Manifest
from stock_data_engine.orchestrator.run_lock import RunLockError, run_lock
from stock_data_engine.storage.layout import init_data_layout


def test_retry_pending_when_batches_still_running(tmp_path):
    cfg = Config(data_root=tmp_path / "data", tdx_allow_mock=True)
    init_data_layout(cfg)
    manifest = Manifest(cfg.manifest_path)
    run_id = manifest.start_run("daily", {"trade_date": "2024-06-28"})
    manifest.start_batch(
        run_id,
        "batch-live",
        "daily_bars",
        "daily_bars",
        symbols=["600519.SH"],
        window_start="2024-06-28",
        window_end="2024-06-28",
    )
    manifest.finish_run(run_id, "failed")

    engine = JobEngine(cfg)
    result = engine.run_job(
        "retry",
        date(2024, 6, 28),
        run_id=run_id,
        retry_failed_only=True,
    )
    assert result["status"] == "pending"
    assert result["retried"] == 0
    assert result["incomplete_batches"] == 1
    assert result["incomplete_by_status"]["running"] == 1


def test_worker_batch_specs_reads_manifest_window(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    init_data_layout(cfg)
    engine = JobEngine(cfg)
    manifest = engine.manifest
    run_id = manifest.start_run("daily", {})
    manifest.start_batch(
        run_id,
        "2016-01-01_2024-06-27-batch-0",
        "daily_bars",
        "daily_bars",
        symbols=["600519.SH"],
        window_start="2016-01-01",
        window_end="2024-06-27",
    )
    manifest.finish_batch(run_id, "2016-01-01_2024-06-27-batch-0", "failed", error_message="boom")

    specs = engine._worker_batch_specs(manifest.get_failed_batches(run_id), date(2024, 6, 28))
    assert specs == [
        (
            "2016-01-01_2024-06-27-batch-0",
            ["600519.SH"],
            date(2016, 1, 1),
            date(2024, 6, 27),
        )
    ]


def test_run_lock_blocks_concurrent_retry(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    init_data_layout(cfg)
    run_id = "run-lock-test"
    with run_lock(cfg.meta_root, run_id):
        try:
            with run_lock(cfg.meta_root, run_id):
                raise AssertionError("should not acquire twice")
        except RunLockError:
            pass


def test_daily_ingestion_lock_blocks_overlapping_runs(tmp_path, monkeypatch):
    cfg = Config(data_root=tmp_path / "data")
    init_data_layout(cfg)
    engine = JobEngine(cfg)
    monkeypatch.setattr(engine, "_run_wave", lambda *args, **kwargs: ([], 0, 0, False, False))

    with run_lock(cfg.meta_root, "daily_ingestion"):
        try:
            engine.run_job("daily:core", date(2024, 6, 28), waves=[])
        except RunLockError:
            pass
        else:
            raise AssertionError("expected RunLockError")


def test_reconcile_orphaned_runs_closes_stale_running(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    init_data_layout(cfg)
    manifest = Manifest(cfg.manifest_path)
    run_id = manifest.start_run("daily:core", {})
    manifest.start_batch(run_id, "batch-0", "daily_bars", "daily_bars")
    out = manifest.reconcile_orphaned_runs(stale_after_seconds=0)
    assert out["runs_closed"] == 1
    assert out["batches_closed"] == 1
    assert manifest.get_run(run_id)["status"] == "failed"
