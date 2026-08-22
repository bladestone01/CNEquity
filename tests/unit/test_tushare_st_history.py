"""Offline contract tests for the optional Tushare BJ ST adapter."""

from __future__ import annotations

from datetime import date

from cnequity.adapters.tushare.st_history import fetch_st_history


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Client:
    def __init__(self, rows_by_code):
        self.rows_by_code = rows_by_code
        self.calls = []

    def post(self, url, *, json):
        self.calls.append(json)
        code = json["params"]["ts_code"]
        rows = self.rows_by_code.get(code, [])
        return _Response(
            {
                "code": 0,
                "data": {
                    "fields": ["ts_code", "name", "trade_date", "type", "type_name"],
                    "items": rows,
                },
            }
        )


def _row(code: str, trade_date: str, kind: str = "ST"):
    return [code, "*ST测试", trade_date, kind, "风险警示板"]


def test_emits_explicit_normal_rows_and_maps_legacy_bj_code():
    client = _Client({"920001.BJ": [_row("920001.BJ", "20170104")]})
    df, failed = fetch_st_history(
        ["873001.BJ"],
        date(2017, 1, 1),
        date(2017, 1, 5),
        token="secret",
        client=client,
        trading_dates={
            "873001.BJ": [date(2017, 1, 3), date(2017, 1, 4)],
        },
    )

    assert failed == []
    assert {call["params"]["ts_code"] for call in client.calls} == {
        "873001.BJ",
        "920001.BJ",
    }
    assert df.sort("trade_date")["status"].to_list() == ["normal", "st"]
    assert df["symbol"].unique().to_list() == ["873001.BJ"]


def test_pre_floor_bars_remain_unresolved_without_network_call():
    client = _Client({})
    df, failed = fetch_st_history(
        ["920001.BJ"],
        date(2016, 1, 1),
        date(2017, 1, 5),
        token="secret",
        client=client,
        trading_dates={"920001.BJ": [date(2016, 12, 30)]},
    )

    assert df.is_empty()
    assert failed == ["920001.BJ"]
    assert client.calls == []


def test_unknown_st_type_fails_closed():
    client = _Client({"920001.BJ": [_row("920001.BJ", "20170104", "RISK")]})
    df, failed = fetch_st_history(
        ["920001.BJ"],
        date(2017, 1, 1),
        date(2017, 1, 5),
        token="secret",
        client=client,
        trading_dates={"920001.BJ": [date(2017, 1, 4)]},
    )

    assert df.is_empty()
    assert failed == ["920001.BJ"]
