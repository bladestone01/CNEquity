from datetime import date

import polars as pl
import pytest

import stock_data_engine.steps  # noqa: F401
from stock_data_engine.config import Config, ScheduleGroup, WaveConfig, validate_config
from stock_data_engine.domain.schemas import validate_dataframe
from stock_data_engine.orchestrator.registry import get_step


def test_m3_steps_are_registered():
    for name in (
        "fund_flow",
        "northbound_holdings",
        "northbound_flows",
        "margin_trading",
        "valuation_metrics",
        "sector_members",
        "announcement_index",
        "dragon_tiger",
        "block_trades",
    ):
        entry = get_step(name)
        assert entry.fn is not None


def test_example_config_validates_with_m3_groups():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    from stock_data_engine.config import load_config

    cfg = load_config(root / "configs" / "stockdata.example.toml")
    assert validate_config(cfg) == []


def test_fund_flow_schema_normalization():
    raw = pl.DataFrame(
        {
            "symbol": ["600519.SH"],
            "trade_date": [date(2024, 6, 28)],
            "main_net_inflow": [1_000_000.0],
            "super_large_net_inflow": [500_000.0],
            "large_net_inflow": [300_000.0],
            "medium_net_inflow": [100_000.0],
            "small_net_inflow": [100_000.0],
            "source": ["eastmoney"],
            "data_version": ["v1"],
            "fetched_at": ["2024-06-28T00:00:00+00:00"],
        }
    )
    out = validate_dataframe(raw, "fund_flow")
    assert out.height == 1


@pytest.fixture
def cfg(tmp_path):
    return Config(data_root=tmp_path / "data")


def test_step_fund_flow_writes_staging(cfg, monkeypatch):
    from stock_data_engine.steps import capital as cap

    def fake_fetch(trade_date, **kwargs):
        return pl.DataFrame(
            {
                "symbol": ["600519.SH"],
                "trade_date": [trade_date],
                "main_net_inflow": [1.0],
                "super_large_net_inflow": [0.0],
                "large_net_inflow": [0.0],
                "medium_net_inflow": [0.0],
                "small_net_inflow": [0.0],
            }
        )

    monkeypatch.setattr(cap, "fetch_fund_flow", fake_fetch)
    cfg.staging_root.mkdir(parents=True)
    result = cap.step_fund_flow(cfg, date(2024, 6, 28), "run-1", {})
    assert result["rows_written"] == 1
    staged = list(cfg.staging_root.glob("fund_flow/**/*.parquet"))
    assert len(staged) == 1


def test_validate_config_accepts_capital_group(tmp_path):
    cfg = Config(
        data_root=tmp_path / "data",
        daily_waves=[WaveConfig(name="core", parallel=True, steps=["instruments"])],
        schedule_groups={
            "capital": ScheduleGroup(at="16:30", steps=["fund_flow", "margin_trading"]),
        },
    )
    assert validate_config(cfg) == []
