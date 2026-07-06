from datetime import date

import polars as pl

from stock_data_engine.config import Config
from stock_data_engine.quality.audit import run_audit
from stock_data_engine.query.universe import coverage_start_date, trading_status_coverage_start
from stock_data_engine.steps.finalize import step_audit


def _write_status_partition(cfg: Config, trade_date: date) -> None:
    path = cfg.curated_root / "trading_status" / f"trade_date={trade_date.isoformat()}"
    path.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["600519.SH"],
            "trade_date": [trade_date],
            "is_trading": [True],
            "status": ["normal"],
            "source": ["eastmoney"],
            "data_version": ["v1"],
            "fetched_at": [f"{trade_date.isoformat()}T00:00:00+00:00"],
        }
    ).write_parquet(path / "part-0.parquet")


def _write_bars_partition(cfg: Config, trade_date: date) -> None:
    path = cfg.curated_root / "daily_bars" / f"trade_date={trade_date.isoformat()}"
    path.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["600519.SH"],
            "trade_date": [trade_date],
            "open": [10.0],
            "high": [11.0],
            "low": [9.0],
            "close": [10.5],
            "volume": [1000],
            "amount": [10_500.0],
            "source": ["tdx_protocol"],
            "data_version": ["v1"],
            "fetched_at": [f"{trade_date.isoformat()}T00:00:00+00:00"],
        }
    ).write_parquet(path / "part-0.parquet")


def test_trading_status_coverage_start_from_partitions(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    _write_status_partition(cfg, date(2024, 6, 27))
    _write_status_partition(cfg, date(2024, 6, 28))
    assert trading_status_coverage_start(cfg) == date(2024, 6, 27)
    assert coverage_start_date(cfg, "daily_bars") is None


def test_audit_emits_trading_status_coverage_start_warning(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    _write_bars_partition(cfg, date(2016, 1, 4))
    _write_status_partition(cfg, date(2024, 6, 28))
    run_id = "run-ts-coverage"
    trade_date = date(2024, 6, 28)

    run_audit(cfg, run_id, trade_date, {})

    payload = (cfg.meta_root / "quality" / "findings" / f"{run_id}.json").read_text(
        encoding="utf-8"
    )
    import json

    findings = json.loads(payload)["findings"]
    coverage = [f for f in findings if f.get("check") == "trading_status_coverage_start"]
    assert len(coverage) == 1
    assert coverage[0]["severity"] == "warning"
    assert coverage[0]["coverage_start"] == "2024-06-28"
    assert coverage[0]["daily_bars_start"] == "2016-01-04"
    assert "does not filter ST/suspended" in coverage[0]["message"]


def test_audit_coverage_start_info_when_aligned(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    _write_bars_partition(cfg, date(2024, 6, 28))
    _write_status_partition(cfg, date(2024, 6, 28))
    run_id = "run-aligned"
    trade_date = date(2024, 6, 28)

    step_audit(cfg, trade_date, run_id, {})

    import json

    payload = json.loads(
        (cfg.meta_root / "quality" / "findings" / f"{run_id}.json").read_text(encoding="utf-8")
    )
    coverage = [f for f in payload["findings"] if f.get("check") == "trading_status_coverage_start"]
    assert len(coverage) == 1
    assert coverage[0]["severity"] == "info"
