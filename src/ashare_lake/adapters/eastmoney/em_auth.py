"""EastMoney NID auth — inject nid cookie for datacenter/reportapi domains.

``push2his`` (and peers) often drop plain ``httpx``/``curl`` TLS from overseas
IPs (Empty reply) while a real Chrome session succeeds. Those hosts therefore
go through ``curl_cffi`` Chrome impersonation — same URL/params as the quote
page DevTools request, matching browser JA3.

CDN edge IPs also rotate: a node that works in Chrome DevTools (Remote Address)
may differ from what system DNS returns. ``_chrome_get`` therefore pins via
``CURLOPT_RESOLVE``, preferring a sticky last-good IP, then DoH / DNS / seeds.
"""

from __future__ import annotations

import json
import logging
import random
import socket
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

if TYPE_CHECKING:
    from ashare_lake.config import Config

logger = logging.getLogger(__name__)

_NID_CACHE: dict = {"nid": None, "expires": 0.0}

_EASTMONEY_DOMAINS = (
    "eastmoney.com",
    "datacenter-web.eastmoney.com",
    "reportapi.eastmoney.com",
    "search-api-web.eastmoney.com",
    "np-weblist.eastmoney.com",
    "anonflow2.eastmoney.com",
)
_PUSH2_DOMAINS = (
    "push2.eastmoney.com",
    "push2delay.eastmoney.com",
    "push2his.eastmoney.com",
    "91.push2his.eastmoney.com",
    "40.push2.eastmoney.com",
)
# Only historical kline hosts need Chrome JA3; live clist (push2/push2delay) is fine on httpx.
_PUSH2HIS_CHROME_DOMAINS = (
    "push2his.eastmoney.com",
    "91.push2his.eastmoney.com",
)
_QUOTE_REFERER = "https://quote.eastmoney.com/"
# Front-end token from quote.eastmoney.com kline calls (stable across sessions).
_PUSH2_UT = "fa5fd1943c7b386f172d6893dbfba10b"
_CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/150.0.0.0 Safari/537.36"
)
_CHROME_IMPERSONATE = "chrome131"
# Recently observed working edges (Chrome Remote Address); rotated by CDN.
_PUSH2HIS_SEED_IPS = (
    "61.129.129.199",
    "103.220.167.80",
    "117.184.40.129",
    "117.184.38.143",
    "140.207.67.156",
    "101.42.128.173",
    "101.226.30.221",
    "14.103.188.89",
)
# Azure Traffic Manager behind push2his — different resolvers return different edges.
_PUSH2HIS_TM_HOST = "push2hisipv6.trafficmanager.cn"
_PUSH2HIS_STICKY_FILE = "push2his_endpoint.json"
_DOH_ENDPOINTS = (
    "https://dns.google/resolve?name={host}&type=A",
    "https://cloudflare-dns.com/dns-query?name={host}&type=A",
    "https://dns.alidns.com/resolve?name={host}&type=A",
    "https://doh.pub/dns-query?name={host}&type=A",
)
# UDP resolvers (via dig when available) — TM answers vary heavily by vantage.
_UDP_DNS_RESOLVERS = (
    "223.5.5.5",
    "119.29.29.29",
    "114.114.114.114",
    "180.76.76.76",
    "8.8.8.8",
    "1.1.1.1",
)
_FAIL_DEMOTE_SEC = 300.0
_DISCOVER_CACHE_SEC = 60.0
# Consecutive end-to-end ladder failures before we accept the egress is blocked,
# and how long that verdict stands before one probe is allowed through.
_BREAKER_TRIP_AFTER = 3
_BREAKER_COOLDOWN_SEC = 300.0

# Process-local sticky (survives within one backfill); file sticky spans runs.
_STICKY_IP: str | None = None
# ip -> unix time until which we demote this edge (failed sticky / pin).
_FAILED_UNTIL: dict[str, float] = {}
# Cached discovery: (expires_at, ips).
_DISCOVER_CACHE: dict[str, tuple[float, list[str]]] = {}
# host -> (consecutive whole-ladder failures, unix time the breaker reopens).
_BREAKER: dict[str, tuple[int, float]] = {}


def reset_egress_breaker(host: str | None = None) -> None:
    """Forget the blocked-egress verdict (host, or all hosts when None)."""
    if host is None:
        _BREAKER.clear()
    else:
        _BREAKER.pop(host, None)


