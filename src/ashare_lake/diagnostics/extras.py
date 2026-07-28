"""Source-package inventory and import probes.

These packages used to be optional extras; they are hard dependencies now, so a
missing one means a broken or partially-uninstalled environment rather than a
forgotten install flag. The probes stay because the failure is still silent:
every adapter below imports its package lazily inside a function, so a broken
install surfaces as thin data at 3am, not as an ImportError at startup.

The useful distinction is not installed/missing but what the adapter does when
the import fails. A step that raises is loud — the batch fails and the manifest
records it. A step that falls back to a narrower source keeps succeeding while
writing less, and nothing in `asl status` says so. Every entry below was checked
against its adapter call site, because those imports are function-local and a
module-level grep cannot tell a sole source from a supplement.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from enum import StrEnum
from importlib import metadata
from importlib.util import find_spec
from pathlib import Path


class Impact(StrEnum):
    """What the absence does to one step."""

    BLOCKS = "blocks"  # step raises; visible in the manifest
    EMPTIES = "empties"  # sole source; step "succeeds" writing zero rows
    REDUCES = "reduces"  # supplementary source; step works, coverage narrows


class Scope(StrEnum):
    """Which code path reaches for the dependency."""

    DAILY = "daily"  # a step in a configured daily group
    BACKFILL = "backfill"  # `asl init` / `--backfill` history paths
    ON_DEMAND = "on_demand"  # `asl query` on-demand fetches


@dataclass(frozen=True)
class Use:
    step: str
    impact: Impact
    scope: Scope
    note: str = ""


@dataclass(frozen=True)
class Extra:
    name: str
    modules: tuple[str, ...]
    uses: tuple[Use, ...]
    summary: str

    def daily_uses(self) -> tuple[Use, ...]:
        return tuple(u for u in self.uses if u.scope is Scope.DAILY)


EXTRAS: tuple[Extra, ...] = (
    Extra(
        name="macro",
        modules=("akshare",),
        summary="AkShare macro series and ST labels — supplements EastMoney",
        uses=(
            Use(
                "macro_indicators",
                Impact.REDUCES,
                Scope.DAILY,
                note="EastMoney is primary; AkShare only adds series EM does not return",
            ),
            Use(
                "trading_status",
                Impact.REDUCES,
                Scope.DAILY,
                note="supplements EM's ST list for robustness; EM alone still writes rows",
            ),
        ),
    ),
    Extra(
        name="nlp",
        modules=("snownlp",),
        summary="SnowNLP sentiment scoring — on-demand stock_news only",
        uses=(
            Use(
                "stock_news",
                Impact.REDUCES,
                Scope.ON_DEMAND,
                note="only when [sentiment] use_snownlp = true; otherwise the keyword lexicon is used",
            ),
        ),
    ),
    Extra(
        name="valuation",
        modules=("baostock",),
        summary="Baostock history — valuation, ST labels, delisted bars",
        uses=(
            Use("valuation_metrics", Impact.BLOCKS, Scope.BACKFILL),
            Use("trading_status", Impact.BLOCKS, Scope.BACKFILL, note="ST history backfill"),
            Use("daily_bars_delisted", Impact.BLOCKS, Scope.BACKFILL),
            Use("instruments", Impact.REDUCES, Scope.BACKFILL, note="delisted symbol merge"),
        ),
    ),
    Extra(
        name="structure",
        modules=("pandas", "openpyxl", "xlrd"),
        summary="Shenwan / CNI constituent spreadsheets (XLS/XLSX parsing)",
        uses=(
            Use("index_constituents", Impact.BLOCKS, Scope.DAILY),
            Use("industry_members", Impact.BLOCKS, Scope.DAILY),
        ),
    ),
)

EXTRAS_BY_NAME: dict[str, Extra] = {e.name: e for e in EXTRAS}

# Config [sources.*] toggles backed by a package that can fail to import.
SOURCE_REQUIREMENTS: dict[str, str] = {
    "akshare": "macro",
    "baostock": "valuation",
}


@dataclass(frozen=True)
class ExtraStatus:
    extra: Extra
    missing: tuple[str, ...]

    @property
    def installed(self) -> bool:
        return not self.missing

    @property
    def install_hint(self) -> str:
        # No extras any more — a missing package means the install is damaged.
        return "pip install --force-reinstall ashare-lake"


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


def probe_extras() -> list[ExtraStatus]:
    return [
        ExtraStatus(extra=e, missing=tuple(m for m in e.modules if not _importable(m)))
        for e in EXTRAS
    ]


def daily_impacts(statuses: list[ExtraStatus]) -> dict[str, list[tuple[ExtraStatus, Use]]]:
    """Map step name → (missing extra, how it degrades) for daily-scope uses."""
    impact: dict[str, list[tuple[ExtraStatus, Use]]] = {}
    for status in statuses:
        if status.installed:
            continue
        for use in status.extra.daily_uses():
            impact.setdefault(use.step, []).append((status, use))
    return impact


# --- py_mini_racer collision -------------------------------------------------
#
# `akshare` depends on `mini-racer`; `mootdx` depended on `py-mini-racer`. Two
# distributions, one shared import package: both write into `py_mini_racer/`,
# installers do not guard against it, and the last one wins.
#
# Dropping mootdx for the vendored wire client removed this project's only route
# to py-mini-racer, so a clean install can no longer hit the collision. The check
# stays because an environment upgraded from an older release still carries the
# old package, and because a user may install mootdx for their own reasons.

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
    removing py-mini-racer also strips files mini-racer needs. mootdx keeps
    working without py-mini-racer — its only consumer is ``utils/holiday.py``,
    which nothing inside mootdx imports.
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
