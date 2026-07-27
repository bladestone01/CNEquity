"""Offline coverage for bars planning helpers."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import polars as pl

from ashare_lake.steps import bars


def test_backfill_window_defaults_and_overrides(tmp_path):
    cfg = SimpleNamespace(_backfill_start=None, _backfill_end=None)
    start, end = bars._backfill_window(cfg, date(2025, 1, 10))
    assert end == date(2025, 1, 10)
    assert start.year <= 2016

    cfg2 = SimpleNamespace(_backfill_start=date(2024, 1, 1), _backfill_end=date(2024, 6, 1))
    assert bars._backfill_window(cfg2, date(2025, 1, 10)) == (date(2024, 1, 1), date(2024, 6, 1))


def test_history_plan_filters_etf_and_future_listings(tmp_path, monkeypatch):
    curated = tmp_path / "curated"
    inst = curated / "instruments" / "year=2025"
    inst.mkdir(parents=True)
    pl.DataFrame(
        [
            {"symbol": "600519.SH", "list_date": date(2001, 8, 27), "asset_type": "stock"},
            {"symbol": "510300.SH", "list_date": date(2012, 5, 28), "asset_type": "etf"},
            {"symbol": "688001.SH", "list_date": date(2024, 6, 1), "asset_type": "stock"},
            {"symbol": "301001.SZ", "list_date": date(2026, 1, 1), "asset_type": "stock"},
            {"symbol": "920001.BJ", "list_date": date(2020, 1, 1), "asset_type": "stock"},
        ]
    ).write_parquet(inst / "part.parquet")

    cfg = SimpleNamespace(curated_root=curated)
    monkeypatch.setattr(
        bars,
        "load_symbols",
        lambda config: ["600519.SH", "510300.SH", "688001.SH", "301001.SZ", "920001.BJ"],
    )
    plan = bars._history_plan(cfg, date(2020, 1, 1), date(2025, 1, 1))
    by_sym = dict(plan)
    assert "600519.SH" in by_sym
    assert by_sym["600519.SH"] == date(2020, 1, 1)
    assert "688001.SH" in by_sym
    assert by_sym["688001.SH"] == date(2024, 1, 1)  # listing year Jan 1
    assert "510300.SH" not in by_sym  # etf
    assert "301001.SZ" not in by_sym  # listed after window
    assert "920001.BJ" not in by_sym  # BJ prefix filtered


def test_history_plan_without_instruments_falls_back(tmp_path, monkeypatch):
    cfg = SimpleNamespace(curated_root=tmp_path / "missing")
    monkeypatch.setattr(bars, "load_symbols", lambda config: ["600519.SH", "920001.BJ"])
    plan = bars._history_plan(cfg, date(2020, 1, 1), date(2025, 1, 1))
    assert plan == [("600519.SH", date(2020, 1, 1))]


def test_sweep_stock_bars_planned_abort_streak(monkeypatch):
    plan = [(f"{i:06d}.SZ", date(2024, 1, 1)) for i in range(12)]
    calls = {"n": 0}

    def boom(symbol, start, end, *, config=None):
        calls["n"] += 1
        raise RuntimeError("down")

    monkeypatch.setattr("ashare_lake.adapters.ths.stock_bars.fetch_stock_bars", boom)
    batches = []
    failed = bars.sweep_stock_bars_planned(
        plan,
        date(2024, 1, 10),
        config=SimpleNamespace(),
        on_batch=lambda rows, codes: batches.append(list(codes)),
        batch_size=50,
    )
    assert len(failed) == 10  # aborts after 10 consecutive
    assert calls["n"] == 10
