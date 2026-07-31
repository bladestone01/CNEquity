"""The v1→v2 volume rewrite: rescale 手 sources, stamp all, stay idempotent."""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import polars as pl

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "migrate_daily_bars_volume_v2.py"
_spec = importlib.util.spec_from_file_location("migrate_daily_bars_volume_v2", _SCRIPT)
migrate = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = migrate
_spec.loader.exec_module(migrate)


def _frame(rows: list[tuple[str, int, str]]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "symbol": "600519.SH",
                "trade_date": date(2024, 6, 28),
                "close": 12.5,
                "volume": volume,
                "amount": 500_000.0,
                "source": source,
                "data_version": version,
            }
            for source, volume, version in rows
        ]
    )


def test_rescales_only_the_lot_sources():
    df = _frame(
        [
            ("tdx_protocol", 400, "v1"),
            ("sina", 400, "v1"),
            ("ths", 40_000, "v1"),
            ("baostock", 40_000, "v1"),
        ]
    )
    out, rescaled, restamped = migrate.migrate_frame(df)
    assert out["volume"].to_list() == [40_000, 40_000, 40_000, 40_000]
    assert out["data_version"].to_list() == ["v2"] * 4
    assert (rescaled, restamped) == (2, 4)


def test_leaves_v2_rows_alone_so_a_rerun_is_a_no_op():
    df = _frame([("tdx_protocol", 40_000, "v2")])
    out, rescaled, restamped = migrate.migrate_frame(df)
    assert out["volume"].to_list() == [40_000]
    assert (rescaled, restamped) == (0, 0)


def test_second_pass_over_migrated_data_changes_nothing():
    once, _, _ = migrate.migrate_frame(_frame([("tdx_protocol", 400, "v1"), ("ths", 40_000, "v1")]))
    twice, rescaled, restamped = migrate.migrate_frame(once)
    assert twice.equals(once)
    assert (rescaled, restamped) == (0, 0)


def test_fetched_at_is_not_restamped():
    """data_version records the reinterpretation; fetched_at records when the
    vendor was actually asked, and must survive."""
    df = _frame([("tdx_protocol", 400, "v1")]).with_columns(
        pl.lit("2024-06-28T09:00:00Z").alias("fetched_at")
    )
    out, _, _ = migrate.migrate_frame(df)
    assert out["fetched_at"].to_list() == ["2024-06-28T09:00:00Z"]


def test_dry_run_writes_nothing(tmp_path, capsys):
    part = tmp_path / "daily_bars" / "trade_date=2024-06-28"
    part.mkdir(parents=True)
    path = part / "part-0.parquet"
    _frame([("tdx_protocol", 400, "v1")]).write_parquet(path)
    before = path.read_bytes()

    assert migrate.run(tmp_path, apply=False) == 0
    assert path.read_bytes() == before
    assert "Would rewrite" in capsys.readouterr().out


def test_apply_rewrites_the_partition(tmp_path):
    part = tmp_path / "daily_bars" / "trade_date=2024-06-28"
    part.mkdir(parents=True)
    path = part / "part-0.parquet"
    _frame([("tdx_protocol", 400, "v1"), ("baostock", 40_000, "v1")]).write_parquet(path)

    assert migrate.run(tmp_path, apply=True) == 0
    out = pl.read_parquet(path)
    assert out["volume"].to_list() == [40_000, 40_000]
    assert out["data_version"].to_list() == ["v2", "v2"]


def test_missing_dataset_is_an_error_exit(tmp_path):
    assert migrate.run(tmp_path, apply=False) == 1
