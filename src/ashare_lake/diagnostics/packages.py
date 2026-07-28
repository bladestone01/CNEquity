"""Runtime package probes and the py_mini_racer collision check.

Every source package is a hard dependency, so a missing one means a damaged
environment rather than a forgotten install flag. The probe still earns its keep
because the failure is silent: the adapters below import their package lazily
inside a function, so a half-uninstalled environment surfaces as thin data on the
next scheduled run, not as an ImportError at startup.

Only lazily-imported packages are listed. `polars`, `duckdb` and friends are
imported at module scope — if one of those is missing, nothing runs at all and
there is no silent failure to catch.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from importlib import metadata
from importlib.util import find_spec
from pathlib import Path


@dataclass(frozen=True)
class RequiredPackage:
    module: str
    purpose: str


REQUIRED_PACKAGES: tuple[RequiredPackage, ...] = (
    RequiredPackage("akshare", "东财未覆盖的宏观序列，ST 标签交叉校验"),
    RequiredPackage("baostock", "估值 / ST / 退市行情的历史回填"),
    RequiredPackage("snownlp", "on-demand stock_news 情绪（[sentiment] use_snownlp）"),
    RequiredPackage("pandas", "申万 / 国证成分历史的 XLS·XLSX 解析"),
    RequiredPackage("openpyxl", "XLSX 解析"),
    RequiredPackage("xlrd", "XLS 解析"),
)


@dataclass(frozen=True)
class PackageStatus:
    package: RequiredPackage
    importable: bool


def _importable(module: str) -> bool:
    """True when ``module`` can be located without executing it.

    ``find_spec`` imports parent packages only, which keeps the probe cheap —
    importing akshare or pandas for real would cost seconds per run.
    """
    try:
        return find_spec(module) is not None
    except (ImportError, ValueError):
        # A half-installed distribution can leave dist-info with no importable
        # package; treat that as missing rather than crashing the report.
        return False


def probe_packages() -> list[PackageStatus]:
    return [PackageStatus(p, _importable(p.module)) for p in REQUIRED_PACKAGES]


# --- py_mini_racer collision -------------------------------------------------
#
# TRANSITIONAL. `akshare` depends on `mini-racer`; the `mootdx` this project used
# to depend on pulled `py-mini-racer`. Two distributions, one shared import
# package: both write into `py_mini_racer/`, installers do not guard against it,
# and the last one wins.
#
# A clean install can no longer reach py-mini-racer, so this only fires for
# environments upgraded from 0.2.x that still carry the old package, or for users
# who installed mootdx themselves. Drop once 0.2.x upgrades are no longer a
# concern.

RACER_PACKAGE = "py_mini_racer"

_NATIVE_SUFFIX: dict[str, str] = {"darwin": ".dylib", "win32": ".dll"}


def _native_suffix(platform: str | None = None) -> str:
    return _NATIVE_SUFFIX.get(platform or sys.platform, ".so")


def racer_providers() -> list[str]:
    """Distributions that install files into the ``py_mini_racer`` import package."""
    prefix = f"{RACER_PACKAGE}/"
    providers: set[str] = set()
    for dist in metadata.distributions():
        name = dist.metadata["Name"] if dist.metadata else None
        if not name:
            continue
        for file in dist.files or ():
            if str(file).startswith(prefix):
                providers.add(name)
                break
    return sorted(providers)


def racer_package_dir() -> Path | None:
    try:
        spec = find_spec(RACER_PACKAGE)
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.submodule_search_locations:
        return None
    return Path(next(iter(spec.submodule_search_locations)))


def racer_repair_commands() -> list[list[str]]:
    """Argv lists that repair the collision, in order. Empty when no installer is usable.

    Returned as argv rather than a shell string on purpose: ``&&`` is a syntax
    error in Windows PowerShell 5.1, and a bare ``pip`` may not be on PATH or may
    belong to a different environment. Both branches below pin the target to the
    interpreter actually running this check, so the repair lands in the right
    environment on macOS, Linux and Windows alike.

    ``uv venv`` creates environments without pip, so ``-m pip`` cannot be assumed;
    fall back to the uv CLI when it is what manages this environment.

    Two steps, not one: the two distributions overlap on ``__init__.py``, so
    removing py-mini-racer also strips files mini-racer needs.
    """
    if _importable("pip"):
        return [
            [sys.executable, "-m", "pip", "uninstall", "-y", "py-mini-racer"],
            [sys.executable, "-m", "pip", "install", "--force-reinstall", "mini-racer"],
        ]

    uv = shutil.which("uv")
    if uv:
        return [
            [uv, "pip", "uninstall", "--python", sys.executable, "py-mini-racer"],
            [uv, "pip", "install", "--python", sys.executable, "--reinstall", "mini-racer"],
        ]

    return []


def racer_native_lib(platform: str | None = None, pkg_dir: Path | None = None) -> Path | None:
    """The V8 binary matching this platform, or None when only foreign ones shipped.

    py-mini-racer 0.6.0 has no arm64 macOS wheel; building its sdist yields a
    package carrying a musl Linux ``.so`` and no ``.dylib``, which imports fine
    and then fails at first use.
    """
    pkg_dir = pkg_dir or racer_package_dir()
    if pkg_dir is None or not pkg_dir.is_dir():
        return None
    suffix = _native_suffix(platform)
    for path in sorted(pkg_dir.iterdir()):
        name = path.name
        if not name.startswith(("libmini_racer", "mini_racer")):
            continue
        # `.muslc.so` must not satisfy a glibc `.so` probe.
        if suffix == ".so" and name.endswith(".muslc.so"):
            continue
        if name.endswith(suffix):
            return path
    return None
