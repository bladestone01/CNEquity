"""The BSE adapter must accept only well-formed, correctly dated quote rows."""

import json
from datetime import date

import pytest

from cnequity.adapters.bse.daily_quotes import BseMarketDataError, fetch_daily_quotes
from cnequity.config import Config


class _Response:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        return None


class _Client:
    def __init__(self, pages: dict[int, str], redirects: int = 0):
        self.pages = pages
        self.redirects = redirects
        self.posted: list[int] = []
        self.closed = False

    def get(self, url):
        return _Response("")

    def post(self, url, *, data):
        page = int(data["page"])
        self.posted.append(page)
        if self.redirects:
            self.redirects -= 1
            return _Response("", status_code=307)
        return _Response(self.pages[page])

    def close(self):
        self.closed = True


def _jsonp(rows, total: int):
    return "null(" + json.dumps([{"content": rows, "totalElements": total}]) + ")"


def _row(code: str = "920571", trade_date: str = "20260821"):
    return {
        "hqzqdm": code,
        "hqjsrq": trade_date,
        "hqjrkp": "9.09",
        "hqzgcj": "9.66",
        "hqzdcj": "9.06",
        "hqzjcj": "9.60",
        "hqcjsl": "33952730",
        "hqcjje": "322779288.68",
    }


def test_fetches_current_bse_quote_and_paginates(tmp_path):
    client = _Client(
        {
            0: _jsonp([_row()], total=21),
            1: _jsonp([_row("920572")], total=21),
        }
    )
    cfg = Config(
        data_root=tmp_path / "data",
        sources={"bse": True},
        source_intervals={"bse": 0.0},
    )

    out = fetch_daily_quotes(date(2026, 8, 21), client=client, config=cfg)

    assert client.posted == [0, 1]
    assert out.height == 2
    assert set(out["symbol"].to_list()) == {"920571.BJ", "920572.BJ"}
    assert out.filter(out["symbol"] == "920571.BJ")["amount"].item() == pytest.approx(
        322779288.68
    )


def test_rejects_other_sessions_and_malformed_payload(tmp_path):
    client = _Client({0: _jsonp([_row(trade_date="20260820")], total=1)})
    cfg = Config(data_root=tmp_path / "data", source_intervals={"bse": 0.0})
    out = fetch_daily_quotes(date(2026, 8, 21), client=client, config=cfg)

    assert out.is_empty()
    assert out.schema["amount"].is_float()

    broken = _Client({0: "null(not-json)"})
    with pytest.raises(BseMarketDataError, match="not valid JSON"):
        fetch_daily_quotes(date(2026, 8, 21), client=broken, config=cfg)


def test_retries_a_same_endpoint_waf_redirect(tmp_path):
    client = _Client({0: _jsonp([_row()], total=1)}, redirects=1)
    cfg = Config(data_root=tmp_path / "data", source_intervals={"bse": 0.0})

    out = fetch_daily_quotes(date(2026, 8, 21), client=client, config=cfg)

    assert out.height == 1
    assert client.posted == [0, 0]


def test_fails_loud_on_an_empty_page_before_advertised_total(tmp_path):
    client = _Client({0: _jsonp([], total=21)})
    cfg = Config(data_root=tmp_path / "data", source_intervals={"bse": 0.0})

    with pytest.raises(BseMarketDataError, match="advertised total"):
        fetch_daily_quotes(date(2026, 8, 21), client=client, config=cfg)
