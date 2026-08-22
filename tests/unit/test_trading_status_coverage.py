import json
from datetime import date

import polars as pl
import pytest

from cnequity.config import Config
from cnequity.quality.audit import run_audit
from cnequity.quality.st_coverage import (
    build_st_scope,
    compose_st_coverage_receipt,
    current_st_universe,
    publish_st_coverage_receipt,
    st_evidence_coverage_report,
    write_st_checkpoint,
)
from cnequity.query.universe import (
    coverage_start_date,
    st_coverage_start,
    trading_status_coverage_start,
)
from cnequity.steps.finalize import step_audit


def _write_status_partition(
    cfg: Config,
    trade_date: date,
    status: str = "normal",
    *,
    source: str = "eastmoney",
) -> None:
    path = cfg.curated_root / "trading_status" / f"trade_date={trade_date.isoformat()}"
    path.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["600519.SH"],
            "trade_date": [trade_date],
            "is_trading": [status != "suspended"],
            "status": [status],
            "source": [source],
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


def _write_st_receipt(cfg: Config, start: date, end: date) -> None:
    root = cfg.curated_root / "instruments"
    root.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"symbol": ["600519.SH"]}).write_parquet(root / "part-merged.parquet")
    scope = build_st_scope(["600519.SH"], start, end, universe="all_a")
    checkpoint = {
        "schema_version": 1,
        "claim": "historical_st_evidence",
        "scope": scope,
        "status": "complete",
        "completed_symbols": ["600519.SH"],
        "evidence_rows_by_symbol": {"600519.SH": 1},
        "unresolved_symbols": [],
    }
    write_st_checkpoint(cfg, checkpoint)
    publish_st_coverage_receipt(cfg, checkpoint)


def test_trading_status_coverage_start_from_partitions(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    _write_status_partition(cfg, date(2024, 6, 27))
    _write_status_partition(cfg, date(2024, 6, 28))
    assert trading_status_coverage_start(cfg) == date(2024, 6, 27)
    assert coverage_start_date(cfg, "daily_bars") is None


def test_st_coverage_start_ignores_superseded_positive_label(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    root = cfg.curated_root / "trading_status" / "trade_date=2024-06"
    root.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["600519.SH"],
            "trade_date": [date(2024, 6, 27)],
            "is_trading": [True],
            "status": ["st"],
            "source": ["eastmoney"],
            "data_version": ["v1"],
            "fetched_at": ["2024-06-27T07:00:00+00:00"],
        }
    ).write_parquet(root / "part-old.parquet")
    pl.DataFrame(
        {
            "symbol": ["600519.SH", "600519.SH"],
            "trade_date": [date(2024, 6, 27), date(2024, 6, 28)],
            "is_trading": [True, True],
            "status": ["normal", "st"],
            "source": ["eastmoney", "eastmoney"],
            "data_version": ["v1", "v1"],
            "fetched_at": [
                "2024-06-27T08:00:00+00:00",
                "2024-06-28T07:00:00+00:00",
            ],
        }
    ).write_parquet(root / "part-new.parquet")

    assert st_coverage_start(cfg) == date(2024, 6, 28)


