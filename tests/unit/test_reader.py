from datetime import UTC, date, datetime

import polars as pl
import pytest

from stock_data_engine.config import Config
from stock_data_engine.query.reader import ReaderError, load, resolve_config


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
    exact = dict(zip(df["symbol"].to_list(), df["adj_is_exact"].to_list(), strict=True))
    assert exact["600519.SH"] is True
    assert exact["000001.SZ"] is True
    assert exact["300750.SZ"] is False
    moutai = df.filter(pl.col("symbol") == "600519.SH")
    assert moutai["adj_close"][0] == pytest.approx(21.0)
    assert moutai["adj_is_exact"][0] is True


def test_load_strict_adj_raises_when_factor_missing(lake):
    with pytest.raises(ReaderError, match="missing adj_factors"):
        load(
            "daily_bars",
            start="2024-06-27",
            end="2024-06-27",
            adjust="hfq",
            strict_adj=True,
            config=lake,
        )


def test_load_daily_bars_universe_filters_st_and_suspended(lake):
    df = load(
        "daily_bars",
        start="2024-06-27",
        end="2024-06-27",
        universe="all_a",
        config=lake,
    )
    assert set(df["symbol"].to_list()) == {"600519.SH"}


def test_load_daily_bars_qfq_derived_from_hfq(lake):
    bars_dir = lake.curated_root / "daily_bars" / "trade_date=2024-06-26"
    bars_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "symbol": ["600519.SH"],
            "trade_date": [date(2024, 6, 26)],
            "open": [10.0],
            "high": [10.0],
            "low": [10.0],
            "close": [10.0],
            "volume": [1000],
            "amount": [10000.0],
            **_prov(),
        }
    ).write_parquet(bars_dir / "part-0.parquet")

    for td, factor in ((date(2024, 6, 26), 2.0), (date(2024, 6, 27), 4.0)):
        adj_dir = lake.derived_root / "adj_factors" / f"trade_date={td.isoformat()}"
        adj_dir.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(
            {
                "symbol": ["600519.SH"],
                "trade_date": [td],
                "adjust_type": ["hfq"],
                "factor": [factor],
                **_prov("sina"),
            }
        ).write_parquet(adj_dir / "part-0.parquet")

    df = load(
        "daily_bars",
        start="2024-06-26",
        end="2024-06-27",
        adjust="qfq",
        config=lake,
    )
    moutai = df.filter(pl.col("symbol") == "600519.SH").sort("trade_date")
    assert moutai["adj_close"][0] == pytest.approx(5.0)  # 10 * (2/4)
    assert moutai["adj_close"][1] == pytest.approx(10.5)  # anchor date
    assert moutai["adj_is_exact"].all()


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


def test_load_raises_when_dataset_has_no_parquet_files(lake):
    with pytest.raises(ReaderError, match="no parquet data for dataset 'corporate_actions'"):
        load("corporate_actions", config=lake)


def test_resolve_config_raises_without_stockdata_toml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ReaderError, match="No config found"):
        resolve_config()


def test_load_raises_when_data_root_has_no_dataset(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    with pytest.raises(ReaderError, match="no parquet data for dataset 'daily_bars'"):
        load("daily_bars", config=cfg)


def test_load_index_bars_rejects_universe_filter(lake):
    with pytest.raises(ReaderError, match="index symbols are not in all_a"):
        load("index_bars", universe="all_a", config=lake)
