"""push2his CDN sticky / failover helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import ashare_lake.adapters.eastmoney.em_auth as em


def _reset_state() -> None:
    em._STICKY_IP = None
    em._FAILED_UNTIL.clear()
    em._DISCOVER_CACHE.clear()
    em.reset_egress_breaker()


def _fake_curl(monkeypatch, get_fn):
    """Swap curl_cffi for a stub whose .get is ``get_fn``."""

    class CurlOpt:
        RESOLVE = "RESOLVE"

    fake_curl_cffi = MagicMock()
    fake_curl_cffi.CurlOpt = CurlOpt
    fake_requests = MagicMock()
    fake_requests.get = get_fn
    fake_curl_cffi.requests = fake_requests

    import sys

    monkeypatch.setitem(sys.modules, "curl_cffi", fake_curl_cffi)
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", fake_requests)


def test_breaker_stops_walking_the_ladder_once_egress_is_blocked(monkeypatch, tmp_path: Path):
    """A blocked egress must cost one ladder walk, not one per request."""
    _reset_state()
    ladder_walks = {"n": 0}

    def counting_candidates(host, path, force_discover=False):
        ladder_walks["n"] += 1
        return ["1.1.1.1", "2.2.2.2"]

    monkeypatch.setattr(em, "_candidate_ips", counting_candidates)
    _fake_curl(monkeypatch, lambda url, **kw: (_ for _ in ()).throw(ConnectionError("closed")))

    def call():
        return em._chrome_get(
            "https://push2his.eastmoney.com/api/qt/stock/kline/get",
            headers={},
            params={"secid": "90.BK1152"},
            sticky_path=tmp_path / "sticky.json",
        )

    for _ in range(em._BREAKER_TRIP_AFTER):
        with pytest.raises(Exception) as exc_info:
            call()
        assert not isinstance(exc_info.value, em.EgressUnavailable)
    walks_before = ladder_walks["n"]
    assert walks_before > 0

    # Breaker is now open: the next call must not touch DNS or the edge list.
    with pytest.raises(em.EgressUnavailable):
        call()
    assert ladder_walks["n"] == walks_before
    # And callers must read it as a dead egress, not a retryable blip.
    with pytest.raises(em.EgressUnavailable) as exc_info:
        call()
    assert em.is_transport_fail_fast(exc_info.value)


def test_breaker_reopens_after_cooldown_and_clears_on_success(monkeypatch, tmp_path: Path):
    _reset_state()
    monkeypatch.setattr(em, "_candidate_ips", lambda host, path, force_discover=False: ["1.1.1.1"])
    outcome = {"ok": False}

    def fake_get(url, **kwargs):
        if not outcome["ok"]:
            raise ConnectionError("closed")
        resp = MagicMock()
        resp.status_code = 200
        return resp

    _fake_curl(monkeypatch, fake_get)

    def call():
        return em._chrome_get(
            "https://push2his.eastmoney.com/api/qt/stock/kline/get",
            headers={},
            params={"secid": "90.BK1152"},
            sticky_path=tmp_path / "sticky.json",
        )

    for _ in range(em._BREAKER_TRIP_AFTER):
        with pytest.raises(ConnectionError):
            call()
    with pytest.raises(em.EgressUnavailable):
        call()

    # Cool-down elapsed: one probe is allowed through, and it succeeds.
    monkeypatch.setattr(em, "_BREAKER_COOLDOWN_SEC", 0.0)
    em._BREAKER["push2his.eastmoney.com"] = (em._BREAKER_TRIP_AFTER, 0.0)
    outcome["ok"] = True
    assert call().status_code == 200
    assert "push2his.eastmoney.com" not in em._BREAKER


def test_proxy_skips_the_pinning_ladder(monkeypatch, tmp_path: Path):
    """CURLOPT_RESOLVE never reaches a CONNECT tunnel — don't replay the ladder."""
    _reset_state()

    def boom(host, path, force_discover=False):
        raise AssertionError("candidate ladder must not run behind a proxy")

    monkeypatch.setattr(em, "_candidate_ips", boom)
    seen: dict = {}

    def fake_get(url, **kwargs):
        seen.update(kwargs)
        resp = MagicMock()
        resp.status_code = 200
        return resp

    _fake_curl(monkeypatch, fake_get)

    resp = em._chrome_get(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        headers={},
        params={"secid": "90.BK1152"},
        proxy="http://127.0.0.1:7890",
        sticky_path=tmp_path / "sticky.json",
    )
    assert resp.status_code == 200
    assert seen["proxy"] == "http://127.0.0.1:7890"
    assert "curl_options" not in seen


