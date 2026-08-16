from datetime import date, datetime, timezone

import polars as pl
import pytest

from cnequity.config import Config
from cnequity.steps import capital as cap
from cnequity.steps import http_common
from cnequity.steps.common import (
    SnapshotBackfillError,
    fetch_incremental_daily,
    incremental_trade_dates,
    is_trading_day,
    list_trading_dates,
)
from cnequity.storage.state import StateStore


def _seed_trading_calendar(cfg: Config, start: date, end: date) -> None:
    rows = []
    d = start
    while d <= end:
        rows.append({"trade_date": d, "is_trading": d.weekday() < 5})
        d = date.fromordinal(d.toordinal() + 1)
    path = cfg.curated_root / "trading_calendar" / "part-merged.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(path)


def test_incremental_trade_dates_uses_watermark(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    _seed_trading_calendar(cfg, date(2024, 6, 24), date(2024, 6, 28))
    StateStore(cfg.meta_root).set_date("fund_flow", date(2024, 6, 25))

    dates = incremental_trade_dates(cfg, "fund_flow", date(2024, 6, 28))
    assert dates == [date(2024, 6, 26), date(2024, 6, 27), date(2024, 6, 28)]


def test_list_trading_dates_skips_weekends_without_calendar(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    dates = list_trading_dates(cfg, date(2024, 6, 28), date(2024, 6, 30))
    assert dates == [date(2024, 6, 28)]


def test_list_trading_dates_merges_nested_staging_calendar_fragments(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    root = cfg.staging_root / "trading_calendar" / "run_id=recovered" / ".fragments"
    root.mkdir(parents=True)
    base = {
        "source": "exchange_calendar",
        "data_version": "v1",
    }
    pl.DataFrame(
        [
            {
                **base,
                "trade_date": date(2024, 6, 24),
                "is_trading": True,
                "fetched_at": datetime(2024, 6, 1, tzinfo=timezone.utc),
            },
            {
                **base,
                "trade_date": date(2024, 6, 26),
                "is_trading": False,
                "fetched_at": datetime(2024, 6, 1, tzinfo=timezone.utc),
            },
        ]
    ).write_parquet(root / "part-0.parquet")
    pl.DataFrame(
        [
            {
                **base,
                "trade_date": date(2024, 6, 25),
                "is_trading": True,
                "fetched_at": datetime(2024, 6, 2, tzinfo=timezone.utc),
            },
            {
                **base,
                "trade_date": date(2024, 6, 26),
                "is_trading": True,
                "fetched_at": datetime(2024, 6, 2, tzinfo=timezone.utc),
            },
        ]
    ).write_parquet(root / "part-1.parquet")

    assert list_trading_dates(cfg, date(2024, 6, 24), date(2024, 6, 26)) == [
        date(2024, 6, 24),
        date(2024, 6, 25),
        date(2024, 6, 26),
    ]


def test_list_trading_dates_rebuilds_when_curated_calendar_is_partial(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    root = cfg.curated_root / "trading_calendar"
    root.mkdir(parents=True)
    # A partially promoted calendar must not make the missing sessions look
    # like holidays and silently narrow a backfill window.
    pl.DataFrame([{"trade_date": date(2024, 6, 26), "is_trading": True}]).write_parquet(
        root / "part-merged.parquet"
    )

    assert list_trading_dates(cfg, date(2024, 6, 24), date(2024, 6, 28)) == [
        date(2024, 6, 24),
        date(2024, 6, 25),
        date(2024, 6, 26),
        date(2024, 6, 27),
        date(2024, 6, 28),
    ]


def test_is_trading_day_prefers_latest_curated_calendar_fragment(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    root = cfg.curated_root / "trading_calendar"
    root.mkdir(parents=True)
    base = {"trade_date": date(2024, 6, 26), "source": "calendar", "data_version": "v1"}
    pl.DataFrame(
        [{**base, "is_trading": False, "fetched_at": datetime(2024, 6, 1, tzinfo=timezone.utc)}]
    ).write_parquet(root / "part-merged.parquet")
    nested = root / ".old-fragments"
    nested.mkdir()
    pl.DataFrame(
        [{**base, "is_trading": True, "fetched_at": datetime(2024, 6, 2, tzinfo=timezone.utc)}]
    ).write_parquet(nested / "part-old.parquet")

    assert is_trading_day(cfg, date(2024, 6, 26)) is True


def test_fetch_incremental_daily_loops_gap_days_for_by_date(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    _seed_trading_calendar(cfg, date(2024, 6, 24), date(2024, 6, 28))
    StateStore(cfg.meta_root).set_date("margin_trading", date(2024, 6, 25))
    fetched: list[date] = []

    def _fetch(day: date) -> pl.DataFrame:
        fetched.append(day)
        return pl.DataFrame({"trade_date": [day], "symbol": ["600519.SH"], "value": [1.0]})

    df, findings = fetch_incremental_daily(cfg, "margin_trading", date(2024, 6, 28), _fetch)
    assert fetched == [date(2024, 6, 26), date(2024, 6, 27), date(2024, 6, 28)]
    assert df.height == 3
    assert findings == []


def test_allowed_empty_response_is_reported_for_dense_dataset(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    _seed_trading_calendar(cfg, date(2024, 6, 24), date(2024, 6, 28))
    StateStore(cfg.meta_root).set_date("market_breadth", date(2024, 6, 25))

    def _fetch(day: date) -> pl.DataFrame:
        if day == date(2024, 6, 27):
            return pl.DataFrame()
        return pl.DataFrame({"trade_date": [day], "metric_id": ["advance_count"], "value": [1.0]})

    df, findings = fetch_incremental_daily(
        cfg,
        "market_breadth",
        date(2024, 6, 28),
        _fetch,
        allow_empty=True,
    )

    assert df.height == 2
    assert findings[0]["check"] == "session_dense_empty_days"
    assert findings[0]["sample_dates"] == ["2024-06-27"]


def test_fetch_incremental_daily_rejects_rows_from_a_different_trade_date(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    _seed_trading_calendar(cfg, date(2024, 6, 28), date(2024, 6, 28))

    with pytest.raises(RuntimeError, match="different or invalid trade_date"):
        fetch_incremental_daily(
            cfg,
            "margin_trading",
            date(2024, 6, 28),
            lambda d: pl.DataFrame(
                {
                    "trade_date": [date(2024, 6, 27)],
                    "symbol": ["600519.SH"],
                    "value": [1.0],
                }
            ),
        )


def test_fetch_incremental_daily_validates_explicit_non_trade_date_column(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    _seed_trading_calendar(cfg, date(2024, 6, 28), date(2024, 6, 28))

    with pytest.raises(RuntimeError, match="different or invalid publish_date"):
        fetch_incremental_daily(
            cfg,
            "news_headlines",
            date(2024, 6, 28),
            lambda d: pl.DataFrame(
                {
                    "publish_date": [date(2024, 6, 27)],
                    "news_id": ["n-1"],
                    "title": ["wrong day"],
                }
            ),
            date_col="publish_date",
        )


def test_fetch_incremental_daily_snapshot_only_fetches_run_day(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    _seed_trading_calendar(cfg, date(2024, 6, 24), date(2024, 6, 28))
    StateStore(cfg.meta_root).set_date("fund_flow", date(2024, 6, 25))
    fetched: list[date] = []

    def _fetch(day: date) -> pl.DataFrame:
        fetched.append(day)
        return pl.DataFrame({"trade_date": [day], "symbol": ["600519.SH"], "value": [1.0]})

    df, findings = fetch_incremental_daily(cfg, "fund_flow", date(2024, 6, 28), _fetch)
    assert fetched == [date(2024, 6, 28)]
    assert df.height == 1
    assert len(findings) == 1
    assert findings[0]["check"] == "coverage_gap"
    assert findings[0]["gap_dates"] == ["2024-06-26", "2024-06-27"]


def test_fetch_incremental_daily_snapshot_rejects_backfill(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    cfg._backfill = True

    with pytest.raises(SnapshotBackfillError, match="fund_flow"):
        fetch_incremental_daily(
            cfg,
            "fund_flow",
            date(2024, 6, 28),
            lambda d: pl.DataFrame(),
        )


def test_fetch_incremental_daily_backfill_rejects_empty_when_not_allowed(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    cfg._backfill = True

    with pytest.raises(RuntimeError, match="margin_trading: no rows returned"):
        fetch_incremental_daily(
            cfg,
            "margin_trading",
            date(2024, 6, 28),
            lambda d: pl.DataFrame(),
            allow_empty=False,
        )


def test_fetch_incremental_daily_backfill_rejects_wrong_trade_date(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    cfg._backfill = True

    with pytest.raises(RuntimeError, match="different or invalid trade_date"):
        fetch_incremental_daily(
            cfg,
            "margin_trading",
            date(2024, 6, 28),
            lambda d: pl.DataFrame(
                {
                    "trade_date": [date(2024, 6, 27)],
                    "symbol": ["600519.SH"],
                    "value": [1.0],
                }
            ),
        )


def test_run_incremental_fetched_rejects_universe_with_no_overlap(tmp_path, monkeypatch):
    cfg = Config(data_root=tmp_path / "data")
    source = pl.DataFrame(
        {
            "symbol": ["000001.SZ"],
            "trade_date": [date(2024, 6, 28)],
            "value": [1.0],
        }
    )
    monkeypatch.setattr(
        http_common,
        "fetch_incremental_daily",
        lambda *args, **kwargs: (source, []),
    )

    with pytest.raises(RuntimeError, match="none matched the reconciled universe"):
        http_common.run_incremental_fetched(
            cfg,
            date(2024, 6, 28),
            "run-no-overlap",
            "valuation_metrics",
            lambda d: source,
            source="eastmoney",
            universe={"600519.SH"},
        )


def test_step_fund_flow_snapshot_gap_only_fetches_run_day(tmp_path, monkeypatch):
    cfg = Config(data_root=tmp_path / "data")
    _seed_trading_calendar(cfg, date(2024, 6, 24), date(2024, 6, 28))
    StateStore(cfg.meta_root).set_date("fund_flow", date(2024, 6, 25))
    fetched: list[date] = []

    def fake_fetch(trade_date, **kwargs):
        fetched.append(trade_date)
        return pl.DataFrame(
            {
                "symbol": ["600519.SH"],
                "trade_date": [trade_date],
                "main_net_inflow": [1.0],
                "super_large_net_inflow": [0.0],
                "large_net_inflow": [0.0],
                "medium_net_inflow": [0.0],
                "small_net_inflow": [0.0],
            }
        )

    monkeypatch.setattr(cap, "fetch_fund_flow", fake_fetch)
    cfg.staging_root.mkdir(parents=True)
    result = cap.step_fund_flow(cfg, date(2024, 6, 28), "run-gap", {})
    assert fetched == [date(2024, 6, 28)]
    assert result["rows_written"] == 1
    findings = result["context_updates"]["audit_findings"]
    assert findings[0]["check"] == "coverage_gap"


def test_step_fund_flow_single_day_when_caught_up(tmp_path, monkeypatch):
    cfg = Config(data_root=tmp_path / "data")
    StateStore(cfg.meta_root).set_date("fund_flow", date(2024, 6, 27))
    fetched: list[date] = []

    def fake_fetch(trade_date, **kwargs):
        fetched.append(trade_date)
        return pl.DataFrame(
            {
                "symbol": ["600519.SH"],
                "trade_date": [trade_date],
                "main_net_inflow": [1.0],
                "super_large_net_inflow": [0.0],
                "large_net_inflow": [0.0],
                "medium_net_inflow": [0.0],
                "small_net_inflow": [0.0],
            }
        )

    monkeypatch.setattr(cap, "fetch_fund_flow", fake_fetch)
    cfg.staging_root.mkdir(parents=True)
    result = cap.step_fund_flow(cfg, date(2024, 6, 28), "run-1", {})
    assert fetched == [date(2024, 6, 28)]
    assert result["rows_written"] == 1
    assert "context_updates" not in result
