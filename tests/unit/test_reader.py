from datetime import UTC, date, datetime

import polars as pl
import pytest

from stock_data_engine.config import Config
from stock_data_engine.query.reader import ReaderError, load


def _prov(source: str = "test") -> dict:
    return {
        "source": source,
        "data_version": "v1",
        "fetched_at": datetime(2024, 6, 28, tzinfo=UTC),
    }


@pytest.fixture
def lake(tmp_path):
    root = tmp_path / "data"
    curated = root / "curated"
    derived = root / "derived"

    (curated / "instruments").mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["600519.SH", "000001.SZ", "300750.SZ"],
            "name": ["Moutai", "PingAn", "CATL"],
            "exchange": ["SH", "SZ", "SZ"],
            "asset_type": ["stock", "stock", "stock"],
            "list_date": [date(2001, 8, 27), date(1991, 4, 3), date(2017, 6, 11)],
            "delist_date": [None, None, None],
            **_prov(),
        }
    ).write_parquet(curated / "instruments" / "part-merged.parquet")

    bars_dir = curated / "daily_bars" / "trade_date=2024-06-27"
    bars_dir.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["600519.SH", "000001.SZ", "300750.SZ"],
            "trade_date": [date(2024, 6, 27)] * 3,
            "open": [10.0, 20.0, 30.0],
            "high": [11.0, 21.0, 31.0],
            "low": [9.0, 19.0, 29.0],
            "close": [10.5, 20.5, 30.5],
            "volume": [1000, 2000, 3000],
            "amount": [10500.0, 41000.0, 91500.0],
            **_prov(),
        }
    ).write_parquet(bars_dir / "part-0.parquet")

    status_dir = curated / "trading_status" / "trade_date=2024-06-27"
    status_dir.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["600519.SH", "000001.SZ", "300750.SZ"],
            "trade_date": [date(2024, 6, 27)] * 3,
            "is_trading": [True, True, False],
            "status": ["normal", "st", "suspended"],
            **_prov("eastmoney"),
        }
    ).write_parquet(status_dir / "part-0.parquet")

    adj_dir = derived / "adj_factors" / "trade_date=2024-06-27"
    adj_dir.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["600519.SH", "000001.SZ"],
            "trade_date": [date(2024, 6, 27)] * 2,
            "adjust_type": ["hfq", "hfq"],
            "factor": [2.0, 3.0],
            **_prov("sina"),
        }
    ).write_parquet(adj_dir / "part-0.parquet")

    fsi_dir = curated / "financial_statement_items" / "report_period=2024Q1"
    fsi_dir.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["600519.SH", "600519.SH"],
            "report_period": ["2024Q1", "2024Q1"],
            "statement_type": ["income", "income"],
            "item_code": ["roe", "revenue"],
            "item_value": [0.25, 1_000_000.0],
            "announce_date": [date(2024, 4, 28), date(2024, 5, 15)],
            **_prov(),
        }
    ).write_parquet(fsi_dir / "part-0.parquet")

    return Config(data_root=root)


def test_load_daily_bars_with_adjustment(lake):
    df = load(
        "daily_bars",
        start="2024-06-27",
        end="2024-06-27",
        adjust="hfq",
        config=lake,
    )
    assert df.height == 3
    moutai = df.filter(pl.col("symbol") == "600519.SH")
    assert moutai["adj_close"][0] == pytest.approx(21.0)


def test_load_daily_bars_universe_filters_st_and_suspended(lake):
    df = load(
        "daily_bars",
        start="2024-06-27",
        end="2024-06-27",
        universe="all_a",
        config=lake,
    )
    assert set(df["symbol"].to_list()) == {"600519.SH"}


def test_load_financial_statement_items_pit(lake):
    df = load(
        "financial_statement_items",
        as_of="2024-04-30",
        items=["roe"],
        config=lake,
    )
    assert df.height == 1
    assert df["item_code"][0] == "roe"
    assert df["announce_date"][0] == date(2024, 4, 28)


def test_load_financial_statement_items_requires_as_of(lake):
    with pytest.raises(ReaderError, match="requires as_of"):
        load("financial_statement_items", config=lake)


def test_load_empty_dataset_returns_typed_frame(lake):
    df = load("corporate_actions", config=lake)
    assert df.is_empty()
    assert "ex_date" in df.columns