def _breaker_guard(host: str) -> None:
    fails, open_until = _BREAKER.get(host, (0, 0.0))
    remaining = open_until - time.time()
    if remaining > 0:
        raise EgressUnavailable(
            f"{host}: {fails} consecutive CDN-ladder failures — skipping the "
            f"ladder for another {remaining:.0f}s"
        )


def _breaker_record(host: str, *, ok: bool) -> None:
    if ok:
        # Half-open probe succeeded (or the egress was fine all along).
        _BREAKER.pop(host, None)
        return
    fails = _BREAKER.get(host, (0, 0.0))[0] + 1
    tripped = fails >= _BREAKER_TRIP_AFTER
    _BREAKER[host] = (fails, time.time() + _BREAKER_COOLDOWN_SEC if tripped else 0.0)
    if tripped:
        logger.warning(
            "%s unreachable after %d full CDN-ladder attempts — failing fast for %.0fs",
            host,
            fails,
            _BREAKER_COOLDOWN_SEC,
        )


def fetch_nid(client: httpx.Client | None = None) -> str:
    url = "https://anonflow2.eastmoney.com/backend/api/webreport"
    payload = {
        "deviceType": "web",
        "browser": "Chrome",
        "os": "Windows",
        "screen": "1920x1080",
        "canvasKey": hex(random.getrandbits(64)),
        "webglKey": hex(random.getrandbits(64)),
        "fontKey": hex(random.getrandbits(64)),
        "audioKey": hex(random.getrandbits(64)),
    }
    own = client is None
    if own:
        client = httpx.Client(timeout=10.0)
    try:
        resp = client.post(url, json=payload)
        for cookie in resp.cookies.jar:
            if cookie.name == "nid":
                return cookie.value or ""
    except Exception as exc:
        logger.debug("NID fetch failed: %s", exc)
    finally:
        if own:
            client.close()
    return ""


def get_nid() -> str:
    now = time.time()
    if now > _NID_CACHE["expires"]:
        nid = fetch_nid()
        if nid:
            _NID_CACHE["nid"] = nid
            _NID_CACHE["expires"] = now + 20
    return _NID_CACHE["nid"] or ""


def build_eastmoney_headers(url: str) -> dict[str, str]:
    headers = {
        "User-Agent": _CHROME_UA,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if any(d in url for d in _PUSH2_DOMAINS):
        headers["Referer"] = _QUOTE_REFERER
        return headers
    if any(d in url for d in _EASTMONEY_DOMAINS):
        nid = get_nid()
        if nid:
            headers["Cookie"] = f"nid={nid}"
    return headers


def _needs_chrome_tls(url: str) -> bool:
    return any(d in url for d in _PUSH2HIS_CHROME_DOMAINS)


class EgressUnavailable(ConnectionError):
    """push2his refused this egress repeatedly — the CDN ladder is skipped.

    Walking every candidate edge costs ~23s a call (16 DoH lookups, several
    ``dig`` subprocesses, ~15 TLS attempts) and pays off only when *some* edge
    is reachable. From a blocked egress every edge resets, so a ~991-board sweep
    spends ~13h proving the same thing 2000 times over. Once the ladder has
    failed end-to-end a few times running, the honest conclusion is that this
    network cannot reach push2his at all; the breaker holds that conclusion for
    a cool-down so callers fail in microseconds instead of re-deriving it.
    """


def is_transport_fail_fast(exc: BaseException) -> bool:
    """True for connect/timeout/protocol drops that retries will not fix overseas."""
    if isinstance(exc, EgressUnavailable):
        return True
    if isinstance(
        exc,
        (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError),
    ):
        return True
    # curl_cffi wraps libcurl drops as generic ConnectionError / RequestException.
    name = type(exc).__name__
    return name in {"ConnectError", "ConnectionError", "Timeout", "RemoteProtocolError"}


def _host_from_url(url: str) -> str:
    return urlparse(url).hostname or "push2his.eastmoney.com"


def _sticky_path(config: Config | None) -> Path | None:
    if config is None:
        return None
    return Path(config.meta_root) / "state" / _PUSH2HIS_STICKY_FILE


def _load_sticky(path: Path | None) -> str | None:
    global _STICKY_IP
    if _STICKY_IP:
        return _STICKY_IP
    if path is None or not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        ip = str(data.get("ip") or "").strip()
        if ip:
            _STICKY_IP = ip
            return ip
    except Exception as exc:  # noqa: BLE001 — sticky is best-effort
        logger.debug("push2his sticky load failed: %s", exc)
    return None


def _save_sticky(path: Path | None, ip: str) -> None:
    global _STICKY_IP
    _STICKY_IP = ip
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"ip": ip, "host": "push2his.eastmoney.com", "updated_at": time.time()})
            + "\n"
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("push2his sticky save failed: %s", exc)


