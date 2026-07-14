"""sector_bars hybrid backfill step — checkpoint resume and warning status."""

from __future__ import annotations

from datetime import date

import polars as pl

from stock_data_engine.config import Config
from stock_data_engine.derive.sector_routing import OHLC_EM, OHLC_TDX
from stock_data_engine.steps import rotation as rot
from stock_data_engine.steps.rotation import (
    _backfill_sector_bars,
    _sector_bars_completed,
    clear_sector_bars_backfill_state,
)


def _write_routing(cfg: Config) -> None:
    routing = pl.DataFrame(
        [
            {
                "sector_code": "BK1630",
                "sector_name": "A",
                "board_type": "concept",
                "ohlc_source": OHLC_EM,
                "tdx_code": None,
            },
            {
                "sector_code": "BK1631",
                "sector_name": "B",
                "board_type": "industry",
                "ohlc_source": OHLC_TDX,
                "tdx_code": "881001",
            },
        ]
    )
    path = cfg.meta_root / "sector_ohlc_routing.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    routing.write_parquet(path)


def _bar_row(sector_code: str) -> dict:
    return {
        "sector_code": sector_code,
        "sector_name": "X",
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


def _patch_hybrid(monkeypatch, *, em_returns, tdx_returns):
    em_df, em_failed, em_succeeded = em_returns
    tdx_df, tdx_failed, tdx_succeeded = tdx_returns

    def fake_em_history(start, end, *, config=None, skip_sectors=None, only_sectors=None):
        skip = skip_sectors or set()
        succeeded = [s for s in em_succeeded if s not in skip]
        if only_sectors is not None:
            succeeded = [s for s in succeeded if s in only_sectors]
        failed = [s for s in em_failed if s not in skip]
        if succeeded:
            part = em_df.filter(pl.col("sector_code").is_in(succeeded))
        else:
            part = pl.DataFrame()
        return part, failed, succeeded

    def fake_tdx_batch(routing, start, end, *, config, skip_sectors=None, backfill=False):
        skip = skip_sectors or set()
        succeeded = [s for s in tdx_succeeded if s not in skip]
        failed = [s for s in tdx_failed if s not in skip]
        if succeeded:
            part = tdx_df.filter(pl.col("sector_code").is_in(succeeded))
        else:
            part = pl.DataFrame()
        return part, failed, succeeded

    written: list[pl.DataFrame] = []

    def fake_write(config, run_id, dataset, df, *, source):
        written.append(df)
        return {"rows_read": df.height, "rows_written": df.height}

    monkeypatch.setattr(
        "stock_data_engine.adapters.eastmoney.rotation.fetch_sector_bars_history",
        fake_em_history,
    )
    monkeypatch.setattr(
        "stock_data_engine.adapters.tdx_protocol.sector_bars.fetch_sector_index_bars_batch",
        fake_tdx_batch,
    )
    monkeypatch.setattr(rot, "write_fetched", fake_write)
    return written


def test_marks_succeeded_boards_and_resumes(tmp_path, monkeypatch):
    cfg = Config(data_root=tmp_path / "data")
    _write_routing(cfg)
    em_df = pl.DataFrame([_bar_row("BK1630")])
    tdx_df = pl.DataFrame([_bar_row("BK1631")])
    _patch_hybrid(
        monkeypatch,
        em_returns=(em_df, [], ["BK1630"]),
        tdx_returns=(tdx_df, [], ["BK1631"]),
    )
    result = _backfill_sector_bars(cfg, date(2026, 7, 14), "run1")
    assert result["rows_written"] == 2
    assert _sector_bars_completed(cfg, "em") == {"BK1630"}
    assert _sector_bars_completed(cfg, "tdx") == {"BK1631"}

    captured: dict = {}

    def fake_em(start, end, *, config=None, skip_sectors=None, only_sectors=None):
        captured["em_skip"] = skip_sectors
        return pl.DataFrame(), [], []

    def fake_tdx(routing, start, end, *, config, skip_sectors=None, backfill=False):
        captured["tdx_skip"] = skip_sectors
        return pl.DataFrame(), [], []

    monkeypatch.setattr(
        "stock_data_engine.adapters.eastmoney.rotation.fetch_sector_bars_history",
        fake_em,
    )
    monkeypatch.setattr(
        "stock_data_engine.adapters.tdx_protocol.sector_bars.fetch_sector_index_bars_batch",
        fake_tdx,
    )
    again = _backfill_sector_bars(cfg, date(2026, 7, 14), "run2")
    assert "already sector_bars-backfilled" in again["note"]
    assert captured["em_skip"] == {"BK1630"}
    assert captured["tdx_skip"] == {"BK1631"}


def test_failed_boards_not_marked_and_emit_warning(tmp_path, monkeypatch):
    cfg = Config(data_root=tmp_path / "data")
    _write_routing(cfg)
    em_df = pl.DataFrame([_bar_row("BK1630")])
    _patch_hybrid(
        monkeypatch,
        em_returns=(em_df, ["BK1632"], ["BK1630"]),
        tdx_returns=(pl.DataFrame(), ["BK1633"], []),
    )
    result = _backfill_sector_bars(cfg, date(2026, 7, 14), "run1")

    assert _sector_bars_completed(cfg, "em") == {"BK1630"}
    assert _sector_bars_completed(cfg, "tdx") == set()
    assert result["failed_sectors"] == 2
    assert result["status"] == "warning"
    finding = result["context_updates"]["audit_findings"][0]
    assert finding["code"] == "sector_bars_backfill_incomplete"


def test_force_clears_checkpoint(tmp_path, monkeypatch):
    cfg = Config(data_root=tmp_path / "data")
    cfg._sector_bars_force = True
    _write_routing(cfg)
    em_df = pl.DataFrame([_bar_row("BK1630")])
    _patch_hybrid(
        monkeypatch,
        em_returns=(em_df, [], ["BK1630"]),
        tdx_returns=(pl.DataFrame(), [], []),
    )
    _backfill_sector_bars(cfg, date(2026, 7, 14), "run1")
    assert _sector_bars_completed(cfg, "em") == {"BK1630"}

    clear_sector_bars_backfill_state(cfg)
    assert _sector_bars_completed(cfg, "em") == set()
    assert _sector_bars_completed(cfg, "tdx") == set()


def test_requires_routing_table(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    try:
        _backfill_sector_bars(cfg, date(2026, 7, 14), "run1")
    except RuntimeError as exc:
        assert "sector_routing" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_migrates_legacy_em_checkpoint(tmp_path, monkeypatch):
    cfg = Config(data_root=tmp_path / "data")
    _write_routing(cfg)
    legacy = cfg.meta_root / "state" / "sector_bars_backfill.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text('{"completed": ["BK1630"]}', encoding="utf-8")
    assert _sector_bars_completed(cfg, "em") == {"BK1630"}

    captured: dict = {}

    def fake_em(start, end, *, config=None, skip_sectors=None, only_sectors=None):
        captured["skip"] = skip_sectors
        return pl.DataFrame(), [], []

    monkeypatch.setattr(
        "stock_data_engine.adapters.eastmoney.rotation.fetch_sector_bars_history",
        fake_em,
    )
    monkeypatch.setattr(
        "stock_data_engine.adapters.tdx_protocol.sector_bars.fetch_sector_index_bars_batch",
        lambda *a, **k: (pl.DataFrame(), [], []),
    )
    _backfill_sector_bars(cfg, date(2026, 7, 14), "run1")
    assert captured["skip"] == {"BK1630"}
