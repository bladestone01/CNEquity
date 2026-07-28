from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from ashare_lake.cli.main import cli
from ashare_lake.config.loader import ScheduleGroup
from ashare_lake.diagnostics.extras import (
    EXTRAS,
    Extra,
    ExtraStatus,
    Impact,
    Scope,
    Use,
    daily_impacts,
    probe_extras,
    racer_native_lib,
)
from ashare_lake.diagnostics.render import render_text, to_dict
from ashare_lake.diagnostics.report import Severity, build_report


def _config(tmp_path, *, sources=None, groups=None, data_root=None):
    return SimpleNamespace(
        data_root=data_root if data_root is not None else tmp_path,
        sources=sources or {},
        schedule_groups=groups or {},
    )


def _status(name: str, *uses: Use, missing: tuple[str, ...] = ("pkg",)) -> ExtraStatus:
    return ExtraStatus(
        extra=Extra(name=name, modules=("pkg",), uses=uses, summary=name),
        missing=missing,
    )


# --- extras registry ---------------------------------------------------------


def test_probe_returns_one_status_per_extra():
    statuses = probe_extras()
    assert [s.extra.name for s in statuses] == [e.name for e in EXTRAS]


def test_every_use_declares_a_known_impact_and_scope():
    for extra in EXTRAS:
        assert extra.uses, f"{extra.name} declares no uses"
        for use in extra.uses:
            assert isinstance(use.impact, Impact)
            assert isinstance(use.scope, Scope)


def test_nlp_is_on_demand_only():
    """SnowNLP is gated behind use_snownlp on the on-demand path.

    The daily sentiment_scores step hardcodes use_snownlp=False, so a missing
    snownlp must never be reported against a daily group.
    """
    nlp = next(e for e in EXTRAS if e.name == "nlp")
    assert nlp.daily_uses() == ()
    assert all(u.scope is Scope.ON_DEMAND for u in nlp.uses)


def test_daily_impacts_ignores_backfill_and_on_demand_scopes():
    status = _status(
        "x",
        Use("daily_step", Impact.BLOCKS, Scope.DAILY),
        Use("backfill_step", Impact.BLOCKS, Scope.BACKFILL),
        Use("on_demand_step", Impact.REDUCES, Scope.ON_DEMAND),
    )
    assert sorted(daily_impacts([status])) == ["daily_step"]


def test_daily_impacts_skips_installed_extras():
    installed = _status("x", Use("s", Impact.BLOCKS, Scope.DAILY), missing=())
    assert daily_impacts([installed]) == {}


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
    """`[all]` installs both on purpose, and our own akshare calls never eval JS.

    Exiting non-zero on the documented install would make doctor noise.
    """
    monkeypatch.setattr(
        "ashare_lake.diagnostics.report.racer_providers",
        lambda: ["mini-racer", "py-mini-racer"],
    )
    report = build_report(config=_config(tmp_path))
    conflict = [f for f in report.findings if "包名冲突" in f.title]
    assert len(conflict) == 1
    assert conflict[0].severity is Severity.WARN
    assert report.ok


# --- config-aware checks -----------------------------------------------------


def test_relative_data_root_is_an_error(tmp_path):
    from pathlib import Path

    report = build_report(config=_config(tmp_path, data_root=Path("./data/ashare-lake")))
    finding = next(f for f in report.findings if "相对路径" in f.title)
    assert finding.severity is Severity.ERROR
    assert not report.ok


def test_missing_data_root_only_warns(tmp_path):
    report = build_report(config=_config(tmp_path, data_root=tmp_path / "absent"))
    finding = next(f for f in report.findings if "尚不存在" in f.title)
    assert finding.severity is Severity.WARN
    assert report.ok


def test_enabled_source_without_package_is_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "ashare_lake.diagnostics.report.probe_extras",
        lambda: [
            ExtraStatus(extra=e, missing=("akshare",) if e.name == "macro" else ()) for e in EXTRAS
        ],
    )
    report = build_report(config=_config(tmp_path, sources={"akshare": True}))
    assert any("sources.akshare" in f.title for f in report.findings)


def test_disabled_source_without_package_is_not_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "ashare_lake.diagnostics.report.probe_extras",
        lambda: [
            ExtraStatus(extra=e, missing=("akshare",) if e.name == "macro" else ()) for e in EXTRAS
        ],
    )
    report = build_report(config=_config(tmp_path, sources={"akshare": False}))
    assert not any("sources.akshare" in f.title for f in report.findings)


def test_silent_empty_source_fails_but_reduced_coverage_only_warns(tmp_path, monkeypatch):
    """Only an invisible zero-row write should fail the command."""
    empties = _status("silent", Use("step_a", Impact.EMPTIES, Scope.DAILY))
    reduces = _status("supplement", Use("step_b", Impact.REDUCES, Scope.DAILY))
    groups = {
        "g_empty": ScheduleGroup(at="16:00", steps=["step_a"]),
        "g_reduce": ScheduleGroup(at="16:00", steps=["step_b"]),
    }
    monkeypatch.setattr("ashare_lake.diagnostics.report.probe_extras", lambda: [empties, reduces])
    report = build_report(config=_config(tmp_path, groups=groups))

    by_group = {f.title.split(":")[0]: f for f in report.findings if "日更组" in f.title}
    assert by_group["日更组 g_empty"].severity is Severity.ERROR
    assert by_group["日更组 g_reduce"].severity is Severity.WARN
    assert not report.ok


