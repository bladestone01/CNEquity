"""Assemble the `asl doctor` report from environment, extras, and config."""

from __future__ import annotations

import os
import platform
import shlex
import sys
from dataclasses import dataclass, field
from enum import StrEnum
from importlib import metadata
from pathlib import Path

from ashare_lake.diagnostics.extras import (
    EXTRAS_BY_NAME,
    SOURCE_REQUIREMENTS,
    ExtraStatus,
    Impact,
    Scope,
    daily_impacts,
    probe_extras,
    racer_native_lib,
    racer_package_dir,
    racer_providers,
    racer_repair_commands,
)


class Severity(StrEnum):
    OK = "ok"
    WARN = "warn"
    ERROR = "error"


# A silent zero-row write is the only failure mode nothing else surfaces, so it
# is the only one that fails the command. A step that raises is already loud.
_IMPACT_SEVERITY: dict[Impact, Severity] = {
    Impact.EMPTIES: Severity.ERROR,
    Impact.BLOCKS: Severity.WARN,
    Impact.REDUCES: Severity.WARN,
}

_IMPACT_LABEL: dict[Impact, str] = {
    Impact.BLOCKS: "硬失败",
    Impact.EMPTIES: "静默零行",
    Impact.REDUCES: "覆盖变窄",
}

_SCOPE_LABEL: dict[Scope, str] = {
    Scope.DAILY: "日更",
    Scope.BACKFILL: "回填/init",
    Scope.ON_DEMAND: "on-demand",
}


@dataclass(frozen=True)
class Finding:
    severity: Severity
    title: str
    detail: str = ""
    fix: str = ""


@dataclass
class Report:
    environment: dict[str, str] = field(default_factory=dict)
    extras: list[ExtraStatus] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.WARN]

    @property
    def ok(self) -> bool:
        return not self.errors


def _environment() -> dict[str, str]:
    try:
        version = metadata.version("ashare-lake")
    except metadata.PackageNotFoundError:  # pragma: no cover - source tree without install
        version = "unknown (not installed)"
    pkg_root = Path(__file__).resolve().parent.parent
    return {
        "ashare-lake": version,
        "python": f"{sys.version.split()[0]} ({platform.machine()})",
        "platform": f"{platform.system()} {platform.release()}",
        "package": str(pkg_root),
        "executable": sys.executable,
    }


def _racer_fix_text() -> str:
    commands = racer_repair_commands()
    if not commands:
        return "当前环境既没有 pip 也找不到 uv；请用管理该环境的工具卸载 py-mini-racer 后强制重装 mini-racer"
    return "`asl doctor --fix` 可自动执行，或手动依次运行：\n" + "\n".join(
        "  " + shlex.join(cmd) for cmd in commands
    )


def _check_racer(findings: list[Finding]) -> None:
    providers = racer_providers()
    if len(providers) > 1:
        findings.append(
            # WARN, not ERROR: [all] installs both on purpose (a full daily
            # pipeline needs tdx and macro), and every akshare endpoint this
            # project calls lives in a module that never evals JS. Failing the
            # command on the documented install would just train people to
            # ignore doctor.
            Finding(
                severity=Severity.WARN,
                title=f"py_mini_racer 包名冲突: {' + '.join(providers)}",
                detail=(
                    "这些发行包都往同一个 import 包 py_mini_racer/ 里写文件，"
                    "安装器不会拦截，后装的覆盖先装的，结果是加载器与二进制不匹配"
                    "（dlsym: symbol not found）。\n"
                    "  本项目采集不受影响：用到的 akshare 接口都不做 JS 求值。"
                    "若你直接调用 akshare 的 cninfo / sina 系列接口，那些会失败。\n"
                    "  mootdx 只在 utils/holiday.py 用到 py-mini-racer，且 mootdx 内部"
                    "无人 import 该模块，卸掉不影响行情采集。"
                ),
                # Rendered as separate argv lines rather than a `&&` chain: that
                # operator is a syntax error in Windows PowerShell 5.1.
                fix=_racer_fix_text(),
            )
        )
        return

    if not providers or racer_package_dir() is None:
        return

    if racer_native_lib() is None:
        findings.append(
            Finding(
                severity=Severity.WARN,
                title=f"py_mini_racer 缺少本平台原生库 ({providers[0]})",
                detail=(
                    f"{platform.system()}/{platform.machine()} 上没有匹配的原生库——"
                    "多半是没有对应 wheel、从 sdist 构建后只落了其他平台的二进制。"
                    "import 能过，首次求值才 RuntimeError。当前没有源用到 JS 求值，暂不影响。"
                ),
                fix="需要 JS 求值时改用有本平台 wheel 的 mini-racer（随 [macro] 安装）",
            )
        )


