from datetime import UTC, date, datetime, timedelta

import polars as pl

from ashare_lake.config import Config
from ashare_lake.orchestrator.manifest import Manifest
from ashare_lake.storage import StagingWriter
from ashare_lake.storage.staging_cleanup import clean_staging, list_staging_run_ids


def _bar_row(symbol: str, trade_date: date) -> dict:
    return {
        "symbol": symbol,
        "trade_date": trade_date,
        "open": 10.0,
        "high": 11.0,
        "low": 9.0,
        "close": 10.5,
        "volume": 1000,
        "amount": 10_500.0,
        "source": "mock",
        "data_version": "v1",
        "fetched_at": f"{trade_date.isoformat()}T00:00:00+00:00",
    }


def test_clean_removes_staging_for_successful_compacted_run(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    manifest = Manifest(cfg.manifest_path)
    run_id = manifest.start_run("daily", {"trade_date": "2024-06-28"})
    manifest.start_batch(run_id, "batch-0", "daily_bars", "daily_bars")
    manifest.finish_batch(run_id, "batch-0", "success", rows_written=1)
    manifest.start_batch(run_id, "compact-0", "compact", "compact")
    manifest.finish_batch(run_id, "compact-0", "success", rows_written=1)
    manifest.finish_run(run_id, "success")

    writer = StagingWriter(cfg.staging_root)
    writer.write_batch(
        "daily_bars",
        run_id,
        "batch-0",
        pl.DataFrame([_bar_row("000001.SZ", date(2024, 6, 28))]),
    )
    assert run_id in list_staging_run_ids(cfg.staging_root)

    result = clean_staging(cfg)
    assert run_id in result.removed_run_ids
    assert run_id not in list_staging_run_ids(cfg.staging_root)


def test_clean_skips_incomplete_run(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    manifest = Manifest(cfg.manifest_path)
    run_id = manifest.start_run("daily", {})
    manifest.start_batch(run_id, "batch-0", "daily_bars", "daily_bars")
    manifest.finish_batch(run_id, "batch-0", "failed", error_message="err")
    manifest.finish_run(run_id, "failed")

    writer = StagingWriter(cfg.staging_root)
    writer.write_batch(
        "daily_bars",
        run_id,
        "batch-0",
        pl.DataFrame([_bar_row("000001.SZ", date(2024, 6, 28))]),
    )

    result = clean_staging(cfg, orphan_retention_days=999)
    assert run_id in result.skipped_run_ids
    assert run_id in list_staging_run_ids(cfg.staging_root)


def test_clean_never_deletes_failed_run_staging_without_force(tmp_path):
    """A failed run's success batches live only in staging; age must not matter."""
    import os

    cfg = Config(data_root=tmp_path / "data")
    manifest = Manifest(cfg.manifest_path)
    run_id = manifest.start_run("daily", {})
    manifest.start_batch(run_id, "batch-0", "daily_bars", "daily_bars")
    manifest.finish_batch(run_id, "batch-0", "success", rows_written=1)
    manifest.start_batch(run_id, "batch-1", "daily_bars", "daily_bars")
    manifest.finish_batch(run_id, "batch-1", "failed", error_message="err")
    manifest.finish_run(run_id, "failed")

    writer = StagingWriter(cfg.staging_root)
    writer.write_batch(
        "daily_bars",
        run_id,
        "batch-0",
        pl.DataFrame([_bar_row("000001.SZ", date(2024, 6, 28))]),
    )
    run_dir = cfg.staging_root / "daily_bars" / f"run_id={run_id}"
    old = datetime.now(UTC) - timedelta(days=30)
    os.utime(run_dir, (old.timestamp(), old.timestamp()))

    result = clean_staging(cfg, orphan_retention_days=7)
    assert run_id in result.skipped_run_ids
    assert run_id in list_staging_run_ids(cfg.staging_root)


def test_clean_force_deletes_failed_run_and_demotes_success_batches(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    manifest = Manifest(cfg.manifest_path)
    run_id = manifest.start_run("daily", {})
    manifest.start_batch(run_id, "batch-0", "daily_bars", "daily_bars")
    manifest.finish_batch(run_id, "batch-0", "success", rows_written=1)
    manifest.start_batch(run_id, "batch-1", "daily_bars", "daily_bars")
    manifest.finish_batch(run_id, "batch-1", "failed", error_message="err")
    manifest.finish_run(run_id, "failed")

    writer = StagingWriter(cfg.staging_root)
    writer.write_batch(
        "daily_bars",
        run_id,
        "batch-0",
        pl.DataFrame([_bar_row("000001.SZ", date(2024, 6, 28))]),
    )

    result = clean_staging(cfg, force=True)
    assert run_id in result.force_removed_run_ids
    assert run_id not in list_staging_run_ids(cfg.staging_root)
    # success batch demoted so retry refetches it instead of losing rows
    statuses = {b["batch_id"]: b["status"] for b in manifest.get_batches_for_run(run_id)}
    assert statuses["batch-0"] == "failed"
    assert statuses["batch-1"] == "failed"


def test_clean_force_dry_run_keeps_manifest_untouched(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    manifest = Manifest(cfg.manifest_path)
    run_id = manifest.start_run("daily", {})
    manifest.start_batch(run_id, "batch-0", "daily_bars", "daily_bars")
    manifest.finish_batch(run_id, "batch-0", "success", rows_written=1)
    manifest.finish_run(run_id, "failed")

    writer = StagingWriter(cfg.staging_root)
    writer.write_batch(
        "daily_bars",
        run_id,
        "batch-0",
        pl.DataFrame([_bar_row("000001.SZ", date(2024, 6, 28))]),
    )

    result = clean_staging(cfg, force=True, dry_run=True)
    assert run_id in result.force_removed_run_ids
    assert run_id in list_staging_run_ids(cfg.staging_root)
    statuses = {b["batch_id"]: b["status"] for b in manifest.get_batches_for_run(run_id)}
    assert statuses["batch-0"] == "success"


def test_clean_removes_orphan_staging_without_manifest(tmp_path):
    import os

    cfg = Config(data_root=tmp_path / "data")
    run_id = "orphan-run"
    writer = StagingWriter(cfg.staging_root)
    writer.write_batch(
        "daily_bars",
        run_id,
        "batch-0",
        pl.DataFrame([_bar_row("000001.SZ", date(2024, 6, 28))]),
    )
    run_dir = cfg.staging_root / "daily_bars" / f"run_id={run_id}"
    old = datetime.now(UTC) - timedelta(days=10)
    os.utime(run_dir, (old.timestamp(), old.timestamp()))

    result = clean_staging(cfg, orphan_retention_days=7)
    assert run_id in result.orphan_run_ids
    assert run_id not in list_staging_run_ids(cfg.staging_root)
