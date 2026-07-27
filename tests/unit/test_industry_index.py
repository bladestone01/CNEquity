"""Industry indices computed from 申万 membership × hfq bars.

The properties worth pinning are the ones whose failure is silent: using the
wrong membership snapshot still produces a plausible number, and so does a
weighted average over turnover that is not money.
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from ashare_lake.config import Config
from ashare_lake.derive.industry_index import (
    LEVELS,
    _hfq_returns,
    _members_as_of,
    compute_industry_index,
    derive_industry_index,
)
from ashare_lake.storage.state import StateStore


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


def _seed_sw_membership(cfg: Config, rows: list[dict]) -> None:
    part = cfg.curated_root / "industry_members" / "as_of_date=2026-05"
    part.mkdir(parents=True)
    pl.DataFrame(rows).write_parquet(part / "part-000.parquet")


def _seed_adj_factor(cfg: Config, symbols: list[str]) -> None:
    part = cfg.derived_root / "adj_factors" / "trade_date=2026-05-15"
    part.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": symbols,
            "trade_date": [date(2026, 5, 15)] * len(symbols),
            "adjust_type": ["hfq"] * len(symbols),
            "adj_factor": [1.0] * len(symbols),
            "source": ["sina"] * len(symbols),
            "data_version": ["v1"] * len(symbols),
            "fetched_at": ["2026-05-15T00:00:00+00:00"] * len(symbols),
        }
    ).write_parquet(part / "part-000.parquet")


def test_compute_industry_index_aggregates_levels(tmp_path, monkeypatch):
    cfg = Config(data_root=tmp_path / "data")
    _seed_sw_membership(
        cfg,
        [
            {
                "symbol": "600000.SH",
                "classification_system": "sw",
                "industry_code": "240301",
                "industry_name": "铝",
                "as_of_date": date(2026, 5, 1),
                "source": "sw",
                "data_version": "v1",
                "fetched_at": "2026-05-01T00:00:00+00:00",
            },
            {
                "symbol": "600001.SH",
                "classification_system": "sw",
                "industry_code": "240301",
                "industry_name": "铝",
                "as_of_date": date(2026, 5, 1),
                "source": "sw",
                "data_version": "v1",
                "fetched_at": "2026-05-01T00:00:00+00:00",
            },
        ],
    )
    _seed_adj_factor(cfg, ["600000.SH", "600001.SH"])

    rets = pl.DataFrame(
        [
            {
                "symbol": "600000.SH",
                "trade_date": date(2026, 5, 15),
                "ret": 0.10,
                "amount": 1.0e8,
            },
            {
                "symbol": "600001.SH",
                "trade_date": date(2026, 5, 15),
                "ret": 0.00,
                "amount": 1.0e8,
            },
        ]
    )
    monkeypatch.setattr(
        "ashare_lake.derive.industry_index._hfq_returns",
        lambda *a, **k: rets,
    )

    frame = compute_industry_index(cfg, date(2026, 5, 15), date(2026, 5, 15), levels=("L3",))
    assert frame.height == 2  # equal + amount
    equal = frame.filter(pl.col("weighting") == "equal").row(0, named=True)
    assert equal["industry_code"] == "240301"
    assert equal["ret"] == pytest.approx(0.05)
    assert equal["n_members"] == 2
    assert equal["n_priced"] == 2
    assert equal["n_excluded"] == 0


def test_compute_industry_index_empty_without_membership(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    (cfg.curated_root / "industry_members").mkdir(parents=True)
    # Empty directory → scan fails or empty; seed an empty-compatible state by
    # writing zero SW rows under a different source only.
    part = cfg.curated_root / "industry_members" / "as_of_date=2026-05"
    part.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["600000.SH"],
            "classification_system": ["em"],
            "industry_code": ["白酒"],
            "industry_name": ["白酒"],
            "as_of_date": [date(2026, 5, 1)],
            "source": ["eastmoney"],
            "data_version": ["v1"],
            "fetched_at": ["2026-05-01T00:00:00+00:00"],
        }
    ).write_parquet(part / "part-000.parquet")
    frame = compute_industry_index(cfg, date(2026, 5, 1), date(2026, 5, 15))
    assert frame.is_empty()


def test_derive_industry_index_writes_and_watermarks(tmp_path, monkeypatch):
    cfg = Config(data_root=tmp_path / "data")
    frame = pl.DataFrame(
        {
            "trade_date": [date(2026, 5, 15), date(2026, 5, 15)],
            "industry_code": ["240301", "240301"],
            "level": ["L3", "L3"],
            "weighting": ["equal", "amount"],
            "ret": [0.05, 0.05],
            "n_members": [2, 2],
            "n_priced": [2, 2],
            "n_excluded": [0, 0],
            "amount": [2.0e8, 2.0e8],
        }
    )
    monkeypatch.setattr(
        "ashare_lake.derive.industry_index.compute_industry_index",
        lambda *a, **k: frame,
    )
    summary = derive_industry_index(cfg, start=date(2026, 5, 15), end=date(2026, 5, 15))
    assert summary["rows"] == 2
    out = list((cfg.derived_root / "industry_index").glob("**/part-000.parquet"))
    assert out
    assert StateStore(cfg.meta_root).get_date("industry_index") == date(2026, 5, 15)

    # Already current → no-op note.
    again = derive_industry_index(cfg, start=date(2026, 5, 16), end=date(2026, 5, 15))
    assert again["rows"] == 0
    assert "already current" in again["note"]
