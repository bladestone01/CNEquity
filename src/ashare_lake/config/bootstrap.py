"""Bootstrap a user config from the packaged example template."""

from __future__ import annotations

import re
import sys
from importlib.resources import files
from pathlib import Path

TEMPLATE_NAME = "ashare-lake.example.toml"
DEFAULT_USER_CONFIG = Path("configs/ashare-lake.toml")


def example_toml_text() -> str:
    """Return the packaged example config (works from PyPI wheels and editable installs)."""
    return (
        files("ashare_lake.config.templates")
        .joinpath(TEMPLATE_NAME)
        .read_text(encoding="utf-8")
    )


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def render_example_toml(
    *,
    data_root: str | None = None,
    platform: str | None = None,
) -> str:
    """Render the example template with optional local tweaks."""
    text = example_toml_text()
    if data_root is not None:
        replaced = re.sub(
            r'(?m)^(\[data\]\s*\nroot\s*=\s*")[^"]*(")',
            rf"\1{_toml_escape(data_root)}\2",
            text,
            count=1,
        )
        if replaced == text:
            raise ValueError("could not patch [data].root in example template")
        text = replaced

    plat = platform if platform is not None else sys.platform
    if plat == "darwin":
        # mootdx + ProcessPool is not fork-safe on macOS; match validate_config.
        text = re.sub(r"(?m)^(workers\s*=\s*)\d+", r"\g<1>1", text, count=1)

    return text


def write_user_config(
    path: Path,
    *,
    data_root: str | None = None,
    force: bool = False,
    platform: str | None = None,
) -> Path:
    """Write a user config file from the packaged example.

    Raises:
        FileExistsError: when ``path`` exists and ``force`` is false.
    """
    path = Path(path)
    if path.exists() and not force:
        raise FileExistsError(
            f"Config already exists: {path}. Re-run with --force to overwrite."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_example_toml(data_root=data_root, platform=platform),
        encoding="utf-8",
    )
    return path