def test_current_st_universe_reads_nested_instrument_fragments(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    root = cfg.curated_root / "instruments"
    root.mkdir(parents=True)
    pl.DataFrame({"symbol": ["600519.SH"]}).write_parquet(root / "part-merged.parquet")
    nested = root / ".old-fragments"
    nested.mkdir()
    pl.DataFrame({"symbol": ["000001.SZ"]}).write_parquet(nested / "part-old.parquet")

    assert current_st_universe(cfg) == ["000001.SZ", "600519.SH"]


def test_current_st_universe_excludes_cdrs(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    instruments = cfg.curated_root / "instruments"
    instruments.mkdir(parents=True)
    pl.DataFrame({"symbol": ["600519.SH", "689009.SH"]}).write_parquet(
        instruments / "part-merged.parquet"
    )

    assert current_st_universe(cfg) == ["600519.SH"]


def test_st_coverage_reports_unsupported_bj_separately(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    instruments = cfg.curated_root / "instruments"
    instruments.mkdir(parents=True)
    pl.DataFrame({"symbol": ["600519.SH", "920001.BJ"]}).write_parquet(
        instruments / "part-merged.parquet"
    )
    bars = cfg.curated_root / "daily_bars" / "trade_date=2024-06-27"
    bars.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["600519.SH", "920001.BJ"],
            "trade_date": [date(2024, 6, 27)] * 2,
            "volume": [100, 100],
        }
    ).write_parquet(bars / "part-0.parquet")

    # This is the legacy name used by the existing production receipt for the
    # Baostock-supported SH/SZ subset.
    scope = build_st_scope(
        ["600519.SH"], date(2024, 6, 27), date(2024, 6, 27), universe="all_a_sh_sz"
    )
    checkpoint = {
        "schema_version": 1,
        "claim": "historical_st_evidence",
        "scope": scope,
        "status": "complete",
        "completed_symbols": ["600519.SH"],
        "evidence_rows_by_symbol": {"600519.SH": 0},
        "unresolved_symbols": [],
    }
    write_st_checkpoint(cfg, checkpoint)
    publish_st_coverage_receipt(cfg, checkpoint)

    report = st_evidence_coverage_report(cfg, date(2024, 6, 27), date(2024, 6, 27))

    assert report["verified"] is False
    assert report["supported_coverage_verified"] is True
    assert report["current_symbols"] == 2
    assert report["supported_symbols"] == 1
    assert report["unsupported_symbols"] == 1
    assert report["unsupported_exchange_counts"] == {"BJ": 1}
    assert report["reason"] == "unsupported_exchange_symbols"

    scoped = st_evidence_coverage_report(
        cfg,
        date(2024, 6, 27),
        date(2024, 6, 27),
        universe="all_a_sh_sz",
    )
    assert scoped["verified"] is True
    assert scoped["current_symbols"] == 1
    assert scoped["unsupported_symbols"] == 0


def test_st_coverage_names_unsupported_bj_without_receipt(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    instruments = cfg.curated_root / "instruments"
    instruments.mkdir(parents=True)
    pl.DataFrame({"symbol": ["920001.BJ"]}).write_parquet(instruments / "part-merged.parquet")
    bars = cfg.curated_root / "daily_bars" / "trade_date=2024-06-27"
    bars.mkdir(parents=True)
    pl.DataFrame(
        {"symbol": ["920001.BJ"], "trade_date": [date(2024, 6, 27)], "volume": [100]}
    ).write_parquet(bars / "part-0.parquet")

    report = st_evidence_coverage_report(cfg, date(2024, 6, 27), date(2024, 6, 27))

    assert report["verified"] is False
    assert report["reason"] == "unsupported_exchange_symbols"
    assert report["unsupported_exchange_counts"] == {"BJ": 1}


def test_current_st_universe_can_prune_bars_to_requested_window(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    instruments = cfg.curated_root / "instruments"
    instruments.mkdir(parents=True)
    pl.DataFrame({"symbol": ["600519.SH", "000001.SZ"]}).write_parquet(
        instruments / "part-merged.parquet"
    )
    bars = cfg.curated_root / "daily_bars"
    bars.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["600519.SH", "000001.SZ"],
            "trade_date": [date(2024, 6, 27), date(2024, 6, 28)],
            "volume": [100, 100],
        }
    ).write_parquet(bars / "part-0.parquet")

    assert current_st_universe(cfg, start=date(2024, 6, 27), end=date(2024, 6, 27)) == [
        "600519.SH"
    ]


def test_current_st_universe_ignores_zero_volume_placeholders(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    instruments = cfg.curated_root / "instruments"
    instruments.mkdir(parents=True)
    pl.DataFrame({"symbol": ["600519.SH", "000001.SZ"]}).write_parquet(
        instruments / "part-merged.parquet"
    )
    bars = cfg.curated_root / "daily_bars" / "trade_date=2024-06-28"
    bars.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["600519.SH", "000001.SZ"],
            "trade_date": [date(2024, 6, 28)] * 2,
            "volume": [0, 100],
        }
    ).write_parquet(bars / "part-0.parquet")

    assert current_st_universe(cfg) == ["000001.SZ"]


