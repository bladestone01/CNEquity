"""EastMoney auth helpers not covered by the push2his ladder tests: NID cookie
fetch/cache, header building, sticky-path plumbing, CDN discovery probes, and
the plain (non-Chrome-TLS) EastMoneyClient request path.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

import ashare_lake.adapters.eastmoney.em_auth as em
from ashare_lake.adapters.eastmoney.em_auth import EastMoneyClient


def _reset_state() -> None:
    em._STICKY_IP = None
    em._FAILED_UNTIL.clear()
    em._DISCOVER_CACHE.clear()
    em.reset_egress_breaker()
    em._NID_CACHE["nid"] = None
    em._NID_CACHE["expires"] = 0.0


def test_reset_egress_breaker_specific_host():
    em._BREAKER["a.example.com"] = (3, 999999999.0)
    em._BREAKER["b.example.com"] = (3, 999999999.0)
    em.reset_egress_breaker("a.example.com")
    assert "a.example.com" not in em._BREAKER
    assert "b.example.com" in em._BREAKER
    em.reset_egress_breaker()
    assert em._BREAKER == {}


def test_fetch_nid_returns_cookie_value(monkeypatch):
    class _Cookie:
        def __init__(self, name, value):
            self.name = name
            self.value = value

    class _Resp:
        class cookies:
            jar = [_Cookie("nid", "abc123"), _Cookie("other", "x")]

    class _Client:
        def post(self, url, json=None):
            return _Resp()

    assert em.fetch_nid(client=_Client()) == "abc123"


def test_fetch_nid_returns_empty_when_cookie_missing():
    class _Resp:
        class cookies:
            jar = []

    class _Client:
        def post(self, url, json=None):
            return _Resp()

    assert em.fetch_nid(client=_Client()) == ""


def test_fetch_nid_returns_empty_on_exception_and_closes_owned_client(monkeypatch):
    closed = {"v": False}

    class _OwnClient:
        def post(self, url, json=None):
            raise RuntimeError("network down")

        def close(self):
            closed["v"] = True

    monkeypatch.setattr(em.httpx, "Client", lambda timeout=10.0: _OwnClient())
    assert em.fetch_nid() == ""
    assert closed["v"] is True


def test_fetch_nid_does_not_close_caller_provided_client():
    closed = {"v": False}

    class _Client:
        def post(self, url, json=None):
            raise RuntimeError("boom")

        def close(self):
            closed["v"] = True

    assert em.fetch_nid(client=_Client()) == ""
    assert closed["v"] is False


def test_get_nid_caches_until_expiry(monkeypatch):
    _reset_state()
    calls = {"n": 0}

    def _fake_fetch(client=None):
        calls["n"] += 1
        return "nid-1"

    monkeypatch.setattr(em, "fetch_nid", _fake_fetch)
    assert em.get_nid() == "nid-1"
    assert em.get_nid() == "nid-1"
    assert calls["n"] == 1  # second call served from cache


def test_get_nid_refetches_after_expiry(monkeypatch):
    _reset_state()
    monkeypatch.setattr(em, "fetch_nid", lambda client=None: "")
    assert em.get_nid() == ""  # empty nid never populates the cache


def test_build_eastmoney_headers_push2_domain_no_nid_lookup(monkeypatch):
    monkeypatch.setattr(em, "get_nid", lambda: pytest.fail("must not be called for push2"))
    headers = em.build_eastmoney_headers("https://push2.eastmoney.com/api/qt/clist/get")
    assert headers["Referer"] == em._QUOTE_REFERER
    assert "Cookie" not in headers


def test_build_eastmoney_headers_eastmoney_domain_sets_cookie(monkeypatch):
    monkeypatch.setattr(em, "get_nid", lambda: "nid-xyz")
    headers = em.build_eastmoney_headers("https://datacenter-web.eastmoney.com/api/data/v1/get")
    assert headers["Cookie"] == "nid=nid-xyz"


def test_build_eastmoney_headers_eastmoney_domain_no_nid(monkeypatch):
    monkeypatch.setattr(em, "get_nid", lambda: "")
    headers = em.build_eastmoney_headers("https://datacenter-web.eastmoney.com/api/data/v1/get")
    assert "Cookie" not in headers


def test_build_eastmoney_headers_unknown_domain_is_plain():
    headers = em.build_eastmoney_headers("https://example.com/other")
    assert "Cookie" not in headers
    assert "Referer" not in headers


def test_sticky_path_none_config_returns_none():
    assert em._sticky_path(None) is None


def test_sticky_path_with_config(tmp_path: Path):
    class _Cfg:
        meta_root = tmp_path

    path = em._sticky_path(_Cfg())
    assert path == tmp_path / "state" / em._PUSH2HIS_STICKY_FILE


def test_load_sticky_missing_path_returns_none(tmp_path: Path):
    _reset_state()
    assert em._load_sticky(tmp_path / "nope.json") is None


def test_load_sticky_none_path_returns_none():
    _reset_state()
    assert em._load_sticky(None) is None


def test_load_sticky_bad_json_returns_none(tmp_path: Path):
    _reset_state()
    path = tmp_path / "sticky.json"
    path.write_text("not json")
    assert em._load_sticky(path) is None


def test_load_sticky_uses_process_cache_before_disk(tmp_path: Path):
    _reset_state()
    em._STICKY_IP = "9.9.9.9"
    assert em._load_sticky(tmp_path / "unrelated.json") == "9.9.9.9"


def test_save_sticky_none_path_still_sets_process_cache():
    _reset_state()
    em._save_sticky(None, "1.2.3.4")
    assert em._STICKY_IP == "1.2.3.4"


def test_save_sticky_handles_write_failure(tmp_path: Path, monkeypatch):
    _reset_state()
    bad_path = tmp_path / "no_such_dir" / "sticky.json"

    def _boom_mkdir(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "mkdir", _boom_mkdir)
    em._save_sticky(bad_path, "1.2.3.4")
    assert em._STICKY_IP == "1.2.3.4"  # process cache still updated


def test_remember_push2his_endpoint_ignores_blank_ip(tmp_path: Path):
    _reset_state()

    class _Cfg:
        meta_root = tmp_path

    em.remember_push2his_endpoint("   ", config=_Cfg())
    assert em._STICKY_IP is None
    assert not (tmp_path / "state").exists()


def test_mark_edge_failed_clears_matching_sticky_and_file(tmp_path: Path):
    _reset_state()
    sticky = tmp_path / "sticky.json"
    sticky.write_text('{"ip": "1.1.1.1"}\n')
    em._STICKY_IP = "1.1.1.1"
    em._mark_edge_failed("1.1.1.1", sticky)
    assert em._STICKY_IP is None
    assert not sticky.exists()


def test_mark_edge_failed_leaves_other_ips_sticky_file_alone(tmp_path: Path):
    _reset_state()
    sticky = tmp_path / "sticky.json"
    sticky.write_text('{"ip": "2.2.2.2"}\n')
    em._mark_edge_failed("1.1.1.1", sticky)
    assert sticky.exists()


def test_mark_edge_failed_missing_sticky_path_is_noop():
    _reset_state()
    em._mark_edge_failed("1.1.1.1", None)
    assert em._is_demoted("1.1.1.1")


def test_mark_edge_failed_tolerates_corrupt_sticky_file(tmp_path: Path):
    _reset_state()
    sticky = tmp_path / "sticky.json"
    sticky.write_text("not json")
    em._mark_edge_failed("1.1.1.1", sticky)  # must not raise
    assert em._is_demoted("1.1.1.1")


def test_is_demoted_expires_after_window(monkeypatch):
    _reset_state()
    em._FAILED_UNTIL["1.1.1.1"] = 0.0  # already expired
    assert em._is_demoted("1.1.1.1") is False
    assert "1.1.1.1" not in em._FAILED_UNTIL


def test_is_demoted_false_when_never_marked():
    _reset_state()
    assert em._is_demoted("9.9.9.9") is False


def test_doh_a_records_parses_answers_and_skips_bad_endpoints(monkeypatch):
    def _fake_get(url, headers=None, timeout=None):
        resp = MagicMock()
        if "dns.google" in url:
            resp.raise_for_status.side_effect = RuntimeError("dns.google down")
            return resp
        resp.raise_for_status.return_value = None
        resp.json.return_value = {
            "Answer": [
                {"type": 1, "data": "1.1.1.1"},
                {"type": 1, "data": "1.1.1.1"},  # dup, deduped
                {"type": 5, "data": "cname.example.com"},  # CNAME, skipped
                {"type": 1, "data": "2.2.2.2"},
            ]
        }
        return resp

    monkeypatch.setattr(em.httpx, "get", _fake_get)
    ips = em._doh_a_records("push2his.eastmoney.com")
    assert ips == ["1.1.1.1", "2.2.2.2"]


def test_doh_a_records_all_endpoints_fail(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("no network")

    monkeypatch.setattr(em.httpx, "get", _boom)
    assert em._doh_a_records("push2his.eastmoney.com") == []


def test_system_a_records_dedupes_and_skips_ipv6(monkeypatch):
    def _fake_getaddrinfo(host, port, type=None):
        return [
            (2, 1, 6, "", ("3.3.3.3", 443)),
            (2, 1, 6, "", ("3.3.3.3", 443)),
            (30, 1, 6, "", ("::1", 443)),
            (2, 1, 6, "", ("4.4.4.4", 443)),
        ]

    monkeypatch.setattr(em.socket, "getaddrinfo", _fake_getaddrinfo)
    ips = em._system_a_records("push2his.eastmoney.com")
    assert ips == ["3.3.3.3", "4.4.4.4"]


def test_system_a_records_returns_empty_on_dns_failure(monkeypatch):
    def _boom(host, port, type=None):
        raise OSError("dns failure")

    monkeypatch.setattr(em.socket, "getaddrinfo", _boom)
    assert em._system_a_records("push2his.eastmoney.com") == []


def test_udp_dig_a_records_returns_empty_when_dig_missing(monkeypatch):
    import shutil as real_shutil

    monkeypatch.setattr(real_shutil, "which", lambda name: None)
    assert em._udp_dig_a_records("push2his.eastmoney.com") == []


def test_udp_dig_a_records_parses_dig_output(monkeypatch):
    import shutil as real_shutil
    import subprocess as real_subprocess

    monkeypatch.setattr(real_shutil, "which", lambda name: "/usr/bin/dig")

    def _fake_check_output(cmd, text=True, timeout=4, stderr=None):
        ns = cmd[-1]
        if ns == "@223.5.5.5":
            return "5.5.5.5.\n\n"
        if ns == "@119.29.29.29":
            raise real_subprocess.SubprocessError("timeout")
        return ""

    monkeypatch.setattr(real_subprocess, "check_output", _fake_check_output)
    ips = em._udp_dig_a_records("push2his.eastmoney.com")
    assert ips == ["5.5.5.5"]


def test_discover_cdn_ips_combines_sources_and_caches(monkeypatch):
    _reset_state()
    calls = {"n": 0}

    def _doh(host):
        calls["n"] += 1
        return ["1.1.1.1"]

    monkeypatch.setattr(em, "_doh_a_records", _doh)
    monkeypatch.setattr(em, "_udp_dig_a_records", lambda host: ["2.2.2.2"])
    monkeypatch.setattr(em, "_system_a_records", lambda host: ["1.1.1.1", "3.3.3.3"])

    ips = em._discover_cdn_ips("push2his.eastmoney.com")
    assert ips == ["1.1.1.1", "2.2.2.2", "3.3.3.3"]
    assert calls["n"] == 2  # host + TM host

    # Cached result is served without calling the probes again.
    calls_before = calls["n"]
    em._discover_cdn_ips("push2his.eastmoney.com")
    assert calls["n"] == calls_before


def test_discover_cdn_ips_force_bypasses_cache(monkeypatch):
    _reset_state()
    calls = {"n": 0}

    def _doh(host):
        calls["n"] += 1
        return []

    monkeypatch.setattr(em, "_doh_a_records", _doh)
    monkeypatch.setattr(em, "_udp_dig_a_records", lambda host: [])
    monkeypatch.setattr(em, "_system_a_records", lambda host: [])

    em._discover_cdn_ips("push2his.eastmoney.com")
    before = calls["n"]
    em._discover_cdn_ips("push2his.eastmoney.com", force=True)
    assert calls["n"] > before


def test_eastmoney_client_default_min_interval():
    client = EastMoneyClient()
    assert client.min_interval == 1.0
    client.close()


def test_eastmoney_client_httpx_client_fallback_to_proxies_kwarg(monkeypatch):
    calls: list[dict] = []
    real_client = httpx.Client

    def _flaky_client(**kwargs):
        calls.append(kwargs)
        if "proxy" in kwargs:
            raise TypeError("proxy kwarg not supported by this httpx version")
        return real_client(**{k: v for k, v in kwargs.items() if k != "proxies"})

    monkeypatch.setattr(em.httpx, "Client", _flaky_client)

    class _Cfg:
        eastmoney_proxy = "http://127.0.0.1:7890"
        eastmoney_timeout_sec = 5.0

    client = EastMoneyClient(config=_Cfg())
    assert len(calls) == 2
    assert "proxy" in calls[0]
    assert "proxies" in calls[1]
    client.close()


def test_eastmoney_client_throttle_sleeps_when_below_min_interval(monkeypatch):
    client = EastMoneyClient(min_interval=10.0)
    sleeps: list[float] = []
    monkeypatch.setattr(em.time, "sleep", lambda s: sleeps.append(s))
    client._last_request = em.time.time()
    client._throttle()
    assert sleeps and sleeps[0] > 0
    client.close()


def test_eastmoney_client_throttle_zero_interval_is_noop(monkeypatch):
    client = EastMoneyClient(min_interval=0.0)
    monkeypatch.setattr(
        em.time, "sleep", lambda s: pytest.fail("must not sleep with min_interval<=0")
    )
    client._throttle()
    client.close()


def test_eastmoney_client_throttle_delegates_to_config_rate_limiter():
    calls: list[str] = []

    class _Cfg:
        eastmoney_proxy = None
        eastmoney_timeout_sec = 15.0

        def rate_limit(self, source):
            calls.append(source)

    client = EastMoneyClient(config=_Cfg())
    client._throttle()
    assert calls == ["eastmoney"]
    client.close()


def test_eastmoney_client_get_uses_plain_httpx_for_non_chrome_hosts(monkeypatch):
    client = EastMoneyClient(min_interval=0.0)
    seen = {}

    def _fake_get(url, headers=None, **kwargs):
        seen["url"] = url
        seen["headers"] = headers
        resp = MagicMock()
        resp.status_code = 200
        return resp

    monkeypatch.setattr(client._client, "get", _fake_get)
    resp = client.get("https://datacenter-web.eastmoney.com/api/data/v1/get")
    assert resp.status_code == 200
    assert "User-Agent" in seen["headers"]
    client.close()


def test_eastmoney_client_get_routes_chrome_hosts_through_chrome_get(monkeypatch):
    client = EastMoneyClient(min_interval=0.0)
    seen = {}

    def _fake_chrome_get(url, *, headers, params, timeout, proxy, sticky_path):
        seen.update(url=url, params=params, timeout=timeout)
        resp = MagicMock()
        resp.status_code = 200
        return resp

    monkeypatch.setattr(em, "_chrome_get", _fake_chrome_get)
    monkeypatch.setattr(em, "_breaker_guard", lambda host: None)
    resp = client.get(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        params={"secid": "1.600519"},
    )
    assert resp.status_code == 200
    assert seen["params"] == {"secid": "1.600519"}
    client.close()


def test_eastmoney_client_post_builds_headers_and_calls_underlying_client(monkeypatch):
    client = EastMoneyClient(min_interval=0.0)
    seen = {}

    def _fake_post(url, headers=None, **kwargs):
        seen["headers"] = headers
        resp = MagicMock()
        resp.status_code = 200
        return resp

    monkeypatch.setattr(client._client, "post", _fake_post)
    resp = client.post("https://www.cninfo.com.cn/new/hisAnnouncement/query", data={"a": 1})
    assert resp.status_code == 200
    assert "User-Agent" in seen["headers"]
    client.close()


def test_eastmoney_client_context_manager_closes_on_exit(monkeypatch):
    closed = {"v": False}
    client = EastMoneyClient(min_interval=0.0)
    monkeypatch.setattr(client, "close", lambda: closed.__setitem__("v", True))
    with client as ctx:
        assert ctx is client
    assert closed["v"] is True
