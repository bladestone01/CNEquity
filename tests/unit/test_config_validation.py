from pathlib import Path

import ashare_lake.steps  # noqa: F401 — register steps
from ashare_lake.config import Config, ScheduleGroup, WaveConfig, load_config, validate_config


def test_validate_config_rejects_unknown_group_step(tmp_path):
    cfg = Config(
        data_root=tmp_path / "data",
        daily_waves=[WaveConfig(name="core", parallel=True, steps=["instruments"])],
        schedule_groups={
            "capital": ScheduleGroup(at="16:30", steps=["not_a_dataset_step"]),
        },
    )
    errors = validate_config(cfg)
    assert any("unknown step 'not_a_dataset_step'" in err for err in errors)


def test_validate_config_rejects_invalid_tdx_servers(tmp_path):
    cfg_path = tmp_path / "test.toml"
    cfg_path.write_text(
        f"""
[data]
root = "{tmp_path / "data"}"

[[job.daily.waves]]
name = "core"
parallel = true
steps = ["instruments"]

[tdx_protocol]
servers = "not-a-server"
"""
    )
    cfg = load_config(cfg_path)
    errors = validate_config(cfg)
    assert any("servers must be" in e for e in errors)


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
    cfg = load_config(root / "configs" / "ashare-lake.example.toml")
    assert validate_config(cfg) == []
    # Free-source anti-blacklist defaults (time may be slow; bans are worse).
    assert cfg.source_intervals["baostock"] == 1.0
    assert cfg.baostock_batch_size == 20
    assert cfg.baostock_batch_rest_seconds == 120.0
    # EastMoney pacing is a floor, not a fixed value: push2his bans bursty
    # overseas IPs, so the example config may raise it further. AKShare mostly
    # wraps EastMoney, so it must never be faster or the two share a ban.
    assert cfg.source_intervals["eastmoney"] >= 1.0
    assert cfg.source_intervals["akshare"] <= cfg.source_intervals["eastmoney"]
    assert cfg.tdx_min_interval_ms == 100
