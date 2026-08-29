from __future__ import annotations

import json

import polars as pl
from click.testing import CliRunner

from cnequity.cli.main import cli
from cnequity.config import Config


def _config(tmp_path):
    data = tmp_path / "lake"
    path = tmp_path / "cnequity.toml"
    path.write_text(f'[data]\nroot = "{data}"\n', encoding="utf-8")
    return Config(data_root=data), path


def test_source_resilience_enforce_passes_for_core_and_discloses_adj_single_source():
    result = CliRunner().invoke(cli, ["source", "resilience", "--enforce"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["backup_gate"]["passed"] is True
    adj = next(item for item in payload["datasets"] if item["dataset"] == "adj_factors")
    assert adj["impact"]["single_source_primary"] is True


def test_source_slo_without_history_fails_closed(tmp_path):
    _, path = _config(tmp_path)
    result = CliRunner().invoke(cli, ["source", "slo", "--config", str(path), "--enforce"])

    assert result.exit_code == 1
    assert json.loads(result.output)["passed"] is False


def test_snapshot_cli_round_trip(tmp_path):
    cfg, path = _config(tmp_path)
    part = cfg.curated_root / "daily_bars" / "trade_date=2026-08-28"
    part.mkdir(parents=True)
    pl.DataFrame({"symbol": ["600000.SH"], "trade_date": ["2026-08-28"]}).write_parquet(
        part / "part.parquet"
    )
    snapshots = tmp_path / "snapshots"
    runner = CliRunner()

    created = runner.invoke(
        cli,
        [
            "snapshot",
            "create",
            "acceptance",
            "--dataset",
            "daily_bars",
            "--config",
            str(path),
            "--snapshot-root",
            str(snapshots),
        ],
    )
    verified = runner.invoke(
        cli,
        [
            "snapshot",
            "verify",
            "acceptance",
            "--config",
            str(path),
            "--snapshot-root",
            str(snapshots),
        ],
    )
    target = tmp_path / "restored"
    restored = runner.invoke(
        cli,
        [
            "snapshot",
            "restore",
            "acceptance",
            str(target),
            "--config",
            str(path),
            "--snapshot-root",
            str(snapshots),
        ],
    )

    assert created.exit_code == 0, created.output
    assert verified.exit_code == 0, verified.output
    assert restored.exit_code == 0, restored.output
    assert (target / "curated/daily_bars/trade_date=2026-08-28/part.parquet").is_file()
