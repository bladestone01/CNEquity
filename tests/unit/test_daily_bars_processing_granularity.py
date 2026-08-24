"""daily_bars processing granularity: symbol vs batch, persisted-evidence exemptions.

Covers the ``daily-bars-processing-granularity`` capability:
- config-only switch (no ``--granularity`` CLI), validation
- symbol-mode partial staging + failure scope vs batch-mode strict all-or-nothing
- persisted-evidence-only exemptions (first-trading-day IPO, whole-window suspension)
- D1 stock universe filter applies regardless of granularity
- retry scope narrowing from ``failed_scope_json``
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from cnequity.config import load_config
from cnequity.config.bootstrap import path_for_toml
from cnequity.orchestrator.engine import JobEngine
from cnequity.orchestrator.worker_pool import fetch_daily_bars_parallel
from cnequity.steps import bars as bars_step
from cnequity.storage.atomic import write_parquet_atomic
from cnequity.storage.layout import init_data_layout


def _config(tmp_path: Path, granularity: str = "symbol") -> object:
    cfg_path = tmp_path / "test.toml"
    cfg_path.write_text(
        f"""
[data]
root = "{path_for_toml(tmp_path / "data")}"

[orchestrator]
workers = 1
batch_size = 100
daily_bars_granularity = "{granularity}"