def test_group_with_no_affected_steps_produces_no_finding(tmp_path, monkeypatch):
    status = _status("x", Use("unused_step", Impact.BLOCKS, Scope.DAILY))
    groups = {"core": ScheduleGroup(at="16:00", steps=["something_else"])}
    monkeypatch.setattr("ashare_lake.diagnostics.report.probe_extras", lambda: [status])
    report = build_report(config=_config(tmp_path, groups=groups))
    assert not any("日更组" in f.title for f in report.findings)


# --- no-config mode ----------------------------------------------------------


def test_report_without_config_still_works():
    report = build_report(config=None)
    assert any("未加载配置" in f.title for f in report.findings)
    assert report.extras


# --- rendering ---------------------------------------------------------------


def test_render_text_covers_every_finding(tmp_path):
    report = build_report(config=_config(tmp_path))
    text = "\n".join(render_text(report))
    for finding in report.findings:
        assert finding.title in text


def test_to_dict_is_json_serializable(tmp_path):
    payload = to_dict(build_report(config=_config(tmp_path)))
    json.dumps(payload)
    assert {"environment", "extras", "findings", "ok"} <= payload.keys()


# --- CLI ---------------------------------------------------------------------


def test_doctor_cli_runs_without_config(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--config", str(tmp_path / "nope.toml")])
    assert "可选依赖" in result.output


def test_doctor_cli_json_output(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--config", str(tmp_path / "nope.toml"), "--json"])
    payload = json.loads(result.output)
    assert "extras" in payload


@pytest.mark.parametrize("flag", [[], ["--json"]])
@pytest.mark.parametrize(
    ("severity", "expected_exit"),
    [(Severity.ERROR, 1), (Severity.WARN, 0)],
)
def test_doctor_exit_code_follows_report_errors(
    tmp_path, monkeypatch, flag, severity, expected_exit
):
    """Only ERROR findings fail the command; warnings must stay exit 0."""
    from ashare_lake.diagnostics.report import Finding, Report

    monkeypatch.setattr(
        "ashare_lake.diagnostics.report.build_report",
        lambda config=None, config_path=None: Report(
            environment={"ashare-lake": "test"},
            extras=[],
            findings=[Finding(severity=severity, title="synthetic")],
        ),
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--config", str(tmp_path / "nope.toml"), *flag])
    assert result.exit_code == expected_exit


# --- repair ------------------------------------------------------------------


def test_repair_commands_avoid_shell_chaining():
    """Must work in Windows PowerShell 5.1, where `&&` is a syntax error."""
    from ashare_lake.diagnostics.extras import racer_repair_commands

    cmds = racer_repair_commands()
    assert len(cmds) == 2, "uninstall then reinstall — the shared __init__.py needs both"
    for cmd in cmds:
        assert not any("&&" in part for part in cmd)
    assert "py-mini-racer" in cmds[0]
    assert "mini-racer" in cmds[1]


def test_repair_commands_prefer_pip_and_target_this_interpreter(monkeypatch):
    import sys as _sys

    from ashare_lake.diagnostics import extras as ex

    monkeypatch.setattr(ex, "_importable", lambda m: m == "pip")
    cmds = ex.racer_repair_commands()
    assert cmds[0][:3] == [_sys.executable, "-m", "pip"]
    assert cmds[1][:3] == [_sys.executable, "-m", "pip"]


def test_repair_commands_fall_back_to_uv_without_pip(monkeypatch):
    """`uv venv` builds environments with no pip, so -m pip cannot be assumed."""
    import sys as _sys

    from ashare_lake.diagnostics import extras as ex

    monkeypatch.setattr(ex, "_importable", lambda m: False)
    monkeypatch.setattr(ex.shutil, "which", lambda name: "/usr/local/bin/uv")
    cmds = ex.racer_repair_commands()
    assert cmds[0][:2] == ["/usr/local/bin/uv", "pip"]
    assert _sys.executable in cmds[0]
    assert _sys.executable in cmds[1]


def test_repair_commands_empty_when_no_installer(monkeypatch):
    from ashare_lake.diagnostics import extras as ex

    monkeypatch.setattr(ex, "_importable", lambda m: False)
    monkeypatch.setattr(ex.shutil, "which", lambda name: None)
    assert ex.racer_repair_commands() == []


def test_repair_reports_when_no_installer_is_available(monkeypatch):
    from ashare_lake.diagnostics import repair

    monkeypatch.setattr(repair, "racer_providers", lambda: ["mini-racer", "py-mini-racer"])
    monkeypatch.setattr(repair, "racer_repair_commands", lambda: [])
    monkeypatch.setattr(repair.subprocess, "run", lambda *a, **k: pytest.fail("must not shell out"))
    assert repair.repair_racer_conflict(echo=lambda _: None) is False


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
