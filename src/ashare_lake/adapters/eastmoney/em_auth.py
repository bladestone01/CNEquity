"""EastMoney NID auth — inject nid cookie for datacenter/reportapi domains."""

from __future__ import annotations

import logging
import random
import time
from typing import TYPE_CHECKING

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
    "push2his.eastmoney.com",
    "91.push2his.eastmoney.com",
)
_QUOTE_REFERER = "https://quote.eastmoney.com/"


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
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    if any(d in url for d in _PUSH2_DOMAINS):
        headers["Referer"] = _QUOTE_REFERER
        return headers
    if any(d in url for d in _EASTMONEY_DOMAINS):
        nid = get_nid()
        if nid:
            headers["Cookie"] = f"nid={nid}"
    return headers


def is_transport_fail_fast(exc: BaseException) -> bool:
    """True for connect/timeout/protocol drops that retries will not fix overseas."""
    return isinstance(
        exc,
        (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError),
    )


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
        proxy = None
        timeout = 15.0
        if config is not None:
            if getattr(config, "eastmoney_proxy", None):
                proxy = config.eastmoney_proxy
            timeout = float(getattr(config, "eastmoney_timeout_sec", 15.0) or 15.0)
        # httpx>=0.28 removed ``proxies``; mootdx-pinned httpx<0.28 still needs it.
        client_kwargs: dict = {"timeout": timeout, "follow_redirects": True}
        if proxy is not None:
            client_kwargs["proxy"] = proxy
        try:
            self._client = httpx.Client(**client_kwargs)
        except TypeError:
            if proxy is not None:
                client_kwargs.pop("proxy", None)
                client_kwargs["proxies"] = proxy
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

    def get(self, url: str, **kwargs) -> httpx.Response:
        self._throttle()
        headers = kwargs.pop("headers", {})
        headers.update(build_eastmoney_headers(url))
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
