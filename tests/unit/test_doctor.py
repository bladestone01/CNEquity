from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from ashare_lake.cli.main import cli
from ashare_lake.diagnostics.packages import (
    REQUIRED_PACKAGES,
    PackageStatus,
    RequiredPackage,
    probe_packages,
    racer_native_lib,
)
from ashare_lake.diagnostics.render import render_text, to_dict
from ashare_lake.diagnostics.report import Severity, build_report


def _config(tmp_path, *, data_root=None):
    return SimpleNamespace(data_root=data_root if data_root is not None else tmp_path)


# --- package probes ----------------------------------------------------------


def test_probe_returns_one_status_per_required_package():
    statuses = probe_packages()
    assert [s.package.module for s in statuses] == [p.module for p in REQUIRED_PACKAGES]


def test_every_required_package_declares_a_purpose():
    for pkg in REQUIRED_PACKAGES:
        assert pkg.purpose.strip(), f"{pkg.module} has no stated purpose"


def test_required_packages_are_all_declared_dependencies():
    """The probe list must not drift from what the project actually installs."""
    import tomllib

    pyproject = tomllib.loads(
        (Path(__file__).resolve().parents[2] / "pyproject.toml").read_bytes().decode("utf-8")
    )
    declared = {
        req.split(">")[0].split("=")[0].split("[")[0].strip().replace("-", "_").lower()
        for req in pyproject["project"]["dependencies"]
    }
    for pkg in REQUIRED_PACKAGES:
        assert pkg.module.replace("-", "_").lower() in declared, (
            f"{pkg.module} is probed but not a declared dependency"
        )


def test_missing_package_is_an_error(tmp_path, monkeypatch):
    """A hard dependency that will not import means a damaged install."""
    monkeypatch.setattr(
        "ashare_lake.diagnostics.report.probe_packages",
        lambda: [
            PackageStatus(RequiredPackage("akshare", "宏观"), importable=False),
            PackageStatus(RequiredPackage("pandas", "XLS"), importable=True),
        ],
    )
    report = build_report(config=_config(tmp_path))
    finding = next(f for f in report.findings if "必需依赖无法导入" in f.title)
    assert finding.severity is Severity.ERROR
    assert "akshare" in finding.detail
    assert "pandas" not in finding.detail
    assert not report.ok


def test_all_packages_present_produces_no_finding(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "ashare_lake.diagnostics.report.probe_packages",
        lambda: [PackageStatus(RequiredPackage("akshare", "宏观"), importable=True)],
    )
    report = build_report(config=_config(tmp_path))
    assert not any("必需依赖" in f.title for f in report.findings)


# --- py_mini_racer probes ----------------------------------------------------


def test_musl_so_does_not_satisfy_a_glibc_probe(tmp_path):
    (tmp_path / "libmini_racer.muslc.so").write_bytes(b"")
    assert racer_native_lib(platform="linux", pkg_dir=tmp_path) is None


def test_native_lib_found_per_platform(tmp_path):
    (tmp_path / "libmini_racer.dylib").write_bytes(b"")
    assert racer_native_lib(platform="darwin", pkg_dir=tmp_path) is not None
    # Same directory has no Linux binary.
    assert racer_native_lib(platform="linux", pkg_dir=tmp_path) is None


def test_two_providers_warns_without_failing(monkeypatch, tmp_path):
    """Only reachable by upgrading from 0.2.x, and harmless to our own fetches."""
    monkeypatch.setattr(
        "ashare_lake.diagnostics.report.racer_providers",
        lambda: ["mini-racer", "py-mini-racer"],
    )
    report = build_report(config=_config(tmp_path))
    conflict = [f for f in report.findings if "包名冲突" in f.title]
    assert len(conflict) == 1
    assert conflict[0].severity is Severity.WARN
    assert report.ok


# --- data.root ---------------------------------------------------------------


def test_relative_data_root_is_an_error(tmp_path):
    report = build_report(config=_config(tmp_path, data_root=Path("./data/ashare-lake")))
    finding = next(f for f in report.findings if "相对路径" in f.title)
    assert finding.severity is Severity.ERROR
    assert not report.ok


def test_missing_data_root_only_warns(tmp_path):
    report = build_report(config=_config(tmp_path, data_root=tmp_path / "absent"))
    finding = next(f for f in report.findings if "尚不存在" in f.title)
    assert finding.severity is Severity.WARN
    assert report.ok


def test_unwritable_data_root_is_an_error(tmp_path):
    root = tmp_path / "lake"
    root.mkdir()
    root.chmod(0o500)
    try:
        report = build_report(config=_config(tmp_path, data_root=root))
        finding = next(f for f in report.findings if "不可写" in f.title)
        assert finding.severity is Severity.ERROR
    finally:
        root.chmod(0o700)


# --- no-config mode ----------------------------------------------------------


def test_report_without_config_still_probes_packages():
    report = build_report(config=None)
    assert any("未加载配置" in f.title for f in report.findings)
    assert report.packages


# --- rendering ---------------------------------------------------------------


def test_render_text_covers_every_finding(tmp_path):
    report = build_report(config=_config(tmp_path))
    text = "\n".join(render_text(report))
    for finding in report.findings:
        assert finding.title in text


