"""MOFCOM 社会融资规模增量 adapter.

Covers the two defects that made the AkShare-wrapped version of this series
never reach curated (issue #3).
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from ashare_lake.adapters.mofcom import social_financing as sf

# One record in MOFCOM's real shape: compact YYYYMM date, keyed components.
_RECORD = {
    "date": "202604",
    "ndbab": -5284,
    "entrustloan": -283,
    "forcloan": 184,
    "rmblaon": -4006,
    "bibae": 4520,
    "tiosfs": 6245,
    "sfinfe": 835.0,
    "trustloan": -129,
}


class _Resp:
    def __init__(self, payload, status: int = 200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)  # type: ignore[arg-type]

    def json(self):
        return self._payload


def _patch_post(monkeypatch, payload):
    monkeypatch.setattr(sf.httpx, "post", lambda *a, **k: _Resp(payload))


def test_compact_yyyymm_month_is_parsed(monkeypatch):
    """`202604` must become 2024-04-30, not be dropped.

    The macro adapter's old date parser only accepted *separated* month forms,
    so MOFCOM's compact `YYYYMM` fell through to None and every 社融 row was
    silently discarded — the indicator was configured and documented but never
    written a single row.
    """
    _patch_post(monkeypatch, [_RECORD])
    rows = sf.fetch_social_financing()
    assert rows == [{"obs_date": date(2026, 4, 30), "value": 6245.0}]


def test_headline_is_read_by_key_not_position(monkeypatch):
    """Reordering the payload keys must not change which number we read.

    AkShare relabelled these columns positionally, so a reordered response would
    have relabelled 社融 as 委托贷款 with nothing raising.
    """
    reordered = {k: _RECORD[k] for k in reversed(list(_RECORD))}
    _patch_post(monkeypatch, [reordered])
    assert sf.fetch_social_financing()[0]["value"] == 6245.0


@pytest.mark.parametrize(
    "payload",
    [
        [{"date": "not-a-month", "tiosfs": 1}],
        [{"date": "202613", "tiosfs": 1}],  # month 13
        [{"date": "202604"}],  # headline key missing
        [{"date": "202604", "tiosfs": "n/a"}],
        [{"date": "202604", "tiosfs": None}],
    ],
)
def test_unusable_records_are_dropped_not_fabricated(monkeypatch, payload):
    _patch_post(monkeypatch, payload)
    assert sf.fetch_social_financing() == []


def test_network_failure_degrades_to_empty(monkeypatch):
    def _boom(*_a, **_k):
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(sf.httpx, "post", _boom)
    assert sf.fetch_social_financing() == []


def test_unexpected_payload_shape_degrades_to_empty(monkeypatch):
    _patch_post(monkeypatch, {"error": "nope"})
    assert sf.fetch_social_financing() == []


def test_rate_limit_is_applied(monkeypatch):
    calls: list[str] = []

    class _Cfg:
        sources: dict[str, bool] = {}

        def rate_limit(self, source):
            calls.append(source)

    _patch_post(monkeypatch, [_RECORD])
    sf.fetch_social_financing(config=_Cfg())
    assert calls == ["mofcom"]
