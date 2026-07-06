from datetime import date

from click.testing import CliRunner

from stock_data_engine.cli.main import cli
from stock_data_engine.config import load_config
from stock_data_engine.orchestrator.engine import JobEngine
from stock_data_engine.storage.layout import init_data_layout


def _write_config(tmp_path) -> str:
    cfg_path = tmp_path / "stockdata.toml"
    cfg_path.write_text(
        f"""
[data]
root = "{tmp_path / "data"}"

[tdx_protocol]
allow_mock = true

[job.init.phases]
names = ["phase1_reference"]
"""
    )
    return str(cfg_path)


def test_init_layout_only_skips_phases(tmp_path, monkeypatch):
    cfg_path = _write_config(tmp_path)
    called = {"phases": False}

    def fake_run_init_phases(self, trade_date=None):
        called["phases"] = True
        return {"run_id": "x", "phases": []}

    monkeypatch.setattr(JobEngine, "run_init_phases", fake_run_init_phases)

    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", cfg_path, "--layout-only"])

    assert result.exit_code == 0
    assert called["phases"] is False
    assert "Initialized layout" in result.output
    cfg = load_config(cfg_path)
    init_data_layout(cfg)
    assert cfg.curated_root.exists()


def test_init_runs_phases_by_default(tmp_path, monkeypatch):
    cfg_path = _write_config(tmp_path)
    seen: dict[str, date | None] = {"trade_date": "unset"}

    def fake_run_init_phases(self, trade_date=None):
        seen["trade_date"] = trade_date
        return {
            "run_id": "init-run",
            "phases": [{"phase": "phase1_reference", "status": "success"}],
        }

    monkeypatch.setattr(JobEngine, "run_init_phases", fake_run_init_phases)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["init", "--config", cfg_path, "--trade-date", "2024-06-28"],
    )

    assert result.exit_code == 0
    assert seen["trade_date"] == date(2024, 6, 28)
    assert "init-run" in result.output


def test_init_exits_nonzero_when_phase_fails(tmp_path, monkeypatch):
    cfg_path = _write_config(tmp_path)

    def fake_run_init_phases(self, trade_date=None):
        return {
            "run_id": "init-run",
            "phases": [{"phase": "phase1_reference", "status": "failed"}],
        }

    monkeypatch.setattr(JobEngine, "run_init_phases", fake_run_init_phases)

    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", cfg_path])

    assert result.exit_code == 1
