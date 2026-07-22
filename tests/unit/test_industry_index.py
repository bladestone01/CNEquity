"""Industry indices computed from 申万 membership × hfq bars.

The properties worth pinning are the ones whose failure is silent: using the
wrong membership snapshot still produces a plausible number, and so does a
weighted average over turnover that is not money.
"""

from __future__ import annotations

from datetime import date

import polars as pl

from ashare_lake.derive.industry_index import LEVELS, _hfq_returns, _members_as_of


def _members(rows: list[tuple[str, str, date]]) -> pl.DataFrame:
    return pl.DataFrame([{"symbol": s, "industry_code": c, "as_of_date": d} for s, c, d in rows])


def test_membership_is_point_in_time():
    """A day takes the snapshot known then, not the newest one.

    600000 moves industry in the June snapshot. May's index must still see it in
    its old industry, or every historical index silently inherits today's
    classification.
    """
    members = _members(
        [
            ("600000.SH", "240301", date(2026, 5, 1)),
            ("600000.SH", "270101", date(2026, 6, 1)),
        ]
    )
    panel = _members_as_of(members, [date(2026, 5, 15), date(2026, 6, 15)])
    may = panel.filter(pl.col("trade_date") == date(2026, 5, 15))
    jun = panel.filter(pl.col("trade_date") == date(2026, 6, 15))
    assert may["industry_code"].to_list() == ["240301"]
    assert jun["industry_code"].to_list() == ["270101"]


def test_days_before_the_first_snapshot_are_dropped():
    """No snapshot yet means no membership — not the earliest one applied backwards."""
    members = _members([("600000.SH", "240301", date(2026, 5, 1))])
    panel = _members_as_of(members, [date(2026, 4, 1), date(2026, 5, 15)])
    assert panel["trade_date"].unique().to_list() == [date(2026, 5, 15)]


def test_levels_come_from_the_code_prefix():
    """申万 codes nest: 240301 铝 -> 2403 工业金属 -> 24 有色金属."""
    code = "240301"
    assert code[: LEVELS["L1"]] == "24"
    assert code[: LEVELS["L2"]] == "2403"
    assert code[: LEVELS["L3"]] == "240301"


def test_unrealistic_turnover_is_treated_as_missing(monkeypatch, tmp_path):
    """A feed can report positive turnover that is not money.

    2026-07-22 arrived with amount ~5.9e-39 for the whole universe. Passing that
    into an amount-weighted mean produces a number that looks like a return, so
    the value has to be discarded at the source.
    """
    bars = pl.DataFrame(
        [
            {
                "symbol": "600000.SH",
                "trade_date": date(2026, 7, 21),
                "close": 10.0,
                "amount": 5.0e8,
            },
            {
                "symbol": "600000.SH",
                "trade_date": date(2026, 7, 22),
                "close": 11.0,
                "amount": 5.9e-39,
            },
        ]
    )
    monkeypatch.setattr("ashare_lake.query.reader.load", lambda *a, **k: bars)
    out = _hfq_returns(object(), date(2026, 7, 1), date(2026, 7, 22), ["600000.SH"])
    assert out.height == 1  # first bar has no prior close
    row = out.row(0, named=True)
    assert row["ret"] == 0.1
    assert row["amount"] is None, "sub-yuan turnover must not survive as a weight"
