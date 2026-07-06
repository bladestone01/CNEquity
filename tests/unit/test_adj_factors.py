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
from stock_data_engine.derive.adj_factors import (
    _align_factors_to_bars,
    _cache_path,
    compute_adj_factors,
)


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
    aligned = _align_factors_to_bars(
        bars.filter(pl.col("symbol") == "600519.SH").select("trade_date"),
        "600519.SH",
        factors,
        "qfq",
    )
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
adjust_types = ["hfq"]

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
    result = compute_adj_factors(adj_config)
    assert result.rows == 1
    assert result.failed == []
    out = adj_config.derived_root / "adj_factors" / "trade_date=2024-06-28" / "part-0.parquet"
    assert out.exists()
    df = pl.read_parquet(out)
    assert df["factor"][0] == 0.5
    assert df["adjust_type"][0] == "hfq"
    assert df["source"][0] == "sina"


def _write_bar(cfg, symbol: str, trade_date: date) -> None:
    bars_dir = cfg.curated_root / "daily_bars" / f"trade_date={trade_date.isoformat()}"
    bars_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "symbol": [symbol],
            "trade_date": [trade_date],
            "open": [1.0],
            "high": [1.0],
            "low": [1.0],
            "close": [1.0],
            "volume": [100],
            "amount": [100.0],
        }
    ).write_parquet(bars_dir / f"{symbol.replace('.', '_')}.parquet")


def _write_factor_cache(cfg, symbol: str, trade_date: date, factor: float = 0.5) -> None:
    path = _cache_path(cfg, symbol, "hfq")
    pl.DataFrame({"trade_date": [trade_date], "factor": [factor]}).write_parquet(path)


def test_compute_adj_factors_reuses_cache_on_non_event_day(adj_config, monkeypatch):
    _write_factor_cache(adj_config, "600519.SH", date(2024, 6, 28))
    _write_bar(adj_config, "600519.SH", date(2024, 6, 29))
    calls: list[str] = []

    def fake_fetch(symbol, adjust_type, client=None):
        calls.append(symbol)
        return pl.DataFrame({"trade_date": [date(2024, 6, 29)], "factor": [0.8]})

    monkeypatch.setattr(
        "stock_data_engine.derive.adj_factors.fetch_adj_factor_series",
        fake_fetch,
    )

    result = compute_adj_factors(adj_config)
    assert calls == []
    assert result.rows == 2
    out = adj_config.derived_root / "adj_factors" / "trade_date=2024-06-29" / "part-0.parquet"
    df = pl.read_parquet(out)
    assert df["factor"][0] == 0.5


def test_compute_adj_factors_refreshes_corporate_action_symbol(adj_config, monkeypatch):
    _write_factor_cache(adj_config, "600519.SH", date(2024, 6, 28))
    _write_bar(adj_config, "600519.SH", date(2024, 6, 29))
    ca_dir = adj_config.curated_root / "corporate_actions" / "ex_date=2024-06-29"
    ca_dir.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["600519.SH"],
            "ex_date": [date(2024, 6, 29)],
            "action_type": ["dividend"],
        }
    ).write_parquet(ca_dir / "part-0.parquet")
    calls: list[str] = []

    def fake_fetch(symbol, adjust_type, client=None):
        calls.append(symbol)
        return pl.DataFrame({"trade_date": [date(2024, 6, 29)], "factor": [0.8]})

    monkeypatch.setattr(
        "stock_data_engine.derive.adj_factors.fetch_adj_factor_series",
        fake_fetch,
    )

    compute_adj_factors(adj_config)
    assert calls == ["600519.SH"]


def test_compute_adj_factors_refreshes_new_listing(adj_config, monkeypatch):
    _write_factor_cache(adj_config, "600519.SH", date(2024, 6, 28))
    _write_factor_cache(adj_config, "000001.SZ", date(2024, 6, 28))
    _write_bar(adj_config, "000001.SZ", date(2024, 6, 29))
    inst_dir = adj_config.curated_root / "instruments"
    inst_dir.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["000001.SZ"],
            "list_date": [date(2024, 6, 29)],
        }
    ).write_parquet(inst_dir / "part-merged.parquet")
    calls: list[str] = []

    def fake_fetch(symbol, adjust_type, client=None):
        calls.append(symbol)
        return pl.DataFrame({"trade_date": [date(2024, 6, 29)], "factor": [1.0]})

    monkeypatch.setattr(
        "stock_data_engine.derive.adj_factors.fetch_adj_factor_series",
        fake_fetch,
    )

    compute_adj_factors(adj_config)
    assert calls == ["000001.SZ"]


def test_resolve_factors_raises_without_cache(adj_config, monkeypatch):
    from stock_data_engine.derive.adj_factors import AdjFactorsFetchError, _resolve_factors

    def boom(*_a, **_kw):
        raise RuntimeError("sina down")

    monkeypatch.setattr(
        "stock_data_engine.derive.adj_factors.fetch_adj_factor_series",
        boom,
    )
    sym_bars = pl.DataFrame({"trade_date": [date(2024, 6, 28)]})
    with pytest.raises(AdjFactorsFetchError, match="No cached adj factors"):
        _resolve_factors(
            adj_config,
            "600519.SH",
            "hfq",
            sym_bars,
            force=True,
            client=object(),
        )


def test_compute_adj_factors_fails_over_threshold(adj_config, monkeypatch):
    from stock_data_engine.derive.adj_factors import AdjFactorsDeriveError, FAIL_RATIO_THRESHOLD
    from stock_data_engine.steps.finalize import step_derive_adj_factors

    def boom(*_a, **_kw):
        raise RuntimeError("sina down")

    monkeypatch.setattr(
        "stock_data_engine.derive.adj_factors.fetch_adj_factor_series",
        boom,
    )
    result = compute_adj_factors(adj_config)
    assert len(result.failed) == 1
    assert result.fail_ratio > FAIL_RATIO_THRESHOLD
    assert result.findings[0]["check"] == "adj_factor_fetch_failed"

    with pytest.raises(AdjFactorsDeriveError, match="adj_factors"):
        step_derive_adj_factors(adj_config, date(2024, 6, 28), "run-adj", {})
