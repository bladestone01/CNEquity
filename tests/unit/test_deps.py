import uuid

import pytest

from stock_data_engine.orchestrator.deps import (
    CyclicDependencyError,
    UnknownStepError,
    step_execution_levels,
    validate_steps_registered,
)
from stock_data_engine.orchestrator.registry import register_step
from stock_data_engine.steps import builtin  # noqa: F401


def test_reference_wave_steps_are_single_parallel_level():
    levels = step_execution_levels(["instruments", "trading_calendar", "trading_status"])
    assert len(levels) == 1
    assert set(levels[0]) == {"instruments", "trading_calendar", "trading_status"}


def test_corp_actions_before_daily_bars_in_sequential_wave():
    levels = step_execution_levels(["corporate_actions", "daily_bars"])
    assert levels[0] == ["corporate_actions"]
    assert levels[1] == ["daily_bars"]


def test_unknown_step_raises():
    with pytest.raises(UnknownStepError, match="not_a_step"):
        validate_steps_registered(["instruments", "not_a_step"])


def test_cyclic_dependency_raises():
    suffix = uuid.uuid4().hex[:8]
    name_a = f"cycle_a_{suffix}"
    name_b = f"cycle_b_{suffix}"

    @register_step(name_a, depends_on=[name_b])
    def _cycle_a(config, trade_date, run_id, context):
        return {}

    @register_step(name_b, depends_on=[name_a])
    def _cycle_b(config, trade_date, run_id, context):
        return {}

    with pytest.raises(CyclicDependencyError):
        step_execution_levels([name_a, name_b])
