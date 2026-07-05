from datetime import date

import polars as pl
import pytest

import stock_data_engine.steps  # noqa: F401
from stock_data_engine.adapters.eastmoney.consensus import fetch_analyst_consensus
from stock_data_engine.adapters.eastmoney.institutional import fetch_institutional_holdings
from stock_data_engine.adapters.macro.indicators import _akshare_rows
from stock_data_engine.config import Config, load_config, validate_config
from stock_data_engine.derive.sentiment_scores import compute_sentiment_scores
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
                return {"result": {"data": self._data}}

        for key, rows in self.batches.items():
            if key in url:
                return Resp(rows)
        return Resp([])

    def close(self):
        return None


def test_p2_steps_registered():
    for name in ("institutional_holdings", "analyst_consensus", "sentiment_scores"):
        assert get_step(name).fn is not None


def test_example_config_validates_research_group():
    from pathlib import Path

    cfg = load_config(Path(__file__).resolve().parents[2] / "configs" / "stockdata.example.toml")
    assert validate_config(cfg) == []


def test_institutional_holdings_parses():
    client = FakeDatacenterClient(
        {
            "RPT_MAIN_ORGHOLD": [
                {
                    "SECURITY_CODE": "600519",
                    "REPORT_DATE": "2024-03-31",
                    "ORG_TYPE": "基金",
                    "ORG_NUM": 120,
                    "HOLD_RATIO": 8.5,
                    "HOLD_MARKET_CAP": 1_000_000_000,
                    "NOTICE_DATE": "2024-04-28",
                }
            ]
        }
    )
    df = fetch_institutional_holdings(date(2024, 4, 28), client=client)  # type: ignore[arg-type]
    assert df.height == 1
    assert df["holder_type"][0] == "fund"
    assert df["report_period"][0] == "2024Q1"


def test_analyst_consensus_parses():
    client = FakeDatacenterClient(
        {
            "RPTA_WEB_RES_PROFIT": [
                {
                    "SECURITY_CODE": "600519",
                    "PUBLISH_DATE": "2024-06-28",
                    "FORECAST_YEAR": 2024,
                    "FORECAST_EPS": 50.5,
                    "FORECAST_PE": 25.0,
                    "TARGET_PRICE": 1800.0,
                    "RATING": "买入",
                    "ORG_NUM": 12,
                }
            ]
        }
    )
    df = fetch_analyst_consensus(date(2024, 6, 28), client=client)  # type: ignore[arg-type]
    assert df.height == 1
    assert df["symbol"][0] == "600519.SH"
    assert df["eps_forecast"][0] == 50.5


@pytest.fixture
def sentiment_lake(tmp_path):
    root = tmp_path / "data"
    part = root / "curated" / "announcement_index" / "announce_date=2024-06-28"
    part.mkdir(parents=True)
    pl.DataFrame(
        {
            "announcement_id": ["a1", "a2", "a3"],
            "symbol": ["600519.SH", "600519.SH", "000001.SZ"],
            "title": ["业绩超预期增长", "分红方案公布", "日常经营公告"],
            "announce_date": [date(2024, 6, 28)] * 3,
            "category": ["", "", ""],
            "url": ["", "", ""],
            "source": ["cninfo"] * 3,
            "data_version": ["v1"] * 3,
            "fetched_at": ["2024-06-28T00:00:00+00:00"] * 3,
        }
    ).write_parquet(part / "part-0.parquet")
    return Config(data_root=root)


def test_sentiment_scores_from_announcements(sentiment_lake):
    df = compute_sentiment_scores(sentiment_lake, date(2024, 6, 28))
    ann = df.filter(pl.col("score_channel") == "announcement_keywords")
    assert ann.height == 2
    moutai = ann.filter(pl.col("symbol") == "600519.SH")
    assert moutai["sentiment_score"][0] > 0
    assert moutai["headline_count"][0] == 2


def test_sentiment_scores_schema(sentiment_lake):
    raw = (
        compute_sentiment_scores(sentiment_lake, date(2024, 6, 28))
        .filter(pl.col("score_channel") == "announcement_keywords")
        .with_columns(
            source=pl.lit("derived"),
            data_version=pl.lit("v1"),
            fetched_at=pl.lit("2024-06-28T00:00:00+00:00"),
        )
    )
    out = validate_dataframe(raw, "sentiment_scores")
    assert out.height == 2


def test_load_analyst_consensus(tmp_path):
    root = tmp_path / "data"
    part = root / "curated" / "analyst_consensus" / "forecast_date=2024-06-28"
    part.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["600519.SH"],
            "forecast_date": [date(2024, 6, 28)],
            "forecast_year": [2024],
            "eps_forecast": [50.5],
            "pe_forecast": [25.0],
            "target_price": [1800.0],
            "rating": ["买入"],
            "analyst_count": [12],
            "source": ["eastmoney"],
            "data_version": ["v1"],
            "fetched_at": ["2024-06-28T00:00:00+00:00"],
        }
    ).write_parquet(part / "part-0.parquet")
    cfg = Config(data_root=root)
    df = load("analyst_consensus", start="2024-06-28", end="2024-06-28", config=cfg)
    assert df.height == 1


def test_akshare_import_available():
    akshare = pytest.importorskip("akshare")
    assert akshare is not None


def test_akshare_macro_rows_returns_list():
    pytest.importorskip("akshare")
    rows = _akshare_rows(date(1900, 1, 1))
    assert isinstance(rows, list)