def remember_push2his_endpoint(ip: str, *, config: Config | None = None) -> None:
    """Record a working CDN edge (e.g. Chrome DevTools Remote Address)."""
    ip = str(ip).strip().split(":")[0]
    if not ip:
        return
    _FAILED_UNTIL.pop(ip, None)
    _save_sticky(_sticky_path(config), ip)
    logger.info("push2his sticky endpoint set to %s", ip)


def _mark_edge_failed(ip: str, sticky_path: Path | None) -> None:
    """Demote a dead edge so the next request prefers freshly discovered IPs."""
    global _STICKY_IP
    _FAILED_UNTIL[ip] = time.time() + _FAIL_DEMOTE_SEC
    if _STICKY_IP == ip:
        _STICKY_IP = None
    # Drop file sticky if it points at the dead edge — next success rewrites it.
    if sticky_path is not None and sticky_path.exists():
        try:
            data = json.loads(sticky_path.read_text())
            if str(data.get("ip") or "").strip() == ip:
                sticky_path.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.debug("push2his sticky clear failed: %s", exc)


def _is_demoted(ip: str) -> bool:
    until = _FAILED_UNTIL.get(ip)
    if until is None:
        return False
    if time.time() >= until:
        _FAILED_UNTIL.pop(ip, None)
        return False
    return True


def _doh_a_records(host: str) -> list[str]:
    ips: list[str] = []
    seen: set[str] = set()
    for tmpl in _DOH_ENDPOINTS:
        try:
            resp = httpx.get(
                tmpl.format(host=host),
                headers={"accept": "application/dns-json"},
                timeout=5.0,
            )
            resp.raise_for_status()
            for ans in resp.json().get("Answer") or []:
                # type 1 = A; skip CNAME (5) — follow via dedicated TM host query.
                if int(ans.get("type") or 0) != 1:
                    continue
                ip = str(ans.get("data") or "").strip()
                if ip and ip not in seen and ":" not in ip:
                    seen.add(ip)
                    ips.append(ip)
        except Exception as exc:  # noqa: BLE001
            logger.debug("DoH %s failed: %s", tmpl, exc)
    return ips


def _system_a_records(host: str) -> list[str]:
    ips: list[str] = []
    seen: set[str] = set()
    try:
        for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM):
            ip = info[4][0]
            if ":" in ip:  # skip IPv6 for CURLOPT_RESOLVE v4 pins
                continue
            if ip not in seen:
                seen.add(ip)
                ips.append(ip)
    except OSError as exc:
        logger.debug("getaddrinfo %s failed: %s", host, exc)
    return ips


