"""Offline coverage for `asl demo` (real network is mocked)."""

from __future__ import annotations

from datetime import date

import polars as pl
from click.testing import CliRunner

from ashare_lake.cli.main import cli
from ashare_lake.domain.schemas import validate_dataframe, with_provenance
from ashare_lake.orchestrator.registry import STEP_REGISTRY, StepEntry


def _inst_frame(symbols: list[str]) -> pl.DataFrame:
    rows = []
    for sym in symbols:
        code, exch = sym.split(".")
        rows.append(
            {
                "symbol": sym,
                "name": f"Name-{code}",
                "exchange": exch,
                "asset_type": "stock",
                "list_date": date(2010, 1, 1),
                "delist_date": None,
                "prev_symbol": None,
            }
        )
    return with_provenance(pl.DataFrame(rows), source="tdx_protocol", data_version="v1")


def _bars_frame(symbols: list[str], start: date, end: date) -> pl.DataFrame:
    rows = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            for sym in symbols:
                rows.append(
                    {
                        "symbol": sym,
                        "trade_date": d,
                        "open": 10.0,
                        "high": 11.0,
                        "low": 9.0,
                        "close": 10.5,
                        "volume": 1000,
                        "amount": 1.0e6,
                        "source": "tdx_protocol",
                        "data_version": "v1",
                        "fetched_at": "2024-06-28T00:00:00+00:00",
                    }
                )
        d = date.fromordinal(d.toordinal() + 1)
    return pl.DataFrame(rows)


def test_asl_demo_offline(tmp_path, monkeypatch):
    symbols = ["600519.SH", "000001.SZ"]
    monkeypatch.setattr("ashare_lake.cli.demo._probe_tdx", lambda cfg: None)
    monkeypatch.setattr(
        "ashare_lake.adapters.tdx_protocol.client.fetch_instruments",
        lambda **kwargs: _inst_frame(symbols),
    )
    monkeypatch.setattr(
        "ashare_lake.adapters.tdx_protocol.client.normalize_with_source",
        lambda df: df,
    )

    def fake_calendar(config, trade_date, run_id, context):
        from ashare_lake.storage.atomic import write_parquet_atomic

        rows = []
        d = date(2024, 5, 1)
        while d <= date(2024, 6, 28):
            rows.append(
                {
                    "trade_date": d,
                    "is_trading": d.weekday() < 5,
                    "source": "seed",
                    "data_version": "v1",
                    "fetched_at": "2024-06-28T00:00:00+00:00",
                }
            )
            d = date.fromordinal(d.toordinal() + 1)
        df = validate_dataframe(pl.DataFrame(rows), "trading_calendar")
        for (year,), group in (
            df.with_columns(pl.col("trade_date").dt.year().alias("_y"))
            .partition_by("_y", as_dict=True)
            .items()
        ):
            out = config.curated_root / "trading_calendar" / f"trade_date={year}"
            out.mkdir(parents=True, exist_ok=True)
            write_parquet_atomic(out / "part-000.parquet", group.drop("_y"))
        return {"rows_read": df.height, "rows_written": df.height}

    def fake_daily_bars(config, trade_date, run_id, context):
        from ashare_lake.storage import StagingWriter

        start = getattr(config, "_backfill_start", date(2024, 6, 1))
        end = getattr(config, "_backfill_end", trade_date)
        df = validate_dataframe(_bars_frame(symbols, start, end), "daily_bars")
        StagingWriter(config.staging_root).write_batch("daily_bars", run_id, "batch-0", df)
        return {"rows_read": df.height, "rows_written": df.height}

    def fake_compact(config, trade_date, run_id, context):
        from ashare_lake.storage.parquet import compact_dataset
        from ashare_lake.storage.state import StateStore

        n = compact_dataset(config.staging_root, config.curated_root, "daily_bars", run_id)
        StateStore(config.meta_root).set_date("daily_bars", trade_date)
        return {"rows_read": n, "rows_written": n}

    originals = {
        name: STEP_REGISTRY[name] for name in ("trading_calendar", "daily_bars", "compact")
    }
    STEP_REGISTRY["trading_calendar"] = StepEntry(fn=fake_calendar, group="core")
    STEP_REGISTRY["daily_bars"] = StepEntry(fn=fake_daily_bars, group="core", requires_workers=True)
    STEP_REGISTRY["compact"] = StepEntry(fn=fake_compact, group="finalize")
    try:
        data_root = tmp_path / "demo-lake"
        config_out = tmp_path / "demo.toml"
        result = CliRunner().invoke(
            cli,
            [
                "demo",
                "--symbols",
                ",".join(symbols),
                "--days",
                "10",
                "--data-root",
                str(data_root),
                "--config-out",
                str(config_out),
                "--trade-date",
                "2024-06-28",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Probe TDX" in result.output
        assert "600519.SH" in result.output
        assert config_out.exists()
        assert (data_root / "curated" / "instruments" / "part-merged.parquet").exists()
        assert list((data_root / "curated" / "daily_bars").glob("**/*.parquet"))
    finally:
        STEP_REGISTRY.update(originals)


def test_demo_help_lists_command():
    result = CliRunner().invoke(cli, ["demo", "--help"])
    assert result.exit_code == 0
    assert "--symbols" in result.output
    assert "--days" in result.output
