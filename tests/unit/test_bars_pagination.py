from datetime import date

import polars as pl

from stock_data_engine.adapters.tdx_protocol.bars import fetch_bars_paginated


class FakeClient:
    def __init__(self, pages: list[list[dict]]):
        self.pages = pages
        self.calls = 0

    def bars(self, symbol, frequency, market, start, offset):
        if self.calls >= len(self.pages):
            return None
        page = self.pages[self.calls]
        self.calls += 1
        return pl.DataFrame(page)


def test_paginated_fetch_merges_pages():
    page1 = [
        {
            "datetime": date(2024, 6, 27),
            "open": 1,
            "high": 1,
            "low": 1,
            "close": 1,
            "volume": 1,
            "amount": 1,
        }
    ] + [
        {
            "datetime": date(2020, 1, 2),
            "open": 1,
            "high": 1,
            "low": 1,
            "close": 1,
            "volume": 1,
            "amount": 1,
        }
    ] * 799
    pages = [
        page1,
        [{"datetime": date(2024, 6, 26), "open": 2, "high": 2, "low": 2, "close": 2, "volume": 2, "amount": 2}],
        [],
    ]
    client = FakeClient(pages)
    rows = fetch_bars_paginated(client, "600519.SH", date(2024, 6, 26), date(2024, 6, 27))
    assert len(rows) == 2
    assert client.calls >= 2
