"""Offline coverage for OnDemandService cache + remote stubs."""

from __future__ import annotations

import pytest

from ashare_lake.config import Config
from ashare_lake.query.on_demand import OnDemandService


def test_fetch_cache_roundtrip_and_placeholders(tmp_path, monkeypatch):
    cfg = Config(
        data_root=tmp_path / "data", on_demand_datasets=["stock_news", "financial_reports"]
    )
    svc = OnDemandService(cfg)

    monkeypatch.setattr(
        "ashare_lake.query.on_demand.fetch_stock_news",
        lambda symbol, **k: {"symbol": symbol, "items": [{"title": "t"}]},
    )
    monkeypatch.setattr(Config, "rate_limit", lambda self, name: None)

    first = svc.fetch("stock_news", "600519.SH", limit=5)
    assert first["items"][0]["title"] == "t"
    assert first["data_version"] == "v1"

    # Second hit uses cache (remote would raise if called).
    monkeypatch.setattr(
        "ashare_lake.query.on_demand.fetch_stock_news",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("cache miss")),
    )
    second = svc.fetch("stock_news", "600519.SH")
    assert second["items"][0]["title"] == "t"

    placeholder = svc.fetch("financial_reports", "600519.SH")
    assert placeholder["statements"] == []

    body = svc._fetch_announcement_body("600519.SH")
    assert body["source"] == "cninfo"

    with pytest.raises(ValueError, match="not enabled"):
        svc.fetch("unknown_ds", "600519.SH")


def test_research_reports_error_path(tmp_path, monkeypatch):
    cfg = Config(data_root=tmp_path / "data", on_demand_datasets=[])
    svc = OnDemandService(cfg)

    class BoomClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def get(self, url):
            raise RuntimeError("offline")

    monkeypatch.setattr("ashare_lake.query.on_demand.EastMoneyClient", BoomClient)
    out = svc._fetch_research_reports("600519.SH")
    assert out["items"] == []
    assert "error" in out

    unknown = svc._fetch_remote("mystery", "600519.SH")
    assert unknown["status"] == "not_implemented"
