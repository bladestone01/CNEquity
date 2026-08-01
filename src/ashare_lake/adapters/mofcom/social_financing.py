"""商务部数据中心 — 社会融资规模增量 (monthly aggregate financing).

https://data.mofcom.gov.cn/gnmy/shrzgm.shtml

This replaces the AkShare wrapper (``macro_china_shrzgm``) for two reasons.

The wrapper renamed the response columns *positionally* — it assigned a fixed
list of Chinese labels to whatever key order MOFCOM happened to return, so a
reordered payload would have relabelled 社融 as 委托贷款 with nothing raising.
The response is keyed (``tiosfs``, ``rmblaon``, …), so reading by key removes
that failure mode entirely.

The wrapper also formats 月份 as compact ``YYYYMM``, which the macro adapter's
date parser did not accept — every 社融 row was silently dropped before it
reached curated, so the indicator was advertised but never actually written.
"""

from __future__ import annotations

import calendar
import logging
import re
from datetime import date

import httpx

logger = logging.getLogger(__name__)

_URL = "https://data.mofcom.gov.cn/datamofcom/front/gnmy/shrzgmQuery"
_TIMEOUT_SECONDS = 45.0

# Response keys. Only the headline aggregate is mapped to an indicator today;
# the components are documented so a future breakdown does not have to re-derive
# them from the wire.
#   tiosfs      社会融资规模增量 (headline)
#   rmblaon     其中-人民币贷款        forcloan   其中-外币贷款
#   entrustloan 其中-委托贷款          trustloan  其中-信托贷款
#   ndbab       其中-未贴现银行承兑汇票  bibae      其中-企业债券
#   sfinfe      其中-非金融企业境内股票融资
_TOTAL_KEY = "tiosfs"
_DATE_KEY = "date"

# MOFCOM reports 月份 as compact YYYYMM ("202604").
_YYYYMM_RE = re.compile(r"^(\d{4})(\d{2})$")


def _month_end(value: object) -> date | None:
    """Parse a compact ``YYYYMM`` month into its last calendar day."""
    match = _YYYYMM_RE.match(str(value).strip())
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        return None
    return date(year, month, calendar.monthrange(year, month)[1])


def fetch_social_financing(*, config=None) -> list[dict]:
    """Return ``[{"obs_date": date, "value": float}, ...]``, newest-first.

    Network or shape failures degrade to an empty list: this is one indicator on
    a multi-source dataset, and the daily run should not fail over it.
    """
    if config is not None:
        config.rate_limit("mofcom")

    try:
        # Plain httpx, not the curl_cffi impersonation used for EastMoney —
        # MOFCOM serves this endpoint to a default client but was observed to
        # hang on a Chrome-impersonated handshake.
        resp = httpx.post(_URL, timeout=_TIMEOUT_SECONDS)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        logger.warning("MOFCOM social financing fetch skipped: %s", exc)
        return []

    if not isinstance(payload, list):
        logger.warning("MOFCOM social financing: unexpected payload type %s", type(payload))
        return []

    rows: list[dict] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        obs = _month_end(item.get(_DATE_KEY))
        if obs is None:
            continue
        raw = item.get(_TOTAL_KEY)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        rows.append({"obs_date": obs, "value": value})

    if not rows:
        # Reaching here means a 200 whose shape we no longer understand — a key
        # rename is exactly the drift this adapter exists to make visible.
        logger.warning(
            "MOFCOM social financing returned %d records but no usable rows; "
            "check the %r / %r keys",
            len(payload),
            _DATE_KEY,
            _TOTAL_KEY,
        )
    return rows