def test_current_st_universe_keeps_legacy_rows_in_mixed_daily_schema(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    instruments = cfg.curated_root / "instruments"
    instruments.mkdir(parents=True)
    pl.DataFrame({"symbol": ["600519.SH", "000001.SZ"]}).write_parquet(
        instruments / "part-merged.parquet"
    )
    bars = cfg.curated_root / "daily_bars"
    bars.mkdir(parents=True)
    pl.DataFrame({"symbol": ["600519.SH"], "trade_date": [date(2024, 6, 27)]}).write_parquet(
        bars / "legacy.parquet"
    )
    pl.DataFrame(
        {"symbol": ["000001.SZ"], "trade_date": [date(2024, 6, 28)], "volume": [0]}
    ).write_parquet(bars / "current.parquet")

    assert current_st_universe(cfg) == ["600519.SH"]


def test_current_st_universe_does_not_shrink_scope_around_corrupt_input(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    instruments = cfg.curated_root / "instruments"
    instruments.mkdir(parents=True)
    pl.DataFrame({"symbol": ["600519.SH"]}).write_parquet(instruments / "valid.parquet")
    (instruments / "broken.parquet").write_bytes(b"not a parquet file")

    assert current_st_universe(cfg) == []


def test_current_st_universe_refuses_partial_bar_scope_when_a_file_is_corrupt(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    instruments = cfg.curated_root / "instruments"
    instruments.mkdir(parents=True)
    pl.DataFrame({"symbol": ["600519.SH"]}).write_parquet(instruments / "part-merged.parquet")
    bars = cfg.curated_root / "daily_bars"
    bars.mkdir(parents=True)
    pl.DataFrame(
        {"symbol": ["600519.SH"], "trade_date": [date(2024, 6, 27)], "volume": [100]}
    ).write_parquet(bars / "valid.parquet")
    (bars / "broken.parquet").write_bytes(b"not a parquet file")

    assert current_st_universe(cfg) == []


def test_st_coverage_ignores_tampered_receipt(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    start = end = date(2024, 6, 27)
    _write_status_partition(cfg, start, source="baostock")
    _write_st_receipt(cfg, start, end)

    import json

    receipt_path = next(
        (cfg.meta_root / "quality" / "coverage" / "historical_st_evidence").glob("*.json")
    )
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["completed_symbols_sha256"] = "tampered"
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")

    report = st_evidence_coverage_report(cfg, start, end)
    assert report["verified"] is False
    assert report["reason"] == "no_matching_complete_receipt"


def test_st_coverage_reports_matching_pending_checkpoint_progress(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    start, end = date(2016, 1, 1), date(2026, 8, 21)
    instruments = cfg.curated_root / "instruments"
    instruments.mkdir(parents=True)
    pl.DataFrame({"symbol": ["600519.SH"]}).write_parquet(instruments / "part-merged.parquet")
    _write_bars_partition(cfg, date(2016, 1, 4))
    scope = build_st_scope(["600519.SH"], start, end, universe="all_a")
    write_st_checkpoint(
        cfg,
        {
            "schema_version": 1,
            "claim": "historical_st_evidence",
            "scope": scope,
            "status": "pending",
            "completed_symbols": [],
            "unresolved_symbols": [],
        },
    )

    report = st_evidence_coverage_report(cfg, date(2001, 1, 2), end)

    assert report["verified"] is False
    assert report["reason"] == "no_matching_complete_receipt"
    assert report["checkpoint_status"] == "pending"
    assert report["checkpoint_scope_start"] == "2016-01-01"
    assert report["checkpoint_completed_symbols"] == 0
    assert report["checkpoint_expected_symbols"] == 1
    assert report["checkpoint_unresolved_symbols"] == 0


def test_st_receipt_rejects_duplicate_primary_rows(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    day = date(2024, 6, 27)
    path = cfg.curated_root / "trading_status" / f"trade_date={day.isoformat()}"
    path.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["600519.SH", "600519.SH"],
            "trade_date": [day, day],
            "is_trading": [True, True],
            "status": ["st", "st"],
            "source": ["baostock", "baostock"],
            "data_version": ["v1", "v1"],
            "fetched_at": [
                "2024-06-27T00:00:00+00:00",
                "2024-06-27T01:00:00+00:00",
            ],
        }
    ).write_parquet(path / "part-0.parquet")
    scope = build_st_scope(["600519.SH"], day, day, universe="all_a")
    checkpoint = {
        "schema_version": 1,
        "claim": "historical_st_evidence",
        "scope": scope,
        "status": "complete",
        "completed_symbols": ["600519.SH"],
        "evidence_rows_by_symbol": {"600519.SH": 2},
        "unresolved_symbols": [],
    }

    with pytest.raises(ValueError, match="reach curated storage"):
        publish_st_coverage_receipt(cfg, checkpoint)


def test_st_receipt_counts_baostock_before_cross_source_dedupe(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    day = date(2024, 6, 27)
    path = cfg.curated_root / "trading_status" / f"trade_date={day.isoformat()}"
    path.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["600519.SH", "600519.SH"],
            "trade_date": [day, day],
            "is_trading": [True, True],
            "status": ["st", "normal"],
            "source": ["baostock", "eastmoney"],
            "data_version": ["v1", "v1"],
            "fetched_at": [
                "2024-06-27T00:00:00+00:00",
                "2024-06-27T01:00:00+00:00",
            ],
        }
    ).write_parquet(path / "part-0.parquet")
    scope = build_st_scope(["600519.SH"], day, day, universe="all_a")
    checkpoint = {
        "schema_version": 1,
        "claim": "historical_st_evidence",
        "scope": scope,
        "status": "complete",
        "completed_symbols": ["600519.SH"],
        "evidence_rows_by_symbol": {"600519.SH": 1},
        "unresolved_symbols": [],
    }

    assert publish_st_coverage_receipt(cfg, checkpoint).exists()


def test_composes_deep_history_receipt_with_overlapping_tail(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    instruments = cfg.curated_root / "instruments"
    instruments.mkdir(parents=True)
    pl.DataFrame({"symbol": ["600519.SH", "600520.SH"]}).write_parquet(
        instruments / "part-merged.parquet"
    )
    bars = cfg.curated_root / "daily_bars"
    for symbol, day in (("600519.SH", date(2001, 1, 2)), ("600520.SH", date(2020, 1, 2))):
        path = bars / f"trade_date={day.isoformat()}"
        path.mkdir(parents=True)
        pl.DataFrame(
            {
                "symbol": [symbol],
                "trade_date": [day],
                "volume": [100],
                "amount": [1000.0],
            }
        ).write_parquet(path / f"{symbol}.parquet")

    status = cfg.curated_root / "trading_status"
    for symbol, day in (
        ("600519.SH", date(2001, 1, 2)),
        ("600519.SH", date(2026, 8, 21)),
        ("600520.SH", date(2026, 8, 21)),
    ):
        path = status / f"trade_date={day.isoformat()}"
        path.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(
            {
                "symbol": [symbol],
                "trade_date": [day],
                "is_trading": [True],
                "status": ["normal"],
                "source": ["baostock"],
            }
        ).write_parquet(path / f"{symbol}.parquet")

    base_scope = build_st_scope(
        ["600519.SH"], date(2001, 1, 1), date(2026, 8, 14), universe="all_a_sh_sz"
    )
    base_checkpoint = {
        "schema_version": 1,
        "claim": "historical_st_evidence",
        "scope": base_scope,
        "status": "complete",
        "completed_symbols": ["600519.SH"],
        "evidence_rows_by_symbol": {"600519.SH": 1},
        "unresolved_symbols": [],
    }
    write_st_checkpoint(cfg, base_checkpoint)
    publish_st_coverage_receipt(cfg, base_checkpoint)

    extension_scope = build_st_scope(
        ["600519.SH", "600520.SH"], date(2016, 1, 1), date(2026, 8, 21), universe="all_a"
    )
    extension_checkpoint = {
        "schema_version": 1,
        "claim": "historical_st_evidence",
        "scope": extension_scope,
        "status": "complete",
        "completed_symbols": ["600519.SH", "600520.SH"],
        "evidence_rows_by_symbol": {"600519.SH": 1, "600520.SH": 1},
        "unresolved_symbols": [],
    }
    write_st_checkpoint(cfg, extension_checkpoint)
    extension_path = publish_st_coverage_receipt(cfg, extension_checkpoint)
    extension_receipt = json.loads(extension_path.read_text(encoding="utf-8"))

    composed = compose_st_coverage_receipt(cfg, extension_receipt)

    assert composed is not None
    report = st_evidence_coverage_report(cfg, date(2001, 1, 2), date(2026, 8, 21))
    assert report["verified"] is True
    assert report["coverage_start"] == "2001-01-01"
    assert report["coverage_end"] == "2026-08-21"


def test_audit_warns_when_st_labels_lag_bars(tmp_path):
    """Suspension covered historically, but ST labels only start late → warning."""
    cfg = Config(data_root=tmp_path / "data")
    _write_bars_partition(cfg, date(2016, 1, 4))
    _write_status_partition(cfg, date(2016, 1, 4), status="suspended")  # historical suspension
    _write_status_partition(cfg, date(2024, 6, 28), status="st")  # ST only recent
    run_id = "run-ts-coverage"
    trade_date = date(2024, 6, 28)

    run_audit(cfg, run_id, trade_date, {})

    import json

    findings = json.loads(
        (cfg.meta_root / "quality" / "findings" / f"{run_id}.json").read_text(encoding="utf-8")
    )["findings"]
    coverage = [f for f in findings if f.get("check") == "trading_status_coverage_start"]
    assert len(coverage) == 1
    assert coverage[0]["severity"] == "warning"
    assert coverage[0]["coverage_start"] == "2016-01-04"
    assert coverage[0]["st_coverage_start"] == "2024-06-28"
    assert "ST evidence" in coverage[0]["message"]


def test_audit_surfaces_unsupported_st_source_scope(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    day = date(2024, 6, 28)
    instruments = cfg.curated_root / "instruments"
    instruments.mkdir(parents=True)
    pl.DataFrame({"symbol": ["600519.SH", "920001.BJ"]}).write_parquet(
        instruments / "part-merged.parquet"
    )
    bars = cfg.curated_root / "daily_bars" / f"trade_date={day.isoformat()}"
    bars.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["600519.SH", "920001.BJ"],
            "trade_date": [day, day],
            "open": [10.0, 10.0],
            "high": [11.0, 11.0],
            "low": [9.0, 9.0],
            "close": [10.5, 10.5],
            "volume": [1000, 1000],
            "amount": [10_500.0, 10_500.0],
            "source": ["tdx_protocol", "sina"],
            "data_version": ["v1", "v1"],
            "fetched_at": [f"{day.isoformat()}T00:00:00+00:00"] * 2,
        }
    ).write_parquet(bars / "part-0.parquet")
    _write_status_partition(cfg, day)

    run_id = "run-unsupported-st-scope"
    run_audit(cfg, run_id, day, {})

    import json

    findings = json.loads(
        (cfg.meta_root / "quality" / "findings" / f"{run_id}.json").read_text(encoding="utf-8")
    )["findings"]
    coverage = [f for f in findings if f.get("check") == "trading_status_coverage_start"]
    assert len(coverage) == 1
    assert coverage[0]["st_evidence_unsupported_symbols"] == 1
    assert coverage[0]["st_evidence_unsupported_exchange_counts"] == {"BJ": 1}
    assert "BJ=1" in coverage[0]["message"]


def test_audit_coverage_info_when_st_and_suspension_aligned(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    _write_bars_partition(cfg, date(2024, 6, 28))
    _write_status_partition(cfg, date(2024, 6, 28), status="st", source="baostock")
    _write_st_receipt(cfg, date(2024, 6, 28), date(2024, 6, 28))
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
    assert coverage[0]["st_evidence_verified"] is True


def test_audit_st_evidence_uses_last_traded_bar_on_weekend(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    last_trading_day = date(2024, 6, 28)
    _write_bars_partition(cfg, last_trading_day)
    _write_status_partition(cfg, last_trading_day, status="st", source="baostock")
    _write_st_receipt(cfg, last_trading_day, last_trading_day)

    run_id = "run-weekend-st-coverage"
    run_audit(cfg, run_id, date(2024, 6, 29), {})  # Saturday

    import json

    payload = json.loads(
        (cfg.meta_root / "quality" / "findings" / f"{run_id}.json").read_text(encoding="utf-8")
    )
    coverage = [f for f in payload["findings"] if f.get("check") == "trading_status_coverage_start"]
    assert len(coverage) == 1
    assert coverage[0]["severity"] == "info"
    assert coverage[0]["st_evidence_verified"] is True
    assert coverage[0]["daily_bars_end"] == last_trading_day.isoformat()