def test_to_dict_is_json_serializable(tmp_path):
    payload = to_dict(build_report(config=_config(tmp_path)))
    json.dumps(payload)
    assert {"environment", "packages", "findings", "ok"} <= payload.keys()


# --- CLI ---------------------------------------------------------------------


def test_doctor_cli_runs_without_config(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--config", str(tmp_path / "nope.toml")])
    assert "依赖" in result.output


def test_doctor_cli_json_output(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--config", str(tmp_path / "nope.toml"), "--json"])
    payload = json.loads(result.output)
    assert "packages" in payload


@pytest.mark.parametrize("flag", [[], ["--json"]])
@pytest.mark.parametrize(("severity", "expected_exit"), [(Severity.ERROR, 1), (Severity.WARN, 0)])
def test_doctor_exit_code_follows_report_errors(
    tmp_path, monkeypatch, flag, severity, expected_exit
):
    """Only ERROR findings fail the command; warnings must stay exit 0."""
    from ashare_lake.diagnostics.report import Finding, Report

    monkeypatch.setattr(
        "ashare_lake.diagnostics.report.build_report",
        lambda config=None, config_path=None: Report(
            environment={"ashare-lake": "test"},
            packages=[],
            findings=[Finding(severity=severity, title="synthetic")],
        ),
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--config", str(tmp_path / "nope.toml"), *flag])
    assert result.exit_code == expected_exit


# --- repair ------------------------------------------------------------------


def test_repair_commands_avoid_shell_chaining():
    """Must work in Windows PowerShell 5.1, where `&&` is a syntax error."""
    from ashare_lake.diagnostics.packages import racer_repair_commands

    cmds = racer_repair_commands()
    assert len(cmds) == 2, "uninstall then reinstall — the shared __init__.py needs both"
    for cmd in cmds:
        assert not any("&&" in part for part in cmd)
    assert "py-mini-racer" in cmds[0]
    assert "mini-racer" in cmds[1]


def test_repair_commands_prefer_pip_and_target_this_interpreter(monkeypatch):
    import sys as _sys

    from ashare_lake.diagnostics import packages as pk

    monkeypatch.setattr(pk, "_importable", lambda m: m == "pip")
    cmds = pk.racer_repair_commands()
    assert cmds[0][:3] == [_sys.executable, "-m", "pip"]
    assert cmds[1][:3] == [_sys.executable, "-m", "pip"]


def test_repair_commands_fall_back_to_uv_without_pip(monkeypatch):
    """`uv venv` builds environments with no pip, so -m pip cannot be assumed."""
    import sys as _sys

    from ashare_lake.diagnostics import packages as pk

    monkeypatch.setattr(pk, "_importable", lambda m: False)
    monkeypatch.setattr(pk.shutil, "which", lambda name: "/usr/local/bin/uv")
    cmds = pk.racer_repair_commands()
    assert cmds[0][:2] == ["/usr/local/bin/uv", "pip"]
    assert _sys.executable in cmds[0]
    assert _sys.executable in cmds[1]


def test_repair_commands_empty_when_no_installer(monkeypatch):
    from ashare_lake.diagnostics import packages as pk

    monkeypatch.setattr(pk, "_importable", lambda m: False)
    monkeypatch.setattr(pk.shutil, "which", lambda name: None)
    assert pk.racer_repair_commands() == []


def test_repair_is_a_noop_without_a_collision(monkeypatch):
    from ashare_lake.diagnostics import repair

    monkeypatch.setattr(repair, "racer_providers", lambda: ["mini-racer"])
    monkeypatch.setattr(repair.subprocess, "run", lambda *a, **k: pytest.fail("must not shell out"))
    assert repair.repair_racer_conflict(echo=lambda _: None) is True


def test_repair_runs_both_commands_without_a_shell(monkeypatch):
    from ashare_lake.diagnostics import repair

    calls = []

    class _Ok:
        returncode = 0
        stdout = stderr = ""

    def _fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return _Ok()

    monkeypatch.setattr(repair, "racer_providers", lambda: ["mini-racer", "py-mini-racer"])
    monkeypatch.setattr(repair.subprocess, "run", _fake_run)

    assert repair.repair_racer_conflict(echo=lambda _: None) is True
    assert len(calls) == 2
    for cmd, kwargs in calls:
        assert isinstance(cmd, list), "argv list, never a shell string"
        assert kwargs.get("shell") in (None, False)


def test_repair_reports_failure(monkeypatch):
    from ashare_lake.diagnostics import repair

    class _Fail:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(repair, "racer_providers", lambda: ["mini-racer", "py-mini-racer"])
    monkeypatch.setattr(repair.subprocess, "run", lambda *a, **k: _Fail())
    assert repair.repair_racer_conflict(echo=lambda _: None) is False


def test_repair_reports_when_no_installer_is_available(monkeypatch):
    from ashare_lake.diagnostics import repair

    monkeypatch.setattr(repair, "racer_providers", lambda: ["mini-racer", "py-mini-racer"])
    monkeypatch.setattr(repair, "racer_repair_commands", lambda: [])
    monkeypatch.setattr(repair.subprocess, "run", lambda *a, **k: pytest.fail("must not shell out"))
    assert repair.repair_racer_conflict(echo=lambda _: None) is False
