import json
from datetime import date

import polars as pl
import pytest

from stock_data_engine.adapters.sina.adj_factors import (
    _parse_sina_factor_payload,
    fetch_adj_factor_series,
    to_sina_symbol,
)
from stock_data_engine.config import load_config
from stock_data_engine.derive.adj_factors import _align_factors_to_bars, compute_adj_factors


def test_to_sina_symbol():
    assert to_sina_symbol("600519.SH") == "sh600519"
    assert to_sina_symbol("000001.SZ") == "sz000001"


def test_parse_sina_qfq_payload():
    payload = {"data": [{"date": "2024-06-28", "qfq_factor": "2.0"}]}
    text = f"var foo = {json.dumps(payload)};"
    rows = _parse_sina_factor_payload(text)
    assert rows[0]["date"] == "2024-06-28"


def test_fetch_adj_factor_series_qfq():
    payload = {
        "data": [
            {"date": "2024-06-27", "qfq_factor": "2.0"},
            {"date": "2024-06-28", "qfq_factor": "2.0"},
        ]
    }
    body = f"var foo = {json.dumps(payload)};"

    class FakeResponse:
        text = body

        def raise_for_status(self):
            return None

    class FakeClient:
        def get(self, url):
            assert "qfq.js" in url
            return FakeResponse()

        def close(self):
            return None

    df = fetch_adj_factor_series("600519.SH", "qfq", client=FakeClient())
    assert df["factor"].to_list() == [0.5, 0.5]


def test_align_factors_to_bars_forward_fill():
    bars = pl.DataFrame(
        {
            "symbol": ["600519.SH"] * 3,
            "trade_date": [date(2024, 6, 26), date(2024, 6, 27), date(2024, 6, 28)],
        }
    )
    factors = pl.DataFrame(
        {
            "trade_date": [date(2024, 6, 27)],
            "factor": [0.5],
        }
    )
    aligned = _align_factors_to_bars(bars, "600519.SH", factors, "qfq")
    assert aligned["factor"].to_list() == [1.0, 0.5, 0.5]


@pytest.fixture
def adj_config(tmp_path, monkeypatch):
    cfg_path = tmp_path / "test.toml"
    data_root = tmp_path / "data"
    cfg_path.write_text(
        f"""
[data]
root = "{data_root}"

[orchestrator]
workers = 1

[sources.sina]
enabled = true
min_interval_seconds = 0

[adj_factors]
source = "sina"
adjust_types = ["qfq"]

[[job.daily.waves]]
name = "finalize"
parallel = false
steps = ["derive_adj_factors"]
"""
    )
    cfg = load_config(cfg_path)
    bars_dir = cfg.curated_root / "daily_bars" / "trade_date=2024-06-28"
    bars_dir.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["600519.SH"],
            "trade_date": [date(2024, 6, 28)],
            "open": [1.0],
            "high": [1.0],
            "low": [1.0],
            "close": [1.0],
            "volume": [100],
            "amount": [100.0],
        }
    ).write_parquet(bars_dir / "part-0.parquet")

    def fake_fetch(symbol, adjust_type, client=None):
        return pl.DataFrame({"trade_date": [date(2024, 6, 28)], "factor": [0.5]})

    monkeypatch.setattr(
        "stock_data_engine.derive.adj_factors.fetch_adj_factor_series",
        fake_fetch,
    )
    return cfg


def test_compute_adj_factors_writes_derived(adj_config):
    rows = compute_adj_factors(adj_config)
    assert rows == 1
    out = adj_config.derived_root / "adj_factors" / "trade_date=2024-06-28" / "part-0.parquet"
    assert out.exists()
    df = pl.read_parquet(out)
    assert df["factor"][0] == 0.5
    assert df["source"][0] == "sina"
