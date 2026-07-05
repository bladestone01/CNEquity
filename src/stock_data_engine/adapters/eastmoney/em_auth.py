"""EastMoney NID auth — inject nid cookie for datacenter/reportapi domains."""

from __future__ import annotations

import logging
import random
import time
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from stock_data_engine.config import Config

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
_PUSH2_DOMAINS = ("push2.eastmoney.com",)


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
        return headers
    if any(d in url for d in _EASTMONEY_DOMAINS):
        nid = get_nid()
        if nid:
            headers["Cookie"] = f"nid={nid}"
    return headers


class EastMoneyClient:
    """HTTP client with automatic EastMoney auth headers."""

    def __init__(
        self,
        min_interval: float = 0.0,
        *,
        config: Config | None = None,
    ):
        self.min_interval = min_interval
        self.config = config
        self._last_request = 0.0
        self._client = httpx.Client(timeout=30.0)

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

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> EastMoneyClient:
        return self

    def __exit__(self, *args) -> None:
        self.close()
