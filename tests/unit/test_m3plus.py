from datetime import date

import polars as pl
import pytest

import stock_data_engine.steps  # noqa: F401
from stock_data_engine.adapters.eastmoney.fundamentals import fetch_financial_statement_items
from stock_data_engine.adapters.eastmoney.index_constituents import fetch_index_constituents
from stock_data_engine.adapters.eastmoney.industry import fetch_industry_members
from stock_data_engine.config import Config, load_config, validate_config
from stock_data_engine.domain.schemas import validate_dataframe
from stock_data_engine.orchestrator.registry import get_step
from stock_data_engine.query import load


class FakeDatacenterClient:
    def __init__(self, batches: dict[str, list[dict]]):
        self.batches = batches

    def get(self, url, **kwargs):
        class Resp:
            def __init__(self, data):
                self._data = data

            def raise_for_status(self):
                return None

            def json(self):
                return {"success": True, "result": {"data": self._data}}

        for key, rows in self.batches.items():
            if key in url:
                return Resp(rows)
        return Resp([])

    def close(self):
        return None


def test_m3plus_steps_registered():
    for name in ("financial_statement_items", "index_constituents", "industry_members"):
        assert get_step(name).fn is not None


def test_example_config_validates_fundamentals_group():
    from pathlib import Path

    cfg = load_config(Path(__file__).resolve().parents[2] / "configs" / "stockdata.example.toml")
    assert validate_config(cfg) == []


def test_financial_statement_items_parses_notice_date():
    client = FakeDatacenterClient(
        {
            "RPT_LICO_FN_CPD": [
                {
                    "SECURITY_CODE": "600519",
                    "REPORTDATE": "2024-03-31",
                    "NOTICE_DATE": "2024-04-28",
                    "TOTAL_OPERATE_INCOME": 100.0,
                    "WEIGHTAVG_ROE": 0.25,
                }
            ]
        }
    )
    df = fetch_financial_statement_items(date(2024, 4, 28), client=client)  # type: ignore[arg-type]
    assert df.height >= 2
    assert set(df["item_code"].to_list()) >= {"revenue", "roe"}
    assert df["announce_date"][0] == date(2024, 4, 28)
    assert df["report_period"][0] == "2024Q1"


def test_index_constituents_schema():
    raw = pl.DataFrame(
        {
            "index_symbol": ["000300.SH"],
            "symbol": ["600519.SH"],
            "as_of_date": [date(2024, 6, 28)],
            "weight": [0.05],
            "source": ["eastmoney"],
            "data_version": ["v1"],
            "fetched_at": ["2024-06-28T00:00:00+00:00"],
        }
    )
    out = validate_dataframe(raw, "index_constituents")
    assert out.height == 1


def test_index_constituents_fetch():
    client = FakeDatacenterClient(
        {
            "RPT_INDEX_CONSTITUENT": [
                {
                    "INDEX_CODE": "000300",
                    "SECURITY_CODE": "600519",
                    "TRADE_DATE": "2024-06-28",
                }
            ]
        }
    )
    df = fetch_index_constituents(date(2024, 6, 28), client=client)  # type: ignore[arg-type]
    assert df.height == 1
    assert df["index_symbol"][0] == "000300.SH"
    assert df["symbol"][0] == "600519.SH"


def test_industry_members_fetch():
    client = FakeDatacenterClient(
        {
            "RPT_BOARD_CONSTITUENT": [
                {
                    "SECURITY_CODE": "600519",
                    "BOARD_CODE": "3405",
                    "BOARD_NAME": "白酒",
                    "BOARD_TYPE_NEW": "2",
                }
            ]
        }
    )
    df = fetch_industry_members(date(2024, 6, 28), client=client)  # type: ignore[arg-type]
    assert df.height == 1
    assert df["industry_name"][0] == "白酒"


@pytest.fixture
def lake(tmp_path):
    root = tmp_path / "data"
    curated = root / "curated"
    fsi = curated / "financial_statement_items" / "report_period=2024Q1"
    fsi.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["600519.SH", "600519.SH"],
            "report_period": ["2024Q1", "2024Q1"],
            "statement_type": ["indicator", "indicator"],
            "item_code": ["roe", "revenue"],
            "item_value": [0.25, 100.0],
            "announce_date": [date(2024, 4, 28), date(2024, 5, 15)],
            "source": ["eastmoney", "eastmoney"],
            "data_version": ["v1", "v1"],
            "fetched_at": ["2024-04-28T00:00:00+00:00", "2024-05-15T00:00:00+00:00"],
        }
    ).write_parquet(fsi / "part-0.parquet")
    return Config(data_root=root)


def test_load_financial_statement_items_by_as_of(lake):
    df = load("financial_statement_items", as_of="2024-04-30", items=["roe"], config=lake)
    assert df.height == 1
    assert df["item_code"][0] == "roe"
