from pathlib import Path

import stock_data_engine.steps  # noqa: F401 — register steps
from stock_data_engine.config import Config, ScheduleGroup, WaveConfig, load_config, validate_config


def test_validate_config_rejects_unknown_group_step(tmp_path):
    cfg = Config(
        data_root=tmp_path / "data",
        daily_waves=[WaveConfig(name="core", parallel=True, steps=["instruments"])],
        schedule_groups={
            "capital": ScheduleGroup(at="16:30", steps=["fund_flow"]),
        },
    )
    errors = validate_config(cfg)
    assert any("unknown step 'fund_flow'" in err for err in errors)


def test_validate_config_accepts_registered_waves(tmp_path):
    cfg = Config(
        data_root=tmp_path / "data",
        daily_waves=[
            WaveConfig(
                name="reference",
                parallel=True,
                steps=["instruments", "trading_calendar"],
            )
        ],
    )
    assert validate_config(cfg) == []


def test_example_config_validates():
    root = Path(__file__).resolve().parents[2]
    cfg = load_config(root / "configs" / "stockdata.example.toml")
    assert validate_config(cfg) == []
