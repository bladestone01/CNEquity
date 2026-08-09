"""_backfill_share_unlock_schedule — strided walk, not daily.

Unlike the other by-date snapshots, share_unlock_schedule's PK is
(symbol, unlock_date) with no as-of/snapshot column: one call returns every
unlock in the next 180 days from its date, so the same event would be
re-fetched up to ~180 times by a daily walk before aging out of the window.
The backfill strides under the horizon instead.
"""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from ashare_lake.config import Config
from ashare_lake.steps.macro_risk import (
    _UNLOCK_STRIDE_DAYS,
    _backfill_share_unlock_schedule,
)


def _row(unlock_date: date, symbol: str = "600000.SH") -> dict:
    return {
        "symbol": symbol,
        "unlock_date": unlock_date,
        "unlock_shares": 1.0,
        "unlock_ratio": 0.01,
        "unlock_type": "限售股份",
    }


def test_strides_under_the_horizon_not_every_day(tmp_path, monkeypatch):
    cfg = Config(data_root=tmp_path / "data")
    cfg._backfill_start = date(2026, 1, 1)
    cfg._backfill_end = date(2026, 6, 30)  # ~181 days: exactly one full stride
    calls: list[date] = []

    def fake_fetch(d: date, *, horizon_days: int = 180) -> pl.DataFrame:
        calls.append(d)
        return pl.DataFrame([_row(d + timedelta(days=30))])

    monkeypatch.setattr("ashare_lake.steps.macro_risk.fetch_share_unlock_schedule", fake_fetch)
    monkeypatch.setattr(cfg, "rate_limit", lambda source: None)

    _backfill_share_unlock_schedule(cfg, date(2026, 7, 1), "run-1")

    # 181-day window at a 150-day stride: calls at day 0 and day 150, not 181
    # separate daily calls.
    assert calls == [date(2026, 1, 1), date(2026, 1, 1) + timedelta(days=_UNLOCK_STRIDE_DAYS)]


def test_writes_a_single_deduped_batch(tmp_path, monkeypatch):
    cfg = Config(data_root=tmp_path / "data")
    cfg._backfill_start = date(2026, 1, 1)
    cfg._backfill_end = date(2026, 6, 30)

    # Overlapping strides both see the same unlock event — the PK (symbol,
    # unlock_date) is what collapses it at compact, not this step.
    shared_unlock = date(2026, 3, 1)

    def fake_fetch(d: date, *, horizon_days: int = 180) -> pl.DataFrame:
        return pl.DataFrame([_row(shared_unlock)])

    monkeypatch.setattr("ashare_lake.steps.macro_risk.fetch_share_unlock_schedule", fake_fetch)
    monkeypatch.setattr(cfg, "rate_limit", lambda source: None)

    out = _backfill_share_unlock_schedule(cfg, date(2026, 7, 1), "run-1")

    staged = list((cfg.staging_root / "share_unlock_schedule").glob("**/*.parquet"))
    assert len(staged) == 1
    assert out["rows_written"] == pl.read_parquet(staged[0]).height


def test_empty_range_writes_nothing(tmp_path, monkeypatch):
    cfg = Config(data_root=tmp_path / "data")
    cfg._backfill_start = date(2026, 1, 1)
    cfg._backfill_end = date(2026, 1, 2)  # under one stride — a single call

    monkeypatch.setattr(
        "ashare_lake.steps.macro_risk.fetch_share_unlock_schedule",
        lambda d, **k: pl.DataFrame(),
    )
    monkeypatch.setattr(cfg, "rate_limit", lambda source: None)

    out = _backfill_share_unlock_schedule(cfg, date(2026, 7, 1), "run-1")

    assert out == {"rows_read": 0, "rows_written": 0}
    assert not (cfg.staging_root / "share_unlock_schedule").exists()
