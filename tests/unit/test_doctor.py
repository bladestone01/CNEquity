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


def test_two_providers_is_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "ashare_lake.diagnostics.report.racer_providers",
        lambda: ["mini-racer", "py-mini-racer"],
    )
    report = build_report(config=_config(tmp_path))
    conflict = [f for f in report.findings if "包名冲突" in f.title]
    assert len(conflict) == 1
    assert conflict[0].severity is Severity.ERROR
    assert not report.ok


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
def test_doctor_exit_code_follows_errors(tmp_path, monkeypatch, flag):
    monkeypatch.setattr(
        "ashare_lake.diagnostics.report.racer_providers",
        lambda: ["mini-racer", "py-mini-racer"],
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--config", str(tmp_path / "nope.toml"), *flag])
    assert result.exit_code == 1