def _udp_dig_a_records(host: str) -> list[str]:
    """Best-effort multi-resolver dig; TM returns different edges per NS."""
    import shutil  # noqa: PLC0415
    import subprocess  # noqa: PLC0415

    if shutil.which("dig") is None:
        return []
    ips: list[str] = []
    seen: set[str] = set()
    for ns in _UDP_DNS_RESOLVERS:
        try:
            out = subprocess.check_output(
                ["dig", "+short", "+time=2", "+tries=1", host, "A", f"@{ns}"],
                text=True,
                timeout=4,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        for line in out.splitlines():
            ip = line.strip().rstrip(".")
            if not ip or not ip[0].isdigit() or ":" in ip or ip in seen:
                continue
            seen.add(ip)
            ips.append(ip)
    return ips


def _discover_cdn_ips(host: str, *, force: bool = False) -> list[str]:
    """Gather current CDN edges from DoH / dig / system DNS (cached briefly)."""
    now = time.time()
    cached = _DISCOVER_CACHE.get(host)
    if not force and cached is not None and cached[0] > now:
        return list(cached[1])

    ordered: list[str] = []
    seen: set[str] = set()

    def add(ip: str | None) -> None:
        if not ip or ip in seen or ":" in ip:
            return
        seen.add(ip)
        ordered.append(ip)

    for name in (host, _PUSH2HIS_TM_HOST):
        for ip in _doh_a_records(name):
            add(ip)
        for ip in _udp_dig_a_records(name):
            add(ip)
        for ip in _system_a_records(name):
            add(ip)

    _DISCOVER_CACHE[host] = (now + _DISCOVER_CACHE_SEC, list(ordered))
    return ordered


def _candidate_ips(
    host: str, sticky_path: Path | None, *, force_discover: bool = False
) -> list[str]:
    """Ordered unique IPv4 candidates for push2his CDN pinning.

    Prefer last-good sticky (if not recently failed), then freshly discovered
    Traffic Manager edges, then seeds. Demoted edges go last so a dead sticky
    does not block failover to the IP Chrome just showed in Remote Address.
    """
    preferred: list[str] = []
    demoted: list[str] = []
    seen: set[str] = set()

    def add(ip: str | None) -> None:
        if not ip or ip in seen or ":" in ip:
            return
        seen.add(ip)
        (demoted if _is_demoted(ip) else preferred).append(ip)

    add(_load_sticky(sticky_path))
    for ip in _discover_cdn_ips(host, force=force_discover):
        add(ip)
    for ip in _PUSH2HIS_SEED_IPS:
        add(ip)
    return preferred + demoted


def _chrome_get(
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, Any] | None = None,
    timeout: float = 30.0,
    proxy: str | None = None,
    sticky_path: Path | None = None,
) -> Any:
    """GET with Chrome TLS fingerprint + CDN IP failover via CURLOPT_RESOLVE.

    Guarded by a per-host breaker so a blocked egress costs one ladder walk,
    not one per request (see :class:`EgressUnavailable`).
    """
    from curl_cffi import requests as creq  # noqa: PLC0415

    q = dict(params or {})
    q.setdefault("ut", _PUSH2_UT)
    host = _host_from_url(url)

    # A proxy resolves the hostname on its own side, so CURLOPT_RESOLVE never
    # applies to the CONNECT tunnel: walking the candidate ladder would replay
    # the *same* request through the *same* egress a dozen times. With a
    # mainland proxy configured the pin is pointless — go straight through.
    if proxy:
        return creq.get(
            url,
            params=q,
            headers=headers,
            timeout=timeout,
            impersonate=_CHROME_IMPERSONATE,
            proxy=proxy,
        )

    _breaker_guard(host)
    try:
        resp = _chrome_get_via_ladder(
            url, headers=headers, params=q, timeout=timeout, host=host, sticky_path=sticky_path
        )
    except Exception:
        _breaker_record(host, ok=False)
        raise
    _breaker_record(host, ok=int(getattr(resp, "status_code", 0) or 0) == 200)
    return resp


def _chrome_get_via_ladder(
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, Any],
    timeout: float,
    host: str,
    sticky_path: Path | None,
) -> Any:
    from curl_cffi import CurlOpt  # noqa: PLC0415
    from curl_cffi import requests as creq  # noqa: PLC0415

    q = params
    candidates = _candidate_ips(host, sticky_path)
    last_exc: Exception | None = None

    # Prefer pinned edges first (natural DNS often lands on a blocked node).
    for ip in candidates:
        try:
            kwargs: dict[str, Any] = {
                "params": q,
                "headers": headers,
                "timeout": timeout,
                "impersonate": _CHROME_IMPERSONATE,
                "curl_options": {CurlOpt.RESOLVE: [f"{host}:443:{ip}"]},
            }
            resp = creq.get(url, **kwargs)
            if int(getattr(resp, "status_code", 0) or 0) == 200:
                prev = _load_sticky(sticky_path)
                _FAILED_UNTIL.pop(ip, None)
                if prev != ip:
                    logger.info("push2his CDN failover → %s (was %s)", ip, prev or "none")
                _save_sticky(sticky_path, ip)
                return resp
            last_exc = RuntimeError(f"push2his HTTP {resp.status_code} via {ip}")
            logger.debug("push2his pin %s status=%s", ip, resp.status_code)
            _mark_edge_failed(ip, sticky_path)
        except Exception as exc:  # noqa: BLE001 — try next edge
            last_exc = exc
            logger.debug("push2his pin %s failed: %s", ip, exc)
            _mark_edge_failed(ip, sticky_path)

    # Full miss: force rediscovery (TM may have rotated) and retry once.
    fresh = _candidate_ips(host, sticky_path, force_discover=True)
    for ip in fresh:
        if ip in candidates:
            continue
        try:
            kwargs = {
                "params": q,
                "headers": headers,
                "timeout": timeout,
                "impersonate": _CHROME_IMPERSONATE,
                "curl_options": {CurlOpt.RESOLVE: [f"{host}:443:{ip}"]},
            }
            resp = creq.get(url, **kwargs)
            if int(getattr(resp, "status_code", 0) or 0) == 200:
                logger.info("push2his CDN rediscover → %s", ip)
                _FAILED_UNTIL.pop(ip, None)
                _save_sticky(sticky_path, ip)
                return resp
            _mark_edge_failed(ip, sticky_path)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            _mark_edge_failed(ip, sticky_path)

    # Last resort: unpinned Chrome TLS (whatever DNS returns).
    try:
        kwargs = {
            "params": q,
            "headers": headers,
            "timeout": timeout,
            "impersonate": _CHROME_IMPERSONATE,
        }
        resp = creq.get(url, **kwargs)
        if int(getattr(resp, "status_code", 0) or 0) == 200:
            # Capture whatever edge libcurl actually used, if reported.
            primary = getattr(resp, "primary_ip", None) or getattr(resp, "ip", None)
            if primary and ":" not in str(primary):
                _save_sticky(sticky_path, str(primary))
                logger.info("push2his unpinned OK → sticky %s", primary)
        return resp
    except Exception as exc:
        if last_exc is not None:
            raise last_exc from exc
        raise


