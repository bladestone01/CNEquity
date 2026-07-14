"""Unit tests for EastMoney rotation adapters."""

from datetime import date
from unittest.mock import MagicMock

import polars as pl

from stock_data_engine.adapters.eastmoney.rotation import (
    _hot_symbol,
    fetch_hot_rank,
    fetch_news_headlines,
    fetch_sector_bars,
    fetch_sector_bars_history,
)


def test_hot_symbol_parsing():
    assert _hot_symbol("SZ002185") == "002185.SZ"
    assert _hot_symbol("SH600519") == "600519.SH"


def test_fetch_hot_rank_normalizes(monkeypatch):
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "data": [{"sc": "SZ002185", "rk": 1, "rc": 2, "hisRc": 3}],
    }
    mock_client.post.return_value = mock_resp

    class CM:
        def __enter__(self):
            return mock_client

        def __exit__(self, *a):
            pass

    monkeypatch.setattr(
        "stock_data_engine.adapters.eastmoney.rotation.EastMoneyClient", lambda: CM()
    )
    df = fetch_hot_rank(date(2026, 7, 14), top_n=10)
    assert df.height == 1
    assert df["symbol"][0] == "002185.SZ"
    assert df["rank"][0] == 1


def test_fetch_sector_bars_from_clist(monkeypatch):
    mock_client = MagicMock()
    monkeypatch.setattr(
        "stock_data_engine.adapters.eastmoney.rotation.fetch_clist_pages",
        lambda client, **kw: [
            {
                "f12": "BK1630",
                "f14": "测试板块",
                "f2": 100.0,
                "f3": 1.5,
                "f15": 101.0,
                "f16": 99.0,
                "f17": 98.0,
                "f5": 1000,
                "f6": 5000000.0,
                "f8": 2.0,
                "f62": 12345.0,
            }
        ],
    )

    class CM:
        def __enter__(self):
            return mock_client

        def __exit__(self, *a):
            pass

    monkeypatch.setattr(
        "stock_data_engine.adapters.eastmoney.rotation.EastMoneyClient", lambda: CM()
    )
    df = fetch_sector_bars(date(2026, 7, 14))
    assert df.height >= 1
    row = df.filter(pl.col("sector_code") == "BK1630")
    assert row["close"][0] == 100.0


def test_fetch_sector_bars_history_parses_klines(monkeypatch):
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "data": {
            "code": "BK1630",
            "klines": [
                "2026-07-10,98.0,100.0,101.0,97.5,1000,5000000.0,3.5,1.5,1.5,2.0",
                "2026-07-13,100.0,102.0,103.0,99.0,1200,6000000.0,4.0,2.0,2.0,2.1",
            ],
        }
    }
    mock_client.get.return_value = mock_resp
    monkeypatch.setattr(
        "stock_data_engine.adapters.eastmoney.rotation.fetch_clist_pages",
        lambda client, **kw: [{"f12": "BK1630", "f14": "测试板块"}],
    )

    class CM:
        def __enter__(self):
            return mock_client

        def __exit__(self, *a):
            pass

    monkeypatch.setattr(
        "stock_data_engine.adapters.eastmoney.rotation.EastMoneyClient", lambda *a, **kw: CM()
    )
    df, failed, succeeded = fetch_sector_bars_history(date(2026, 6, 1), date(2026, 7, 14))
    assert failed == []
    assert succeeded == ["BK1630"]
    assert df.height == 2
    day = df.filter(pl.col("trade_date") == date(2026, 7, 13))
    assert day["open"][0] == 100.0
    assert day["close"][0] == 102.0
    assert day["high"][0] == 103.0
    assert day["low"][0] == 99.0
    assert day["volume"][0] == 1200
    assert day["change_pct"][0] == 2.0


