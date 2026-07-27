"""Tests for asl config init / packaged example template."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from ashare_lake.cli.main import cli
from ashare_lake.config.bootstrap import (
    example_toml_text,
    render_example_toml,
    write_user_config,
)


def test_packaged_example_matches_repo_checkout():
    repo_example = Path("configs/ashare-lake.example.toml")
    if not repo_example.is_file():
        pytest.skip("repo example.toml not present")
    assert example_toml_text() == repo_example.read_text(encoding="utf-8")


def test_render_patches_data_root_and_darwin_workers():
    text = render_example_toml(data_root="/tmp/my-lake", platform="darwin")
    assert 'root = "/tmp/my-lake"' in text
    assert "workers = 1" in text
    assert "workers = 8" not in text


def test_render_keeps_linux_workers():
    text = render_example_toml(platform="linux")
    assert "workers = 8" in text


def test_write_user_config_refuses_overwrite(tmp_path):
    out = tmp_path / "configs" / "ashare-lake.toml"
    write_user_config(out, platform="linux")
    assert out.is_file()
    with pytest.raises(FileExistsError):
        write_user_config(out, platform="linux")
    write_user_config(out, force=True, data_root=str(tmp_path / "data"), platform="linux")
    assert f'root = "{tmp_path / "data"}"' in out.read_text(encoding="utf-8")


def test_cli_config_init_and_validate(tmp_path):
    out = tmp_path / "ashare-lake.toml"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "config",
            "init",
            "--config",
            str(out),
            "--data-root",
            str(tmp_path / "lake"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert out.is_file()
    assert "Wrote" in result.output

    again = runner.invoke(cli, ["config", "init", "--config", str(out)])
    assert again.exit_code != 0
    assert "already exists" in again.output

    ok = runner.invoke(cli, ["config", "validate", "--config", str(out)])
    assert ok.exit_code == 0, ok.output
    assert "Configuration OK" in ok.output


def test_resolve_config_missing_suggests_config_init(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["config", "validate"])
    assert result.exit_code != 0
    assert "asl config init" in result.output