[tdx_protocol]
allow_mock = true
"""
    )
    return load_config(cfg_path)


def _bars_df(symbols, start: date):
    return pl.DataFrame(
        {
            "symbol": symbols,
            "trade_date": [start] * len(symbols),
            "open": [1.0] * len(symbols),
            "high": [2.0] * len(symbols),
            "low": [0.5] * len(symbols),
            "close": [1.2] * len(symbols),
            "volume": [100_000] * len(symbols),
            "amount": [100_000.0] * len(symbols),
        }
    )


def _write_curated_daily_bars(cfg, rows: pl.DataFrame):
    root = cfg.curated_root / "daily_bars"
    for symbol, trade_date in zip(
        rows["symbol"].to_list(), rows["trade_date"].to_list(), strict=True
    ):
        key = _partition_key("trade_date", trade_date)
        out_dir = root / f"trade_date={key}"
        out_dir.mkdir(parents=True, exist_ok=True)
        write_parquet_atomic(
            out_dir / "part-merged.parquet",
            rows.filter((pl.col("symbol") == symbol) & (pl.col("trade_date") == trade_date)),
            compression="zstd",
        )


def _partition_key(col: str, value):
    if col == "trade_date":
        return value.isoformat()
    return str(value)


def _write_curated_trading_status(cfg, rows: pl.DataFrame):
    root = cfg.curated_root / "trading_status"
    for symbol, trade_date, status in zip(
        rows["symbol"].to_list(),
        rows["trade_date"].to_list(),
        rows["status"].to_list(),
        strict=True,
    ):
        out_dir = root / f"trade_date={_partition_key('trade_date', trade_date)}"
        out_dir.mkdir(parents=True, exist_ok=True)
        write_parquet_atomic(
            out_dir / "part-merged.parquet",
            pl.DataFrame({"symbol": [symbol], "trade_date": [trade_date], "status": [status]}),
            compression="zstd",
        )


def _write_curated_instruments(cfg, symbols, *, list_dates=None):
    stock = {"600519.SH": "stock", "562110.SH": "etf", "301655.SZ": "stock", "920001.BJ": "stock"}
    rows = []
    for sym in symbols:
        rows.append(
            {
                "symbol": sym,
                "name": f"n-{sym}",
                "exchange": sym.split(".")[1],
                "asset_type": stock.get(sym, "stock"),
                "list_date": list_dates.get(sym) if list_dates else None,
                "delist_date": None,
            }
        )
    out_dir = cfg.curated_root / "instruments"
    out_dir.mkdir(parents=True, exist_ok=True)
    write_parquet_atomic(
        out_dir / "part-merged.parquet",
        pl.DataFrame(rows),
        compression="zstd",
    )


# --- config-only switch --------------------------------------------------


def test_config_only_no_cli_granularity():
    from click.testing import CliRunner

    from cnequity.cli.main import cli

    for args in (
        ["run", "daily", "--help"],
        ["backfill", "daily_bars", "--help"],
        ["retry", "--help"],
    ):
        result = CliRunner().invoke(cli, args)
        assert result.exit_code == 0
        assert "granularity" not in (result.output or "").lower()


def test_granularity_config_validation(tmp_path):
    for value, ok in (("symbol", True), ("batch", True), ("row", False)):
        cfg = _config(tmp_path, value if ok else "symbol")
        if ok:
            cfg.daily_bars_granularity = value
        else:
            cfg.daily_bars_granularity = "row"
        from cnequity.config import validate_config

        errors = [e for e in validate_config(cfg) if "daily_bars_granularity" in e]
        if ok:
            assert not errors, errors
        else:
            assert errors, "expected a validation error for an unknown granularity"


# --- symbol vs batch dual path -------------------------------------------


def test_symbol_mode_stages_partial_and_records_failed_scope(tmp_path, monkeypatch):
    cfg = _config(tmp_path, "symbol")
    init_data_layout(cfg)
    from cnequity.orchestrator import worker_pool

    start = date(2026, 8, 20)
    end = date(2026, 8, 21)
    good = ["600519.SH", "000001.SZ"]
    failed = ["301655.SZ"]

    def _tolerant(symbols, start, end, **kwargs):
        # emulate a per-symbol fetch that returns two symbols and fails the third
        _require_all = set(symbols)
        _good = [s for s in symbols if s in good]
        return _bars_df(_good, start), [s for s in symbols if s in failed]

    monkeypatch.setattr(worker_pool, "fetch_daily_bars_tolerant", _tolerant)
    run_id = worker_pool.Manifest(cfg.manifest_path).start_run("t")
    result = fetch_daily_bars_parallel(
        cfg,
        good + failed,
        start,
        end,
        run_id,
        "daily_bars",
        batch_specs=[
            ("b0", ["600519.SH", "000001.SZ", "301655.SZ"], start, end),
        ],
    )
    assert result["had_error"] is True
    assert result["failed_symbols"] == failed
    staged = worker_pool.StagingWriter(cfg.staging_root).list_run_files("daily_bars", run_id)
    frame = pl.concat([pl.read_parquet(f) for f in staged])
    assert set(frame["symbol"].unique().to_list()) == set(good)
    m = worker_pool.Manifest(cfg.manifest_path)
    batch = m.get_batch(run_id, "b0")
    assert batch["status"] == "failed"
    scope = m.get_failed_scope(run_id, "b0")
    assert {s["symbol"] for s in scope} == set(failed)


def test_batch_mode_whole_batch_fails_on_any_missing(tmp_path, monkeypatch):
    cfg = _config(tmp_path, "batch")
    init_data_layout(cfg)
    from cnequity.orchestrator import worker_pool

    start = date(2026, 8, 20)
    end = date(2026, 8, 21)

    def _boom(symbols, start, end, **kwargs):
        raise worker_pool.fetch_daily_bars_tolerant.__class__(RuntimeError("source down"))

    monkeypatch.setattr(worker_pool, "fetch_daily_bars", _boom)
    run_id = worker_pool.Manifest(cfg.manifest_path).start_run("t")
    result = fetch_daily_bars_parallel(
        cfg, ["600519.SH", "000001.SZ"], start, end, run_id, "daily_bars"
    )
    assert result["had_error"] is True
    assert len(result["failed_symbols"]) == 2
    assert worker_pool.StagingWriter(cfg.staging_root).list_run_files("daily_bars", run_id) == []


# --- D1 stock filter is unconditional ------------------------------------


def test_stock_filter_applies_in_both_granularities(tmp_path):
    cfg = _config(tmp_path, "batch")
    init_data_layout(cfg)
    _write_curated_instruments(cfg, ["600519.SH", "562110.SH", "920001.BJ"])
    out = bars_step._stock_only_symbols(cfg, ["600519.SH", "562110.SH", "920001.BJ"])
    assert "562110.SH" not in out  # etf
    assert "600519.SH" in out and "920001.BJ" in out  # stock (incl BJ)
    cfg = _config(tmp_path, "symbol")
    out = bars_step._stock_only_symbols(cfg, ["600519.SH", "562110.SH"])
    assert out == ["600519.SH"]


# --- persisted-evidence exemptions ---------------------------------------


def test_first_trading_day_exempt_when_no_bar(tmp_path):
    cfg = _config(tmp_path, "symbol")
    init_data_layout(cfg)
    end = date(2026, 8, 21)
    _write_curated_instruments(cfg, ["301655.SZ"], list_dates={"301655.SZ": end})
    exempt, findings = bars_step._persisted_exemptions(cfg, ["301655.SZ"], date(2026, 8, 20), end)
    assert exempt == ["301655.SZ"]
    assert any(f["check"] == "daily_bars_persisted_exemptions" for f in findings)


def test_first_trading_day_not_exempt_with_bar(tmp_path):
    cfg = _config(tmp_path, "symbol")
    init_data_layout(cfg)
    end = date(2026, 8, 21)
    _write_curated_instruments(cfg, ["301655.SZ"], list_dates={"301655.SZ": end})
    _write_curated_daily_bars(cfg, _bars_df(["301655.SZ"], end))
    exempt, _ = bars_step._persisted_exemptions(cfg, ["301655.SZ"], date(2026, 8, 20), end)
    assert exempt == []


def test_suspension_exempt_via_trading_status(tmp_path):
    cfg = _config(tmp_path, "symbol")
    init_data_layout(cfg)
    start, end = date(2026, 8, 20), date(2026, 8, 21)
    ts = pl.DataFrame(
        {
            "symbol": ["000001.SZ"] * 2,
            "trade_date": [start, end],
            "status": ["suspended", "suspended"],
        }
    )
    _write_curated_trading_status(cfg, ts)
    exempt, _ = bars_step._persisted_exemptions(cfg, ["000001.SZ"], start, end)
    assert exempt == ["000001.SZ"]


def _zero_placeholder_row(d: date) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["000001.SZ"],
            "trade_date": [d],
            "open": [1.0],
            "high": [1.0],
            "low": [1.0],
            "close": [1.0],
            "volume": [0],
            "amount": [0.0],
        }
    )


def test_suspension_exempt_via_placeholder_run(tmp_path):
    cfg = _config(tmp_path, "symbol")
    init_data_layout(cfg)
    start, end = date(2026, 8, 20), date(2026, 8, 21)
    _write_curated_daily_bars(cfg, _bars_df(["000001.SZ"], date(2025, 1, 1)))
    from cnequity.steps.delisted import _ORPHAN_ACTIVE_PLACEHOLDER_MIN_ROWS

    for i in range(_ORPHAN_ACTIVE_PLACEHOLDER_MIN_ROWS):
        _write_curated_daily_bars(cfg, _zero_placeholder_row(start + timedelta(days=i)))
    exempt, _ = bars_step._persisted_exemptions(cfg, ["000001.SZ"], start, end)
    assert exempt == ["000001.SZ"]


def test_suspension_short_placeholder_run_not_exempt(tmp_path):
    cfg = _config(tmp_path, "symbol")
    init_data_layout(cfg)
    start, end = date(2026, 8, 20), date(2026, 8, 21)
    _write_curated_daily_bars(cfg, _bars_df(["000001.SZ"], date(2025, 1, 1)))
    for i in range(2):
        _write_curated_daily_bars(cfg, _zero_placeholder_row(start + timedelta(days=i)))
    exempt, _ = bars_step._persisted_exemptions(cfg, ["000001.SZ"], start, end)
    assert exempt == []


def test_exemption_classification_is_offline(tmp_path, monkeypatch):
    """Persistence-only constraint: no network client may be reached."""
    cfg = _config(tmp_path, "symbol")
    init_data_layout(cfg)
    end = date(2026, 8, 21)
    _write_curated_instruments(cfg, ["301655.SZ"], list_dates={"301655.SZ": end})
    for name in ("cnequity.adapters.tdx_protocol.client._quotes_client",):
        monkeypatch.setattr(
            name,
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("live vendor called")),
            raising=False,
        )
    exempt, _ = bars_step._persisted_exemptions(cfg, ["301655.SZ"], date(2026, 8, 20), end)
    assert exempt == ["301655.SZ"]


def test_classify_with_exemptions_moves_exempt_to_expected_no_data(tmp_path):
    cfg = _config(tmp_path, "symbol")
    init_data_layout(cfg)
    end = date(2026, 8, 21)
    _write_curated_instruments(
        cfg,
        ["600519.SH", "301655.SZ"],
        list_dates={"301655.SZ": end, "600519.SH": date(2010, 1, 1)},
    )
    spans = bars_step._instrument_spans(cfg)
    ownership, findings = bars_step._classify_with_exemptions(
        cfg, ["600519.SH", "301655.SZ"], spans, date(2026, 8, 20), end
    )
    assert "301655.SZ" in ownership.expected_no_data
    assert "301655.SZ" not in ownership.generic
    assert "600519.SH" in ownership.generic


# --- retry scope wiring --------------------------------------------------


def test_attempt_batch_id_counts_and_supersedes_family(tmp_path):
    from cnequity.orchestrator.manifest import Manifest

    cfg = _config(tmp_path, "symbol")
    init_data_layout(cfg)
    manifest = Manifest(cfg.manifest_path)
    run_id = manifest.start_run("t")
    manifest.start_batch(run_id, "b0", "daily_bars", "daily_bars", symbols=["s1"])
    manifest.finish_batch(run_id, "b0", "failed", error_message="gap")
    # first attempt id is attempt-1
    a1 = bars_step._attempt_batch_id(cfg, run_id, "b0")
    assert a1 == "b0-attempt-1"
    manifest.start_batch(run_id, a1, "daily_bars", "daily_bars", symbols=["s1"])
    manifest.finish_batch(run_id, a1, "failed", error_message="again")
    # second attempt sees the existing attempt-1 and increments
    a2 = bars_step._attempt_batch_id(cfg, run_id, "b0")
    assert a2 == "b0-attempt-2"
    manifest.start_batch(run_id, a2, "daily_bars", "daily_bars", symbols=["s1"])
    manifest.finish_batch(run_id, a2, "success", rows_written=1)
    assert bars_step._supersede_resolved_attempts(cfg, run_id, {a2: "b0"}) is None
    statuses = {r["batch_id"]: r["status"] for r in manifest.get_batches_for_run(run_id)}
    assert statuses["b0"] == "superseded"
    assert statuses[a1] == "superseded"
    assert statuses[a2] == "success"
    # supersede cleared the family so the compact gate no longer counts them
    assert manifest.incomplete_batch_counts_by_dataset(run_id) == {}


def test_retry_restores_recorded_granularity(tmp_path):
    from cnequity.orchestrator.manifest import Manifest

    cfg = _config(tmp_path, "batch")
    init_data_layout(cfg)
    manifest = Manifest(cfg.manifest_path)
    run_id = manifest.start_run("daily")
    manifest.start_batch(run_id, "b0", "daily_bars", "daily_bars", symbols=["s1"])
    manifest.finish_batch(run_id, "b0", "success", rows_written=1)
    # record the batch-mode choice the run started under (as run_job would)
    manifest.update_run_metadata(run_id, {"daily_bars_granularity": "batch"})
    # current config says symbol; the retry must restore the recorded batch mode
    # before it decides retry scope. A run with no retryable batches short-circuits
    # right after the restore, so no step/network runs here.
    cfg.daily_bars_granularity = "symbol"
    engine = JobEngine(cfg)
    engine._retry_run_locked(run_id, date(2026, 8, 21), auto_finalize=False)
    assert cfg.daily_bars_granularity == "batch"


def _failed_batch_run(tmp_path, granularity="symbol"):
    """Set up a run with one failed daily_bars batch (failed_scope recorded)."""
    from cnequity.orchestrator.manifest import Manifest

    cfg = _config(tmp_path, granularity)
    init_data_layout(cfg)
    manifest = Manifest(cfg.manifest_path)
    run_id = manifest.start_run("daily")
    manifest.start_batch(
        run_id,
        "b0",
        "daily_bars",
        "daily_bars",
        symbols=["600519.SH"],
        window_start="2026-08-20",
        window_end="2026-08-21",
    )
    manifest.set_failed_scope(run_id, "b0", [{"symbol": "301655.SZ"}])
    manifest.finish_batch(run_id, "b0", "failed", error_message="1 symbol(s) in failure scope")
    return cfg, run_id


def _noop_gapfill(*a, **k):
    return {
        "rows_read": 0,
        "rows_written": 0,
        "filled": False,
        "unresolved_symbols": ["301655.SZ"],
        "audit_findings": [],
    }


def test_finish_daily_bars_reports_failed_status_with_payload(tmp_path, monkeypatch, caplog):
    cfg, run_id = _failed_batch_run(tmp_path)
    monkeypatch.setattr(bars_step, "_gapfill_multiday_via_kline", _noop_gapfill)
    monkeypatch.setattr(bars_step, "_reject_preopen_placeholder", lambda *a, **k: None)

    with caplog.at_level("ERROR", logger="cnequity.steps.bars"):
        result = bars_step._finish_daily_bars(
            cfg,
            date(2026, 8, 21),
            run_id,
            start=date(2026, 8, 20),
            end=date(2026, 8, 21),
            expected_tdx_symbols=["301655.SZ", "600519.SH"],
            tdx_result={
                "rows_read": 0,
                "rows_written": 0,
                "had_error": True,
                "failed_symbols": ["301655.SZ"],
            },
            sina_result=None,
        )
    assert result["status"] == "failed"
    assert result["unresolved_symbols"] == ["301655.SZ"]
    assert result["missing_keys"] == 1
    payload = result["failed_batches"]
    assert payload[0]["batch_id"] == "b0"
    assert payload[0]["symbol_count"] == 1
    assert "301655.SZ" in payload[0]["sample_symbols"]

    # 2.4 — actionable ERROR, no traceback
    messages = [r.getMessage() for r in caplog.records]
    assert any("cne retry --run-id" in m for m in messages)
    assert all("Traceback (most recent call last)" not in m for m in messages)

    # 2.5 — the unresolved batch stays manifest 'failed' → compact gate blocks
    from cnequity.orchestrator.manifest import Manifest

    manifest = Manifest(cfg.manifest_path)
    assert manifest.get_batch(run_id, "b0")["status"] == "failed"
    assert manifest.incomplete_batch_counts_by_dataset(run_id) == {"daily_bars": 1}


def test_finish_daily_bars_genuine_bug_still_raises(tmp_path, monkeypatch):
    cfg, run_id = _failed_batch_run(tmp_path)

    def _boom(*a, **k):
        raise RuntimeError("schema violation inside gap-fill")

    monkeypatch.setattr(bars_step, "_gapfill_multiday_via_kline", _boom)
    monkeypatch.setattr(bars_step, "_reject_preopen_placeholder", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="schema violation"):
        bars_step._finish_daily_bars(
            cfg,
            date(2026, 8, 21),
            run_id,
            start=date(2026, 8, 20),
            end=date(2026, 8, 21),
            expected_tdx_symbols=["301655.SZ"],
            tdx_result={
                "rows_read": 0,
                "rows_written": 0,
                "had_error": True,
                "failed_symbols": ["301655.SZ"],
            },
            sina_result=None,
        )


def test_engine_run_job_surfaces_worker_step_failed_status(tmp_path, monkeypatch):
    cfg = _config(tmp_path, "symbol")
    init_data_layout(cfg)
    from cnequity.orchestrator import engine as engine_mod
    from cnequity.orchestrator.registry import STEP_REGISTRY

    canned = {
        "rows_read": 1,
        "rows_written": 1,
        "status": "failed",
        "unresolved_symbols": ["301655.SZ"],
        "missing_keys": 1,
        "failed_batches": [{"batch_id": "b0", "symbol_count": 1, "sample_symbols": ["301655.SZ"]}],
    }

    def _fake_step(config, trade_date, run_id, context):
        return dict(canned)

    monkeypatch.setattr(STEP_REGISTRY["daily_bars"], "fn", _fake_step)
    engine = JobEngine(cfg)
    result = engine.run_job(
        "daily",
        date(2026, 8, 21),
        backfill=True,
        waves=[engine_mod.WaveConfig(name="w", parallel=False, steps=["daily_bars"])],
    )
    assert result["status"] == "failed"
    entry = next(r for r in result["results"] if r.get("step") == "daily_bars")
    assert entry["status"] == "failed"
    assert entry["unresolved_symbols"] == ["301655.SZ"]
    assert entry["failed_batches"][0]["batch_id"] == "b0"


def test_worker_batch_specs_narrows_to_failed_scope(tmp_path):
    import json

    from cnequity.config import Config

    class _Row:
        def __init__(self, d):
            self.d = d

        def keys(self):
            return list(self.d.keys())

        def __getitem__(self, k):
            return self.d[k]

    cfg = Config(data_root=tmp_path)
    engine = JobEngine(cfg)
    cfg.daily_bars_granularity = "symbol"
    batch = _Row(
        {
            "batch_id": "w0",
            "symbols_json": json.dumps(["s1", "s2", "s3"]),
            "window_start": "2026-08-20",
            "window_end": "2026-08-21",
            "failed_scope_json": json.dumps(
                [{"symbol": "s3", "start": "2026-08-20", "end": "2026-08-21"}]
            ),
        }
    )
    specs = engine._worker_batch_specs([batch], date(2026, 8, 21))
    assert specs == [("w0", ["s3"], date(2026, 8, 20), date(2026, 8, 21))]