def test_fetch_sector_bars_history_reports_failures(monkeypatch):
    mock_client = MagicMock()
    mock_client.get.side_effect = RuntimeError("timeout")
    monkeypatch.setattr(
        "stock_data_engine.adapters.eastmoney.rotation.fetch_clist_pages",
        lambda client, **kw: [{"f12": "BK9999", "f14": "坏板块"}],
    )

    class CM:
        def __enter__(self):
            return mock_client

        def __exit__(self, *a):
            pass

    monkeypatch.setattr(
        "stock_data_engine.adapters.eastmoney.rotation.EastMoneyClient", lambda *a, **kw: CM()
    )
    df, failed, succeeded = fetch_sector_bars_history(date(2026, 6, 1), date(2026, 7, 14))
    assert df.is_empty()
    assert failed == ["BK9999"]
    assert succeeded == []


def test_fetch_sector_bars_history_failover_host(monkeypatch):
    mock_client = MagicMock()
    bad = MagicMock()
    bad.get.side_effect = RuntimeError("disconnect")
    good = MagicMock()
    good.json.return_value = {
        "data": {
            "klines": ["2026-07-13,100.0,102.0,103.0,99.0,1200,6000000.0,4.0,2.0,2.0,2.1"],
        }
    }
    calls = {"n": 0}

    def get_side_effect(url, **kwargs):
        calls["n"] += 1
        if "push2his.eastmoney.com" in url and "91." not in url:
            raise RuntimeError("disconnect")
        resp = MagicMock()
        resp.json.return_value = good.json.return_value
        resp.raise_for_status = MagicMock()
        return resp

    mock_client.get.side_effect = get_side_effect
    monkeypatch.setattr(
        "stock_data_engine.adapters.eastmoney.rotation.fetch_clist_pages",
        lambda client, **kw: [{"f12": "BK1630", "f14": "测试板块"}],
    )

    class CM:
        def __enter__(self):
            return mock_client

        def __exit__(self, *a):
            pass

    monkeypatch.setattr(
        "stock_data_engine.adapters.eastmoney.rotation.EastMoneyClient", lambda *a, **kw: CM()
    )
    df, failed, succeeded = fetch_sector_bars_history(date(2026, 6, 1), date(2026, 7, 14))
    assert failed == []
    assert succeeded == ["BK1630"]
    assert df.height == 1
    assert calls["n"] >= 2


def test_fetch_sector_bars_history_skips_completed(monkeypatch):
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"data": {"klines": []}}
    mock_client.get.return_value = mock_resp
    monkeypatch.setattr(
        "stock_data_engine.adapters.eastmoney.rotation.fetch_clist_pages",
        lambda client, **kw: [
            {"f12": "BK1630", "f14": "已完成"},
            {"f12": "BK1631", "f14": "待拉"},
        ],
    )

    class CM:
        def __enter__(self):
            return mock_client

        def __exit__(self, *a):
            pass

    monkeypatch.setattr(
        "stock_data_engine.adapters.eastmoney.rotation.EastMoneyClient", lambda *a, **kw: CM()
    )
    df, failed, succeeded = fetch_sector_bars_history(
        date(2026, 6, 1), date(2026, 7, 14), skip_sectors={"BK1630"}
    )
    assert "BK1630" not in succeeded
    assert "BK1631" in succeeded
    assert mock_client.get.call_count >= 1


def test_fetch_news_headlines_filters_date(monkeypatch):
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "data": {
            "fastNewsList": [
                {
                    "code": "n1",
                    "showTime": "2026-07-14 16:00:00",
                    "title": "测试新闻",
                    "summary": "摘要",
                    "stockList": ["0.600519"],
                },
                {
                    "code": "n2",
                    "showTime": "2026-07-13 16:00:00",
                    "title": "旧新闻",
                },
            ]
        }
    }
    mock_client.get.return_value = mock_resp

    class CM:
        def __enter__(self):
            return mock_client

        def __exit__(self, *a):
            pass

    monkeypatch.setattr(
        "stock_data_engine.adapters.eastmoney.rotation.EastMoneyClient", lambda: CM()
    )
    df = fetch_news_headlines(date(2026, 7, 14))
    assert df.height == 1
    assert df["news_id"][0] == "n1"
    assert "600519.SH" in df["related_symbols"][0]
