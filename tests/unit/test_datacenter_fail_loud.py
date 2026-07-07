import pytest

from stock_data_engine.adapters.eastmoney.datacenter import (
    EastMoneyDatacenterError,
    fetch_datacenter,
)


class FakeClient:
    def __init__(self, responses: list[Exception | dict]):
        self.responses = responses
        self.calls = 0

    def get(self, url: str, **kwargs):
        if self.calls >= len(self.responses):
            raise RuntimeError("unexpected call")
        item = self.responses[self.calls]
        self.calls += 1
        if isinstance(item, Exception):
            raise item

        class Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return item

        return Resp()


def test_fetch_datacenter_raises_on_page_failure():
    client = FakeClient([RuntimeError("network"), RuntimeError("network"), RuntimeError("network")])
    with pytest.raises(EastMoneyDatacenterError):
        fetch_datacenter(client, "RPT_TEST", "COL", max_retries=3, retry_backoff_seconds=0)


def test_fetch_datacenter_treats_empty_result_as_no_rows():
    client = FakeClient([{"success": False, "message": "返回数据为空", "code": 0}])
    rows = fetch_datacenter(client, "RPT_TEST", "COL", max_retries=1, retry_backoff_seconds=0)
    assert rows == []


def test_fetch_datacenter_raises_on_api_rejection():
    client = FakeClient([{"success": False, "message": "TRADE_DATE列不存在", "code": 9501}])
    with pytest.raises(EastMoneyDatacenterError, match="TRADE_DATE列不存在"):
        fetch_datacenter(client, "RPT_TEST", "COL", max_retries=1, retry_backoff_seconds=0)


def test_fetch_datacenter_paginates_until_short_page():
    client = FakeClient(
        [
            {"success": True, "result": {"data": [{"x": 1}] * 5000}},
            {"success": True, "result": {"data": [{"x": 2}]}},
        ]
    )
    rows = fetch_datacenter(client, "RPT_TEST", "COL", page_size=5000)
    assert len(rows) == 5001
    assert client.calls == 2


def test_fetch_datacenter_clamps_page_size_to_500():
    """pageSize>500 must be clamped or EM silently truncates high-volume reports."""
    captured = {}

    class CapClient:
        def get(self, url: str, **kwargs):
            captured["url"] = url

            class Resp:
                def raise_for_status(self):
                    return None

                def json(self):
                    return {"success": True, "result": {"data": []}}

            return Resp()

    fetch_datacenter(CapClient(), "RPT_TEST", "COL", page_size=5000, max_retries=1)
    assert "pageSize=500" in captured["url"]
    assert "pageSize=5000" not in captured["url"]
