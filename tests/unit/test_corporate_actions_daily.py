from datetime import date
from unittest.mock import patch

import polars as pl
import pytest

import stock_data_engine.steps  # noqa: F401
from stock_data_engine.config import Config
from stock_data_engine.steps.events import step_corporate_actions


def test_corporate_actions_daily_uses_eastmoney(tmp_path):
    cfg = Config(data_root=tmp_path / "data", sources={"eastmoney": True})
    em_df = pl.DataFrame(
        {
            "symbol": ["600519.SH"],
            "ex_date": [date(2024, 6, 28)],
            "action_type": ["cash_dividend"],
            "cash_dividend": [10.0],
            "bonus_ratio": [0.0],
            "transfer_ratio": [0.0],
            "allotment_ratio": [None],
            "allotment_price": [None],
        }
    )
    with patch(
        "stock_data_engine.steps.events.fetch_corporate_actions_eastmoney",
        return_value=em_df,
    ):
        result = step_corporate_actions(cfg, date(2024, 6, 28), "run-1", {})

    assert result["context_updates"]["symbols_to_rebackfill"] == ["600519.SH"]
    staged = list((cfg.staging_root / "corporate_actions").glob("**/*.parquet"))
    assert staged
    df = pl.read_parquet(staged[0])
    assert df["source"][0] == "eastmoney"


def test_corporate_actions_daily_empty_is_ok(tmp_path):
    cfg = Config(data_root=tmp_path / "data", sources={"eastmoney": True})
    with patch(
        "stock_data_engine.steps.events.fetch_corporate_actions_eastmoney",
        return_value=pl.DataFrame(),
    ):
        result = step_corporate_actions(cfg, date(2024, 6, 28), "run-1", {})

    assert result["context_updates"]["symbols_to_rebackfill"] == []
    assert result["rows_written"] == 0
