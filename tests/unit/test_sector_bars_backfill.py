"""sector_bars backfill step — checkpoint resume and warning status."""

from __future__ import annotations

from datetime import date

import polars as pl

from stock_data_engine.config import Config
from stock_data_engine.steps import rotation as rot
from stock_data_engine.steps.rotation import (
    _backfill_sector_bars,
    _sector_bars_completed,
    clear_sector_bars_backfill_state,
)


def _patch_history(monkeypatch, *, returns):
    def fake_history(start, end, *, config=None, skip_sectors=None, only_sectors=None):
        df, failed, succeeded = returns
        skip = skip_sectors or set()
        succeeded = [s for s in succeeded if s not in skip]
        failed = [s for s in failed if s not in skip]
        if succeeded:
            part = df.filter(pl.col("sector_code").is_in(succeeded))
        else:
            part = pl.DataFrame()
        return part, failed, succeeded

    written: list[pl.DataFrame] = []

    def fake_write(config, run_id, dataset, df, *, source):
        written.append(df)
        return {"rows_read": df.height, "rows_written": df.height}

    monkeypatch.setattr(
        "stock_data_engine.adapters.eastmoney.rotation.fetch_sector_bars_history",
        fake_history,
    )
    monkeypatch.setattr(rot, "write_fetched", fake_write)
    return written


def test_marks_succeeded_boards_and_resumes(tmp_path, monkeypatch):
    cfg = Config(data_root=tmp_path / "data")
    df = pl.DataFrame(
        [
            {
                "sector_code": "BK1630",
                "sector_name": "A",
                "board_type": "concept",
                "trade_date": date(2026, 7, 10),
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1,
                "amount": 1.0,
                "change_pct": 0.1,
            }
        ]
    )
    _patch_history(monkeypatch, returns=(df, [], ["BK1630"]))
    result = _backfill_sector_bars(cfg, date(2026, 7, 14), "run1")
    assert result["rows_written"] == 1
    assert _sector_bars_completed(cfg) == {"BK1630"}

    captured: dict = {}

    def fake_history(start, end, *, config=None, skip_sectors=None, only_sectors=None):
        captured["skip"] = skip_sectors
        return pl.DataFrame(), [], []

    monkeypatch.setattr(
        "stock_data_engine.adapters.eastmoney.rotation.fetch_sector_bars_history",
        fake_history,
    )
    again = _backfill_sector_bars(cfg, date(2026, 7, 14), "run2")
    assert "already sector_bars-backfilled" in again["note"]
    assert captured["skip"] == {"BK1630"}


def test_failed_boards_not_marked_and_emit_warning(tmp_path, monkeypatch):
    cfg = Config(data_root=tmp_path / "data")
    df = pl.DataFrame(
        [
            {
                "sector_code": "BK1630",
                "sector_name": "A",
                "board_type": "concept",
                "trade_date": date(2026, 7, 10),
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1,
                "amount": 1.0,
                "change_pct": 0.1,
            }
        ]
    )
    _patch_history(monkeypatch, returns=(df, ["BK1631", "BK1632"], ["BK1630"]))
    result = _backfill_sector_bars(cfg, date(2026, 7, 14), "run1")

    assert _sector_bars_completed(cfg) == {"BK1630"}
    assert result["failed_sectors"] == 2
    assert result["status"] == "warning"
    finding = result["context_updates"]["audit_findings"][0]
    assert finding["code"] == "sector_bars_backfill_incomplete"


def test_force_clears_checkpoint(tmp_path, monkeypatch):
    cfg = Config(data_root=tmp_path / "data")
    cfg._sector_bars_force = True
    df = pl.DataFrame(
        [
            {
                "sector_code": "BK1630",
                "sector_name": "A",
                "board_type": "concept",
                "trade_date": date(2026, 7, 10),
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1,
                "amount": 1.0,
                "change_pct": 0.1,
            }
        ]
    )
    _patch_history(monkeypatch, returns=(df, [], ["BK1630"]))
    _backfill_sector_bars(cfg, date(2026, 7, 14), "run1")
    assert _sector_bars_completed(cfg) == {"BK1630"}

    clear_sector_bars_backfill_state(cfg)
    assert _sector_bars_completed(cfg) == set()
