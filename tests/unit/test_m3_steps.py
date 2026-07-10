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
    from stock_data_engine.storage.state import StateStore

    StateStore(cfg.meta_root).set_date("fund_flow", date(2024, 6, 27))

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


def test_valuation_snapshot_filters_to_bar_universe(cfg, monkeypatch):
    """The EastMoney clist returns delisted names with no bar; the daily snapshot
    must drop them so valuation stays in lock-step with daily_bars (audit:
    valuation_bars_orphan_symbol)."""
    from stock_data_engine.steps import fundamentals as fund
    from stock_data_engine.storage.state import StateStore

    # Bar universe: only 600519.SH has ever traded.
    bars_part = cfg.curated_root / "daily_bars" / "trade_date=2024-06-28"
    bars_part.mkdir(parents=True)
    pl.DataFrame({"symbol": ["600519.SH"], "trade_date": [date(2024, 6, 28)]}).write_parquet(
        bars_part / "part-merged.parquet"
    )

    StateStore(cfg.meta_root).set_date("valuation_metrics", date(2024, 6, 27))

    def fake_fetch(trade_date, **kwargs):
        return pl.DataFrame(
            {
                # 600519.SH trades; 000003.SZ is a delisted orphan the clist still returns.
                "symbol": ["600519.SH", "000003.SZ"],
                "trade_date": [trade_date, trade_date],
                "pe_ttm": [30.0, 1.0],
                "pb": [9.0, 0.1],
                "ps_ttm": [12.0, 0.5],
                "total_mv": [2.0e12, 1.0e8],
                "float_mv": [2.0e12, 1.0e8],
            }
        )

    monkeypatch.setattr(fund, "fetch_valuation_metrics", fake_fetch)
    cfg.staging_root.mkdir(parents=True)
    result = fund.step_valuation_metrics(cfg, date(2024, 6, 28), "run-1", {})

    assert result["rows_written"] == 1
    staged = pl.read_parquet(list(cfg.staging_root.glob("valuation_metrics/**/*.parquet")))
    assert staged["symbol"].to_list() == ["600519.SH"]


def test_validate_config_accepts_capital_group(tmp_path):
    cfg = Config(
        data_root=tmp_path / "data",
        daily_waves=[WaveConfig(name="core", parallel=True, steps=["instruments"])],
        schedule_groups={
            "capital": ScheduleGroup(at="16:30", steps=["fund_flow", "margin_trading"]),
        },
    )
    assert validate_config(cfg) == []


def test_fflow_kline_url_is_formattable_string():
    """Regression: _FFLOW_KLINE_URL must be a str (a stray comma made it a tuple)."""
    from stock_data_engine.adapters.eastmoney import capital

    assert isinstance(capital._FFLOW_KLINE_URL, str)
    assert "lmt=5" in capital._FFLOW_KLINE_URL.format(limit=5)
