"""Offline coverage for the explicit 同花顺 BJ corporate-action repair."""

from datetime import date

import polars as pl

import cnequity.steps  # noqa: F401
from cnequity.adapters.ths import corporate_actions as ca
from cnequity.config import Config
from cnequity.steps import events

_PAGE = """
<html><body>
<table class="m_table">
<tr><th>报告期</th><th>董事会日期</th><th>股东大会预案公告日期</th>
<th>实施公告日</th><th>分红方案说明</th><th>A股股权登记日</th>
<th>A股除权除息日</th><th>分红总额</th><th>方案进度</th></tr>
<tr><td>2021年报</td><td>2022-04-19</td><td>2022-05-27</td>
<td>2022-06-29</td><td>10送3股派0.5元(含税)</td><td>2022-07-06</td>
<td>2022-07-07</td><td>766.67万</td><td>实施方案</td></tr>
<tr><td>2020年报</td><td>2021-04-19</td><td>2021-05-27</td>
<td>2021-06-29</td><td>不分配不转增</td><td>--</td>
<td>--</td><td>--</td><td>实施方案</td></tr>
<tr><td>2019年报</td><td>2020-04-19</td><td>2020-05-27</td>
<td>2020-06-29</td><td>10配3股，配股价5.00元</td><td>2020-07-06</td>
<td>2020-07-07</td><td>--</td><td>董事会预案</td></tr>
</table>
</body></html>
"""


def test_parse_plan_uses_per_share_units_and_keeps_combined_actions():
    values = ca._parse_plan("10转4股派4.50元(含税)")
    assert values == {
        "cash_dividend": 0.45,
        "bonus_ratio": 0.0,
        "transfer_ratio": 0.4,
        "allotment_ratio": 0.0,
        "allotment_price": None,
    }


def test_rows_from_page_requires_completed_plan_and_filters_window():
    rows = ca._rows_from_page("430090.BJ", _PAGE, date(2022, 1, 1), date(2022, 12, 31))
    assert len(rows) == 2
    by_type = {row["action_type"]: row for row in rows}
    assert by_type["cash_dividend"]["cash_dividend"] == 0.05
    assert by_type["bonus"]["bonus_ratio"] == 0.3
    assert by_type["cash_dividend"]["ex_date"] == date(2022, 7, 7)


def test_fetch_corporate_actions_only_queries_bj_and_reports_page_failures():
    calls: list[str] = []

    def page(code: str) -> str:
        calls.append(code)
        if code == "430198":
            raise RuntimeError("source unavailable")
        return _PAGE

    frame, failed = ca.fetch_corporate_actions_ths(
        ["600519.SH", "430090.BJ", "430198.BJ", "430090.BJ"],
        date(2001, 1, 1),
        date(2025, 12, 31),
        page_fetcher=page,
    )
    assert calls == ["430090", "430198"]
    assert failed == ["430198.BJ"]
    assert frame["symbol"].unique().to_list() == ["430090.BJ"]
    assert frame["ex_date"].to_list() == [date(2022, 7, 7)] * 2


def test_step_ths_repair_is_scoped_to_delisted_bj_and_keeps_provenance(tmp_path, monkeypatch):
    cfg = Config(data_root=tmp_path / "lake", sources={"ths_bonus": True})
    cfg._backfill = True
    cfg._backfill_start = date(2020, 1, 1)
    cfg._backfill_end = date(2020, 12, 31)
    cfg._corporate_actions_ths_repair = True

    monkeypatch.setattr(events, "load_symbols", lambda _cfg: ["430090.BJ", "600000.SH"])
    monkeypatch.setattr(
        events,
        "instrument_metadata",
        lambda _cfg: pl.DataFrame(
            {
                "symbol": ["430090.BJ", "600000.SH"],
                "list_date": [date(2010, 1, 1)] * 2,
                "delist_date": [date(2025, 9, 30), None],
            }
        ),
    )
    monkeypatch.setattr(events, "fetch_corporate_actions", lambda *args, **kwargs: pl.DataFrame())

    repair_rows = pl.DataFrame(
        {
            "symbol": ["430090.BJ"],
            "ex_date": [date(2020, 7, 7)],
            "action_type": ["cash_dividend"],
            "cash_dividend": [0.05],
            "bonus_ratio": [0.0],
            "transfer_ratio": [0.0],
            "allotment_ratio": [None],
            "allotment_price": [None],
        },
        schema_overrides={"allotment_ratio": pl.Float64, "allotment_price": pl.Float64},
    )
    seen = []

    def fake_ths(symbols, start, end, **kwargs):
        seen.append((symbols, start, end))
        return repair_rows, []

    monkeypatch.setattr(events, "fetch_corporate_actions_ths", fake_ths)
    result = events.step_corporate_actions(cfg, date(2020, 12, 31), "run-1", {})

    assert seen == [(["430090.BJ"], date(2020, 1, 1), date(2020, 12, 31))]
    assert result["rows_written"] == 1
    staged = list((cfg.staging_root / "corporate_actions").glob("**/*.parquet"))
    assert len(staged) == 1
    assert pl.read_parquet(staged[0])["source"].to_list() == ["ths"]
