"""Text and JSON rendering for the doctor report."""

from __future__ import annotations

import click

from ashare_lake.diagnostics.report import Report, Severity

_MARK: dict[Severity, tuple[str, str]] = {
    Severity.OK: ("OK  ", "green"),
    Severity.WARN: ("WARN", "yellow"),
    Severity.ERROR: ("FAIL", "red"),
}


def _style(text: str, color: str) -> str:
    return click.style(text, fg=color)


def render_text(report: Report) -> list[str]:
    lines: list[str] = ["环境"]
    width = max(len(k) for k in report.environment)
    for key, value in report.environment.items():
        lines.append(f"  {key.ljust(width)}  {value}")

    lines.append("")
    lines.append("可选依赖")
    for status in report.extras:
        if status.installed:
            mark, color = _MARK[Severity.OK]
            note = "已安装"
        else:
            mark, color = _MARK[Severity.WARN]
            note = f"缺 {', '.join(status.missing)}"
        lines.append(f"  {_style(mark, color)}  [{status.extra.name}] {note}")

    if report.findings:
        lines.append("")
        lines.append("检查结果")
        for finding in report.findings:
            mark, color = _MARK[finding.severity]
            lines.append(f"  {_style(mark, color)}  {finding.title}")
            for detail_line in finding.detail.splitlines():
                lines.append(f"        {detail_line}")
            if finding.fix:
                fix_lines = finding.fix.splitlines()
                lines.append(f"        {_style('→', 'cyan')} {fix_lines[0]}")
                # Repair steps arrive as one line per command; keep them aligned
                # under the arrow so they stay copy-pasteable.
                lines.extend(f"          {extra}" for extra in fix_lines[1:])
            lines.append("")

    errors, warnings = len(report.errors), len(report.warnings)
    if errors:
        lines.append(_style(f"{errors} 个问题需要处理，{warnings} 个警告。", "red"))
    elif warnings:
        lines.append(_style(f"没有致命问题，{warnings} 个警告。", "yellow"))
    else:
        lines.append(_style("一切正常。", "green"))
    return lines


def to_dict(report: Report) -> dict:
    return {
        "environment": report.environment,
        "extras": [
            {
                "name": s.extra.name,
                "installed": s.installed,
                "missing": list(s.missing),
                "summary": s.extra.summary,
                "uses": [
                    {
                        "step": u.step,
                        "impact": u.impact.value,
                        "scope": u.scope.value,
                        "note": u.note,
                    }
                    for u in s.extra.uses
                ],
            }
            for s in report.extras
        ],
        "findings": [
            {
                "severity": f.severity.value,
                "title": f.title,
                "detail": f.detail,
                "fix": f.fix,
            }
            for f in report.findings
        ],
        "ok": report.ok,
    }
