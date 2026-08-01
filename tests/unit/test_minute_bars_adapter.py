from datetime import date, datetime, time, timedelta

import pytest

from ashare_lake.adapters.tdx_protocol.minute_bars import (
    FREQUENCIES,
    TdxMinuteBarsError,
    bars_per_session,
    category_for,
    fetch_minute_bars_paginated,
    in_session,
    pages_for_window,
)


def _bar(stamp: datetime, **over):
    """One wire row as the TDX parser emits it (components plus a string)."""
    row = {
        "year": stamp.year,
        "month": stamp.month,
        "day": stamp.day,
        "hour": stamp.hour,
        "minute": stamp.minute,
        "datetime": stamp.strftime("%Y-%m-%d %H:%M"),
        "open": 10.0,
        "high": 10.5,
        "low": 9.5,
        "close": 10.2,
        "vol": 1000,
        "volume": 1000,
        "amount": 10200.0,
    }
    row.update(over)
    return row


def _session(day: date, count: int = 240) -> list[dict]:
    """The first *count* closing-minute labels of a session, in order."""
    stamps = []
    minute = datetime(day.year, day.month, day.day, 9, 31)
    while len(stamps) < count:
        if in_session(minute):
            stamps.append(minute)
        minute += timedelta(minutes=1)
    return [_bar(s) for s in stamps]


class FakeClient:
    def __init__(self, pages: list[list[dict]]):
        self.pages = pages
        self.calls: list[dict] = []

    def bars(self, symbol, frequency, market, start, offset):
        self.calls.append(
            {"symbol": symbol, "frequency": frequency, "market": market, "start": start}
        )
        index = len(self.calls) - 1
        return self.pages[index] if index < len(self.pages) else []


def test_category_and_session_size_per_frequency():
    assert category_for("1m") == 8
    assert category_for("5m") == 0
    assert bars_per_session("1m") == 240
    assert bars_per_session("5m") == 48
    with pytest.raises(ValueError, match="unsupported intraday frequency"):
        category_for("2m")


def test_frequencies_cover_a_full_session():
    # 4 trading hours: every frequency's bar count must multiply back to 240
    # minutes, which is what makes bars_per_session usable as a gap yardstick.
    minutes = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "60m": 60}
    for label, per_bar in minutes.items():
        assert label in FREQUENCIES
        assert bars_per_session(label) * per_bar == 240


@pytest.mark.parametrize(
    ("clock", "expected"),
    [
        ((9, 31), True),  # first bar of the session
        ((9, 30), False),  # the opening auction is inside the 09:31 bar
        ((11, 30), True),  # last bar before lunch
        ((12, 15), False),  # lunch break
        ((13, 0), False),  # 13:01 is the first afternoon label, not 13:00
        ((13, 1), True),
        ((15, 0), True),  # closing auction
        ((15, 1), False),
    ],
)
def test_in_session_boundaries(clock, expected):
    assert in_session(datetime(2026, 7, 31, *clock)) is expected


def test_fetch_keeps_only_in_window_session_bars():
    day = date(2026, 7, 31)
    page = [
        _bar(datetime(2026, 7, 30, 14, 59)),  # before the window
        _bar(datetime(2026, 7, 31, 9, 31)),
        _bar(datetime(2026, 7, 31, 12, 15)),  # lunch — a decode error
        _bar(datetime(2026, 7, 31, 15, 0)),
    ]
    rows = fetch_minute_bars_paginated(FakeClient([page]), "600519.SH", day, day)
    assert [r["bar_time"] for r in rows] == [
        datetime(2026, 7, 31, 9, 31),
        datetime(2026, 7, 31, 15, 0),
    ]
    assert {r["frequency"] for r in rows} == {"1m"}
    assert {r["trade_date"] for r in rows} == {day}


def test_fetch_maps_market_and_category_from_symbol():
    day = date(2026, 7, 31)
    client = FakeClient([[_bar(datetime(2026, 7, 31, 9, 31))]])
    fetch_minute_bars_paginated(client, "000001.SZ", day, day, frequency="5m")
    assert client.calls[0]["market"] == 0
    assert client.calls[0]["frequency"] == 0
    assert client.calls[0]["symbol"] == "000001"


