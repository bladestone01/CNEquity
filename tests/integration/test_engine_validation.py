from datetime import date

import pytest

import ashare_lake.steps  # noqa: F401 — register steps
from ashare_lake.orchestrator.deps import UnknownStepError
from ashare_lake.orchestrator.engine import JobEngine
from ashare_lake.storage.layout import init_data_layout


def test_run_job_rejects_unknown_steps(config):
    init_data_layout(config)
    engine = JobEngine(config)
    with pytest.raises(UnknownStepError, match="not_registered"):
        engine.run_job("daily", date(2024, 6, 28), steps=["not_registered"])
