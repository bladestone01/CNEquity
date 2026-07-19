"""Paginated snapshot fetches must fail loud, never silently truncate."""

from datetime import date

import pytest

from ashare_lake.adapters.cninfo.regulatory import fetch_regulatory_events
from ashare_lake.adapters.eastmoney import clist
from ashare_lake.adapters.eastmoney.clist import fetch_clist_pages


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _clist_payload(codes, total):
    return {"data": {"total": total, "diff": [{"f12": c, "f13": 1} for c in codes]}}


def test_clist_raises_when_all_hosts_fail(monkeypatch):
    monkeypatch.setattr(clist.time, "sleep", lambda *_: None)

    class AllFail:
        def get(self, url, **kwargs):
            raise RuntimeError("connection reset")

    with pytest.raises(RuntimeError, match="failed on all hosts"):
        fetch_clist_pages(AllFail(), fields="f12,f13")


def test_clist_raises_on_midpagination_truncation(monkeypatch):
    monkeypatch.setattr(clist.time, "sleep", lambda *_: None)

    class FailSecondPage:
        def __init__(self):
            self.calls = 0

        def get(self, url, **kwargs):
            self.calls += 1
            # page 1 returns a full page (total forces a second page); page 2 dies.
            if "pn=2" in url or self.calls > 1:
                raise RuntimeError("read timeout on page 2")
            return _Resp(_clist_payload([f"{600000 + i}" for i in range(5000)], total=10000))

    with pytest.raises(RuntimeError, match="page 2 failed"):
        fetch_clist_pages(FailSecondPage(), fields="f12,f13", page_size=5000)


def test_regulatory_raises_on_page_failure():
    class FailPost:
        def post(self, url, **kwargs):
            raise RuntimeError("cninfo 503")

        def close(self):
            return None

    with pytest.raises(RuntimeError, match="regulatory pagination failed"):
        fetch_regulatory_events(date(2024, 6, 28), client=FailPost())
