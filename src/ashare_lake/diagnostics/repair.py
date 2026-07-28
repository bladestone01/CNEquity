"""In-place repair for the py_mini_racer distribution collision.

Kept out of the CLI so the subprocess handling stays testable. Commands run
through ``subprocess.run`` with an argv list and ``shell=False``: the repair has
to work identically on macOS, Linux and Windows, and shell chaining does not —
``&&`` is a syntax error in Windows PowerShell 5.1.
"""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable

from ashare_lake.diagnostics.packages import racer_providers, racer_repair_commands


def repair_racer_conflict(echo: Callable[[str], None] = print) -> bool:
    """Drop py-mini-racer and restore mini-racer. True when nothing is left to do.

    Returns False only when a step actually failed, so the caller can exit
    non-zero. A no-op (no collision present) is a success.
    """
    providers = racer_providers()
    if len(providers) < 2:
        echo("py_mini_racer 没有冲突，无需修复。")
        return True

    echo(f"检测到冲突: {' + '.join(providers)}")
    commands = racer_repair_commands()
    if not commands:
        echo("当前环境既没有 pip 也找不到 uv，无法自动修复。")
        echo("请用管理该环境的工具卸载 py-mini-racer，再强制重装 mini-racer。")
        return False

    for cmd in commands:
        echo(f"  $ {shlex.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            echo(f"命令失败（退出码 {result.returncode}）:")
            echo((result.stderr or result.stdout).strip())
            return False

    echo("修复完成，重新体检：")
    return True
