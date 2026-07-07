from datetime import date

import polars as pl
import pytest

import stock_data_engine.steps  # noqa: F401
from stock_data_engine.adapters.cninfo.regulatory import fetch_regulatory_events
from stock_data_engine.adapters.eastmoney.share_unlock import fetch_share_unlock_schedule
from stock_data_engine.adapters.macro.indicators import fetch_macro_indicators
from stock_data_engine.config import Config, load_config, validate_config
from stock_data_engine.derive.market_breadth import compute_market_breadth
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


class FakeCninfoClient:
    def __init__(self, announcements: list[dict]):
        self.announcements = announcements

    def post(self, url, data=None, **kwargs):
        class Resp:
            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        return Resp(
            {
                "announcements": self.announcements,
                "hasMore": False,
            }
        )

    def close(self):
        return None


def test_v11_steps_registered():
    for name in (
        "macro_indicators",
        "market_breadth",
        "share_unlock_schedule",
        "regulatory_events",
    ):
        assert get_step(name).fn is not None


def test_example_config_validates_macro_risk_group():
    from pathlib import Path

    cfg = load_config(Path(__file__).resolve().parents[2] / "configs" / "stockdata.example.toml")
    assert validate_config(cfg) == []


def test_macro_indicators_parses_treasury_and_shibor(monkeypatch):
    # Keep the test hermetic: akshare (when installed) would fetch the real
    # full monthly history over the network.
    from stock_data_engine.adapters.macro import indicators as macro_indicators

    monkeypatch.setattr(macro_indicators, "_akshare_rows", lambda _td: [])
    client = FakeDatacenterClient(
        {
            "RPTA_WEB_TREASURYYIELD": [{"SOLAR_DATE": "2024-06-28", "EMM00166466": 2.25}],
            "RPT_IMP_INTRESTRATEN": [{"REPORT_DATE": "2024-06-28", "IR_RATE": 1.85}],
            "RPTA_WEB_RATE": [{"TRADE_DATE": "2024-06-28", "LPR1Y": 3.45}],
        }
    )
    df = fetch_macro_indicators(date(2024, 6, 28), client=client)  # type: ignore[arg-type]
    ids = set(df["indicator_id"].to_list())
    assert {"cnbond_yield_10y", "shibor_3m"}.issubset(ids)
    out = validate_dataframe(
        df.with_columns(
            source=pl.lit("eastmoney"),
            data_version=pl.lit("v1"),
            fetched_at=pl.lit("2024-06-28T00:00:00+00:00"),
        ),
        "macro_indicators",
    )
    assert out["obs_date"][0] == date(2024, 6, 28)


def test_share_unlock_schedule_parses():
    client = FakeDatacenterClient(
        {
            "RPT_LIFT_STAGE": [
                {
                    "SECURITY_CODE": "600519",
                    "FREE_DATE": "2024-08-01",
                    "ABLE_FREE_SHARES": 1_000_000,
                    "FREE_RATIO": 0.5,
                    "FREE_SHARES_TYPE": "首发原股东",
                }
            ]
        }
    )
    df = fetch_share_unlock_schedule(date(2024, 6, 28), client=client)  # type: ignore[arg-type]
    assert df.height == 1
    assert df["symbol"][0] == "600519.SH"
    assert df["unlock_date"][0] == date(2024, 8, 1)


def test_regulatory_events_filters_titles():
    client = FakeCninfoClient(
        [
            {
                "announcementId": "123",
                "secCode": "600519",
                "announcementTitle": "关于收到行政处罚决定书的公告",
            },
            {
                "announcementId": "456",
                "secCode": "600519",
                "announcementTitle": "2024年半年度报告摘要",
            },
        ]
    )
    df = fetch_regulatory_events(date(2024, 6, 28), client=client)  # type: ignore[arg-type]
    assert df.height == 1
    assert df["event_type"][0] == "penalty"
    assert df["event_id"][0] == "reg-123"


@pytest.fixture
def breadth_lake(tmp_path):
    root = tmp_path / "data"
    curated = root / "curated"

    cal = curated / "trading_calendar" / "trade_date=2024-06-27"
    cal.mkdir(parents=True)
    pl.DataFrame(
        {
            "trade_date": [date(2024, 6, 27)],
            "is_trading": [True],
            "source": ["seed"],
            "data_version": ["v1"],
            "fetched_at": ["2024-06-27T00:00:00+00:00"],
        }
    ).write_parquet(cal / "part-0.parquet")

    for d, closes in (
        (date(2024, 6, 27), {"A.SH": 10.0, "B.SH": 20.0, "C.SH": 30.0}),
        (date(2024, 6, 28), {"A.SH": 11.0, "B.SH": 18.0, "C.SH": 30.0}),
    ):
        part = curated / "daily_bars" / f"trade_date={d.isoformat()}"
        part.mkdir(parents=True)
        pl.DataFrame(
            {
                "symbol": list(closes.keys()),
                "trade_date": [d] * 3,
                "open": list(closes.values()),
                "high": list(closes.values()),
                "low": list(closes.values()),
                "close": list(closes.values()),
                "volume": [100, 100, 100],
                "amount": [1000.0, 1000.0, 1000.0],
                "source": ["tdx"] * 3,
                "data_version": ["v1"] * 3,
                "fetched_at": ["2024-06-28T00:00:00+00:00"] * 3,
            }
        ).write_parquet(part / "part-0.parquet")

    return Config(data_root=root)


def test_market_breadth_computed_from_daily_bars(breadth_lake):
    df = compute_market_breadth(breadth_lake, date(2024, 6, 28))
    assert df.height == 7
    metrics = dict(zip(df["metric_id"].to_list(), df["value"].to_list(), strict=True))
    assert metrics["advance_count"] == 1.0
    assert metrics["decline_count"] == 1.0
    assert metrics["flat_count"] == 1.0
    assert metrics["total_count"] == 3.0


def test_load_macro_indicators_by_date_range(tmp_path):
    root = tmp_path / "data"
    part = root / "curated" / "macro_indicators" / "obs_date=2024-06-28"
    part.mkdir(parents=True)
    pl.DataFrame(
        {
            "indicator_id": ["shibor_3m"],
            "obs_date": [date(2024, 6, 28)],
            "value": [1.85],
            "frequency": ["daily"],
            "source": ["eastmoney"],
            "data_version": ["v1"],
            "fetched_at": ["2024-06-28T00:00:00+00:00"],
        }
    ).write_parquet(part / "part-0.parquet")
    cfg = Config(data_root=root)
    df = load("macro_indicators", start="2024-06-28", end="2024-06-28", config=cfg)
    assert df.height == 1
    assert df["indicator_id"][0] == "shibor_3m"


def test_parse_series_obs_date_handles_month_formats():
    from stock_data_engine.adapters.macro.indicators import _parse_series_obs_date

    assert _parse_series_obs_date("2024-06-28") == date(2024, 6, 28)
    assert _parse_series_obs_date("2024-06") == date(2024, 6, 30)
    assert _parse_series_obs_date("2024年6月份") == date(2024, 6, 30)
    assert _parse_series_obs_date("2024年12月") == date(2024, 12, 31)
    assert _parse_series_obs_date("garbage") is None
    assert _parse_series_obs_date(None) is None
