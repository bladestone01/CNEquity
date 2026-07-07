import pytest

from stock_data_engine.adapters.tdx_protocol import client as tdx
from stock_data_engine.config import Config


def test_quotes_client_auto_binds_probed_server(monkeypatch):
    seen: dict[str, object] = {}

    def fake_factory(market, **kwargs):
        seen["market"] = market
        seen.update(kwargs)
        return object()

    # auto mode probes a reachable+functional server instead of the slow,
    # flaky bestip scan; stub the probe so the test stays offline.
    monkeypatch.setattr("mootdx.quotes.Quotes.factory", fake_factory)
    monkeypatch.setattr(tdx, "_pick_reachable_server", lambda timeout=10: ("1.2.3.4", 7709))
    tdx.reset_tdx_server_cache()
    cfg = Config(data_root="/tmp/data")
    cfg.tdx_connect_timeout_sec = 42
    cfg.tdx_servers = "auto"

    tdx._quotes_client(cfg)
    tdx.reset_tdx_server_cache()

    assert seen["market"] == "std"
    assert seen["timeout"] == 42
    assert seen["server"] == ("1.2.3.4", 7709)
    assert "bestip" not in seen


def test_quotes_client_explicit_server(monkeypatch):
    seen: dict[str, object] = {}

    def fake_factory(market, **kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr("mootdx.quotes.Quotes.factory", fake_factory)
    cfg = Config(data_root="/tmp/data")
    cfg.tdx_servers = "119.147.212.81:7709"
    cfg.tdx_connect_timeout_sec = 15

    tdx._quotes_client(cfg)

    assert seen["server"] == ("119.147.212.81", 7709)
    assert seen["timeout"] == 15
    assert "bestip" not in seen


def test_quotes_client_rejects_invalid_servers():
    cfg = Config(data_root="/tmp/data")
    cfg.tdx_servers = "not-a-server"
    with pytest.raises(tdx.TdxSourceError, match="invalid"):
        tdx._quotes_client(cfg)
