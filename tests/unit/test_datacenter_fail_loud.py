import pytest

from stock_data_engine.adapters.eastmoney.datacenter import EastMoneyDatacenterError, fetch_datacenter


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


def test_fetch_datacenter_paginates_until_short_page():
    client = FakeClient(
        [
            {"result": {"data": [{"x": 1}] * 5000}},
            {"result": {"data": [{"x": 2}]}},
        ]
    )
    rows = fetch_datacenter(client, "RPT_TEST", "COL", page_size=5000)
    assert len(rows) == 5001
    assert client.calls == 2
