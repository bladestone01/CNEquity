"""push2his CDN sticky / failover helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import ashare_lake.adapters.eastmoney.em_auth as em


def _reset_state() -> None:
    em._STICKY_IP = None
    em._FAILED_UNTIL.clear()
    em._DISCOVER_CACHE.clear()


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
