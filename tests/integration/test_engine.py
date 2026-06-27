from datetime import date

import pytest

from stock_data_engine.catalog.init_layout import init_data_layout
from stock_data_engine.config import validate_config
from stock_data_engine.orchestrator.engine import JobEngine
from stock_data_engine.orchestrator.manifest import Manifest
from stock_data_engine.steps import builtin  # noqa: F401 — register steps

pytestmark = pytest.mark.integration


def test_validate_config(config):
    assert validate_config(config) == []


def test_init_layout(config):
    init_data_layout(config)
    assert config.manifest_path.exists()
    assert config.curated_root.exists()


def test_manifest_run(config):
    init_data_layout(config)
    m = Manifest(config.manifest_path)
    run_id = m.start_run("test")
    m.start_batch(run_id, "b1", "t1", "instruments")
    m.finish_batch(run_id, "b1", "success", rows_written=10)
    m.finish_run(run_id, "success", rows_written=10)
    summary = m.run_summary(run_id)
    assert summary["batch_counts"]["success"] == 1


def test_daily_job_mock(config):
    init_data_layout(config)
    engine = JobEngine(config)
    result = engine.run_job("daily", date(2024, 6, 28))
    assert result["run_id"]
    assert result["status"] in ("success", "failed")
