"""Resume + orchestration for the trading_status ST backfill step (C4).

The baostock fetch and the curated write are stubbed so the test isolates the
step's own logic: the todo set, the swept-symbol resume marker, and the
fail-loud finding on dropped symbols.
"""

from __future__ import annotations

from datetime import date

import polars as pl

from stock_data_engine.config import Config
from stock_data_engine.steps import reference
from stock_data_engine.steps.reference import (
    _backfill_trading_status_st,
    _st_backfilled_symbols,
)


def _write_instruments(config: Config, symbols: list[str]) -> None:
    root = config.curated_root / "instruments"
    root.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"symbol": symbols}).write_parquet(root / "part-merged.parquet")


def _st_row(symbol: str, d: date) -> dict:
    return {"symbol": symbol, "trade_date": d, "is_trading": True, "status": "st"}


def _patch(monkeypatch, *, returns):
    """Stub the network fetch and the curated write; return a captured-writes list."""
    written: list[pl.DataFrame] = []

    def fake_fetch(symbols, start, end, **kwargs):
        df, failed = returns
        return df, failed

    def fake_write(config, run_id, dataset, df, *, source):
        written.append(df)
        return {"rows_read": df.height, "rows_written": df.height}

    monkeypatch.setattr(
        "stock_data_engine.adapters.baostock.st_history.fetch_st_history", fake_fetch
    )
    monkeypatch.setattr(reference, "write_fetched", fake_write)
    return written


def test_writes_st_rows_and_marks_all_swept_symbols(tmp_path, monkeypatch):
    cfg = Config(data_root=tmp_path / "data")
    _write_instruments(cfg, ["600000.SH", "600001.SH"])  # both all_a, no ST for 600001
    df = pl.DataFrame([_st_row("600000.SH", date(2020, 5, 6))])
    written = _patch(monkeypatch, returns=(df, []))

    result = _backfill_trading_status_st(cfg, date(2026, 7, 1), "run1")

    assert result["rows_written"] == 1
    assert written[0]["symbol"].to_list() == ["600000.SH"]
    # every swept symbol is marked done — including the one that was never ST
    assert _st_backfilled_symbols(cfg) == {"600000.SH", "600001.SH"}


def test_resume_skips_already_swept_symbols(tmp_path, monkeypatch):
    cfg = Config(data_root=tmp_path / "data")
    _write_instruments(cfg, ["600000.SH", "600001.SH"])
    _patch(monkeypatch, returns=(pl.DataFrame([_st_row("600000.SH", date(2020, 5, 6))]), []))
    _backfill_trading_status_st(cfg, date(2026, 7, 1), "run1")

    # Second run: nothing left to do.
    captured: dict = {}

    def fake_fetch(symbols, start, end, **kwargs):
        captured["symbols"] = symbols
        return pl.DataFrame(schema={"symbol": pl.Utf8}), []

    monkeypatch.setattr(
        "stock_data_engine.adapters.baostock.st_history.fetch_st_history", fake_fetch
    )
    result = _backfill_trading_status_st(cfg, date(2026, 7, 1), "run2")
    assert "already ST-backfilled" in result["note"]
    assert "symbols" not in captured  # fetch not even called


def test_failed_symbols_are_not_marked_and_surface_a_finding(tmp_path, monkeypatch):
    cfg = Config(data_root=tmp_path / "data")
    _write_instruments(cfg, ["600000.SH", "600001.SH"])
    df = pl.DataFrame([_st_row("600000.SH", date(2020, 5, 6))])
    _patch(monkeypatch, returns=(df, ["600001.SH"]))  # 600001 dropped by throttling

    result = _backfill_trading_status_st(cfg, date(2026, 7, 1), "run1")

    # only the succeeded symbol is marked; the dropped one stays todo for resume
    assert _st_backfilled_symbols(cfg) == {"600000.SH"}
    assert result["failed_symbols"] == 1
    finding = result["context_updates"]["audit_findings"][0]
    assert finding["code"] == "baostock_st_backfill_incomplete"
    assert finding["severity"] == "warning"