def _check_extras(statuses: list[ExtraStatus], findings: list[Finding]) -> None:
    """Per-extra findings, used only when no config is available to be precise."""
    for status in statuses:
        if status.installed:
            continue
        lines = [
            f"  {u.step}（{_SCOPE_LABEL[u.scope]}·{_IMPACT_LABEL[u.impact]}）"
            + (f" — {u.note}" if u.note else "")
            for u in status.extra.uses
        ]
        worst = max(
            (_IMPACT_SEVERITY[u.impact] for u in status.extra.uses),
            key=lambda s: (s is Severity.ERROR, s is Severity.WARN),
            default=Severity.WARN,
        )
        findings.append(
            Finding(
                severity=worst,
                title=f"[{status.extra.name}] 未安装 — 缺 {', '.join(status.missing)}",
                detail=status.extra.summary + "\n" + "\n".join(lines),
                fix=status.install_hint,
            )
        )


def _check_data_root(data_root: Path, findings: list[Finding]) -> None:
    if not data_root.is_absolute():
        findings.append(
            Finding(
                severity=Severity.ERROR,
                title=f"data.root 是相对路径: {data_root}",
                detail=(
                    "相对路径按进程 CWD 解析。调度器（launchd/cron）的 CWD 与你手跑时不同，"
                    "会在别处静默建出第二个空湖，可能几天后才发现。"
                ),
                fix=f"把配置里的 data.root 改成绝对路径，例如 {data_root.resolve()}",
            )
        )
        return

    if not data_root.exists():
        findings.append(
            Finding(
                severity=Severity.WARN,
                title=f"data.root 尚不存在: {data_root}",
                detail="首次 `asl init` 会创建它。",
                fix="asl init --config <配置路径>",
            )
        )
        return

    if not os.access(data_root, os.W_OK):
        findings.append(
            Finding(
                severity=Severity.ERROR,
                title=f"data.root 不可写: {data_root}",
                detail="编排会在首次写入时失败。",
                fix=f"chmod u+w {data_root}",
            )
        )


def _check_sources(config, statuses: list[ExtraStatus], findings: list[Finding]) -> None:
    """Flag [sources.*] toggles that are enabled but have no package behind them."""
    by_name = {s.extra.name: s for s in statuses}
    for source, extra_name in SOURCE_REQUIREMENTS.items():
        if not config.sources.get(source, False):
            continue
        status = by_name.get(extra_name)
        if status is None or status.installed:
            continue
        extra = EXTRAS_BY_NAME[extra_name]
        worst = max(
            (_IMPACT_SEVERITY[u.impact] for u in extra.uses),
            key=lambda s: (s is Severity.ERROR, s is Severity.WARN),
            default=Severity.WARN,
        )
        findings.append(
            Finding(
                severity=worst,
                title=f"[sources.{source}] enabled = true，但 {source} 未安装",
                detail="配置声明启用了这个源，实际调用时它不会参与——" + extra.summary,
                fix=f'pip install "ashare-lake[{extra_name}]"  # 或在配置里关掉该源',
            )
        )


def _check_groups(config, statuses: list[ExtraStatus], findings: list[Finding]) -> None:
    """Report, per configured daily group, which steps lose a source."""
    impact = daily_impacts(statuses)
    if not impact:
        return
    for group_name, group in sorted(config.schedule_groups.items()):
        hits = [s for s in group.steps if s in impact]
        if not hits:
            continue
        lines: list[str] = []
        worst = Severity.WARN
        extras_needed: set[str] = set()
        for step in hits:
            for status, use in impact[step]:
                lines.append(
                    f"  {step} → 缺 [{status.extra.name}]（{_IMPACT_LABEL[use.impact]}）"
                    + (f" — {use.note}" if use.note else "")
                )
                extras_needed.add(status.extra.name)
                if _IMPACT_SEVERITY[use.impact] is Severity.ERROR:
                    worst = Severity.ERROR
        findings.append(
            Finding(
                severity=worst,
                title=f"日更组 {group_name}: {len(hits)} 个 step 依赖未安装的 extra",
                detail="\n".join(lines),
                fix="pip install " + " ".join(f'"ashare-lake[{n}]"' for n in sorted(extras_needed)),
            )
        )


def build_report(config=None, config_path: Path | None = None) -> Report:
    """Collect environment, extras, and (when available) config-aware findings."""
    statuses = probe_extras()
    findings: list[Finding] = []

    _check_racer(findings)

    if config is None:
        # Without a config the per-extra findings are all we can say; with one,
        # the source and group checks below cover the same ground precisely, so
        # emitting both would report a single root cause several times over.
        _check_extras(statuses, findings)
        findings.append(
            Finding(
                severity=Severity.WARN,
                title="未加载配置——只做了依赖体检",
                detail="没有配置就无法检查 data.root、启用的源，以及日更组的依赖影响。",
                fix="asl config init",
            )
        )
    else:
        _check_data_root(Path(config.data_root), findings)
        _check_sources(config, statuses, findings)
        _check_groups(config, statuses, findings)

    env = _environment()
    if config_path is not None:
        env["config"] = str(config_path)

    return Report(environment=env, extras=statuses, findings=findings)
