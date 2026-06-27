from datetime import date

import pytest

from stock_data_engine.catalog.init_layout import init_data_layout
from stock_data_engine.orchestrator.deps import UnknownStepError
from stock_data_engine.orchestrator.engine import JobEngine
from stock_data_engine.steps import builtin  # noqa: F401


def test_run_job_rejects_unknown_steps(config):
    init_data_layout(config)
    engine = JobEngine(config)
    with pytest.raises(UnknownStepError, match="not_registered"):
        engine.run_job("daily", date(2024, 6, 28), steps=["not_registered"])