def test_candidate_ips_prefer_sticky_then_discovered(tmp_path: Path, monkeypatch):
    _reset_state()
    sticky = tmp_path / "push2his_endpoint.json"
    sticky.write_text('{"ip": "61.129.129.199"}\n')
    monkeypatch.setattr(
        em, "_discover_cdn_ips", lambda host, force=False: ["103.220.167.80", "140.207.67.156"]
    )
    ips = em._candidate_ips("push2his.eastmoney.com", sticky)
    assert ips[0] == "61.129.129.199"
    assert "103.220.167.80" in ips
    assert "140.207.67.156" in ips
    assert ips.count("61.129.129.199") == 1


def test_candidate_ips_demote_dead_sticky(tmp_path: Path, monkeypatch):
    _reset_state()
    sticky = tmp_path / "push2his_endpoint.json"
    sticky.write_text('{"ip": "61.129.129.199"}\n')
    monkeypatch.setattr(em, "_discover_cdn_ips", lambda host, force=False: ["103.220.167.80"])
    em._mark_edge_failed("61.129.129.199", sticky)
    ips = em._candidate_ips("push2his.eastmoney.com", sticky)
    assert ips[0] == "103.220.167.80"
    assert ips[-1] == "61.129.129.199" or "61.129.129.199" in ips
    assert not sticky.exists()


def test_chrome_get_fails_over_to_next_ip(monkeypatch, tmp_path: Path):
    _reset_state()
    sticky = tmp_path / "push2his_endpoint.json"
    monkeypatch.setattr(
        em, "_candidate_ips", lambda host, path, force_discover=False: ["1.1.1.1", "61.129.129.199"]
    )

    calls: list[str] = []

    class CurlOpt:
        RESOLVE = "RESOLVE"

    def fake_get(url, **kwargs):
        opts = kwargs.get("curl_options") or {}
        resolve_list = opts.get("RESOLVE") or []
        if not resolve_list:
            raise ConnectionError("unpinned fail")
        ip = str(resolve_list[0]).rsplit(":", 1)[-1]
        calls.append(ip)
        if ip == "1.1.1.1":
            raise ConnectionError("closed")
        resp = MagicMock()
        resp.status_code = 200
        return resp

    fake_curl_cffi = MagicMock()
    fake_curl_cffi.CurlOpt = CurlOpt
    fake_requests = MagicMock()
    fake_requests.get = fake_get
    fake_curl_cffi.requests = fake_requests

    import sys

    monkeypatch.setitem(sys.modules, "curl_cffi", fake_curl_cffi)
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", fake_requests)

    resp = em._chrome_get(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        headers={"Referer": "https://quote.eastmoney.com/"},
        params={"secid": "90.BK1152"},
        sticky_path=sticky,
    )
    assert resp.status_code == 200
    assert calls == ["1.1.1.1", "61.129.129.199"]
    assert sticky.exists()
    assert "61.129.129.199" in sticky.read_text()


def test_remember_push2his_endpoint(tmp_path: Path):
    _reset_state()

    class Cfg:
        meta_root = tmp_path

    em.remember_push2his_endpoint("61.129.129.199:443", config=Cfg())  # type: ignore[arg-type]
    path = tmp_path / "state" / "push2his_endpoint.json"
    assert path.exists()
    assert "61.129.129.199" in path.read_text()