class EastMoneyClient:
    """HTTP client with automatic EastMoney auth headers."""

    def __init__(
        self,
        min_interval: float | None = None,
        *,
        config: Config | None = None,
    ):
        # Prefer Config pacing; bare clients default to 1.0s in-process spacing.
        self.config = config
        if config is not None:
            self.min_interval = 0.0
        elif min_interval is None:
            self.min_interval = 1.0
        else:
            self.min_interval = float(min_interval)
        self._last_request = 0.0
        self._proxy = None
        timeout = 15.0
        if config is not None:
            if getattr(config, "eastmoney_proxy", None):
                self._proxy = config.eastmoney_proxy
            timeout = float(getattr(config, "eastmoney_timeout_sec", 15.0) or 15.0)
        self._timeout = timeout
        # httpx>=0.28 removed ``proxies``; older httpx still needs it. The mootdx
        # pin that forced <0.26 is gone, but the floor is still 0.25.
        client_kwargs: dict = {"timeout": timeout, "follow_redirects": True}
        if self._proxy is not None:
            client_kwargs["proxy"] = self._proxy
        try:
            self._client = httpx.Client(**client_kwargs)
        except TypeError:
            if self._proxy is not None:
                client_kwargs.pop("proxy", None)
                client_kwargs["proxies"] = self._proxy
            self._client = httpx.Client(**client_kwargs)

    def _throttle(self) -> None:
        if self.config is not None:
            self.config.rate_limit("eastmoney")
            return
        if self.min_interval <= 0:
            return
        elapsed = time.time() - self._last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request = time.time()

    def get(self, url: str, **kwargs) -> Any:
        # Check the breaker before paying the rate limiter: throttling a request
        # that is about to be refused locally just adds min_interval to every
        # board of a dead sweep.
        if _needs_chrome_tls(url) and not self._proxy:
            _breaker_guard(_host_from_url(url))
        self._throttle()
        headers = kwargs.pop("headers", {})
        headers.update(build_eastmoney_headers(url))
        if _needs_chrome_tls(url):
            timeout = float(kwargs.pop("timeout", self._timeout) or self._timeout)
            params = kwargs.pop("params", None)
            kwargs.pop("follow_redirects", None)
            return _chrome_get(
                url,
                headers=headers,
                params=params,
                timeout=timeout,
                proxy=self._proxy,
                sticky_path=_sticky_path(self.config),
            )
        return self._client.get(url, headers=headers, **kwargs)

    def post(self, url: str, **kwargs) -> httpx.Response:
        self._throttle()
        headers = kwargs.pop("headers", {})
        headers.update(build_eastmoney_headers(url))
        return self._client.post(url, headers=headers, **kwargs)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> EastMoneyClient:
        return self

    def __exit__(self, *args) -> None:
        self.close()