def test_fetch_refuses_beijing_symbols():
    # TDX has no BJ route and there is no intraday fallback vendor, so an empty
    # list would read as "did not trade" rather than "cannot be served".
    with pytest.raises(TdxMinuteBarsError, match="Beijing"):
        fetch_minute_bars_paginated(
            FakeClient([]), "920819.BJ", date(2026, 7, 31), date(2026, 7, 31)
        )


def test_fetch_pages_until_reaching_window_start():
    full = _session(date(2026, 7, 31))
    older = _session(date(2026, 7, 29))
    # 800-row pages force a second request; the second reaches before `start`.
    page1 = (full + full + full + full)[:800]
    page2 = (older + older + older + older)[:800]
    client = FakeClient([page1, page2])
    rows = fetch_minute_bars_paginated(client, "600519.SH", date(2026, 7, 30), date(2026, 7, 31))
    assert len(client.calls) == 2
    assert client.calls[1]["start"] == 800
    # Page 2 is entirely older than the window, so nothing from it survives.
    assert {r["trade_date"] for r in rows} == {date(2026, 7, 31)}


def test_fetch_respects_max_pages():
    page = _session(date(2026, 7, 31)) * 4
    client = FakeClient([page[:800], page[:800], page[:800]])
    fetch_minute_bars_paginated(
        client, "600519.SH", date(2020, 1, 1), date(2026, 7, 31), max_pages=2
    )
    assert len(client.calls) == 2


def test_first_page_failure_always_raises():
    class Broken:
        def bars(self, **kwargs):
            raise RuntimeError("connection reset")

    with pytest.raises(TdxMinuteBarsError, match="start=0"):
        fetch_minute_bars_paginated(Broken(), "600519.SH", date(2026, 7, 31), date(2026, 7, 31))


def test_lunch_boundary_padding_bar_is_dropped():
    """The source pads inactive instruments with a 13:00 bar.

    Observed on 162107.SZ (a barely-traded LOF): a 13:00-labelled bar on days
    it did not trade, zero volume, close carried forward. 13:01 is the first
    real afternoon label, so 13:00 is padding — keeping it would put a phantom
    bar in every gap check.
    """
    page = [
        _bar(datetime(2026, 7, 31, 11, 30)),
        _bar(datetime(2026, 7, 31, 13, 0), vol=0, volume=0, amount=0.0, close=1.0),
        _bar(datetime(2026, 7, 31, 13, 1)),
    ]
    rows = fetch_minute_bars_paginated(
        FakeClient([page]), "162107.SZ", date(2026, 7, 31), date(2026, 7, 31)
    )
    assert [r["bar_time"].time() for r in rows] == [time(11, 30), time(13, 1)]


def test_no_trade_minute_stores_exact_zeros():
    # The wire decoder maps a raw 0 volume to 2**-127, not to 0.0. Left alone
    # that denormal lands in `amount` and quietly breaks the lake's no-trade
    # convention (volume=0, amount=0).
    denormal = 2.0**-127
    page = [_bar(datetime(2026, 7, 31, 14, 59), vol=denormal, volume=denormal, amount=denormal)]
    rows = fetch_minute_bars_paginated(
        FakeClient([page]), "600519.SH", date(2026, 7, 31), date(2026, 7, 31)
    )
    assert rows[0]["volume"] == 0
    assert rows[0]["amount"] == 0.0


def test_real_quantities_survive_the_zero_snap():
    page = [_bar(datetime(2026, 7, 31, 9, 31), vol=67700, volume=67700, amount=91_450_000.0)]
    rows = fetch_minute_bars_paginated(
        FakeClient([page]), "600519.SH", date(2026, 7, 31), date(2026, 7, 31)
    )
    assert rows[0]["volume"] == 67700
    assert rows[0]["amount"] == 91_450_000.0


def test_duplicate_bars_are_deduped_by_primary_key():
    stamp = datetime(2026, 7, 31, 9, 31)
    page = [_bar(stamp, close=10.0), _bar(stamp, close=11.0)]
    rows = fetch_minute_bars_paginated(
        FakeClient([page]), "600519.SH", date(2026, 7, 31), date(2026, 7, 31)
    )
    assert len(rows) == 1


def test_pages_for_window_covers_the_whole_window():
    # 240 bars a day against 800-bar pages: 3 days fit in one page but never
    # align to it, so the bound always carries a spare page.
    assert pages_for_window("1m", 3) == 2
    assert pages_for_window("1m", 95) == 30
    assert pages_for_window("5m", 491) == 31
    assert pages_for_window("1m", 1) == 2
