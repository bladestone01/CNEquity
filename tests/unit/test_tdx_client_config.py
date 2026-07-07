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
    monkeypatch.setattr(
        tdx, "_pick_reachable_server", lambda config=None, timeout=10: ("1.2.3.4", 7709)
    )
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


def test_candidate_servers_prefers_config_pool(monkeypatch):
    cfg = Config(data_root="/tmp/data")
    cfg.tdx_host_pool = ["1.1.1.1:7709", "2.2.2.2:7709"]
    candidates = tdx._candidate_servers(cfg)
    # configured pool comes first, in order
    assert candidates[0] == ("1.1.1.1", 7709)
    assert candidates[1] == ("2.2.2.2", 7709)
    # bundled hosts appended as fallback
    assert len(candidates) > 2


def test_pick_reachable_server_returns_first_functional(monkeypatch):
    cfg = Config(data_root="/tmp/data")
    cfg.tdx_host_pool = ["9.9.9.9:7709", "8.8.8.8:7709"]

    # only 8.8.8.8 serves data
    def fake_probe(host, port, timeout):
        return host == "8.8.8.8"

    monkeypatch.setattr(tdx, "_probe", fake_probe)
    monkeypatch.setattr(tdx, "_candidate_servers", lambda config: [("9.9.9.9", 7709), ("8.8.8.8", 7709)])
    assert tdx._pick_reachable_server(cfg) == ("8.8.8.8", 7709)


def test_pick_reachable_server_raises_when_none_live(monkeypatch):
    monkeypatch.setattr(tdx, "_probe", lambda h, p, t: False)
    monkeypatch.setattr(tdx, "_candidate_servers", lambda config: [("9.9.9.9", 7709)])
    with pytest.raises(tdx.TdxSourceError, match="no TDX server responded"):
        tdx._pick_reachable_server(None)
