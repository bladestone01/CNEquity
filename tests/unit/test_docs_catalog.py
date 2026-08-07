"""The published catalog must list what the registry actually holds.

``docs/datasets/catalog.md`` carries per-dataset prose (主源, 备注) that has no
home in ``DatasetSpec``, so the document is written by hand rather than
generated. That leaves it free to drift: before this guard existed the L7 table
was missing ``flash_news_wire`` and ``economic_calendar``, and ``industry_index``
appeared in no tier table at all.
"""

from __future__ import annotations

import re
from pathlib import Path

from ashare_lake.domain.datasets import DATASETS, datasets_by_tier

CATALOG = Path(__file__).resolve().parents[2] / "docs" / "datasets" / "catalog.md"

_SECTION = re.compile(r"^##\s+(L[0-8])\b")
_ROW = re.compile(r"^\|\s*([a-z][a-z0-9_]*)\s*\|")


def _documented_by_tier() -> dict[str, list[str]]:
    """Dataset names appearing in each ``## L<n>`` section's tables."""
    documented: dict[str, list[str]] = {}
    tier: str | None = None
    for line in CATALOG.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            match = _SECTION.match(line)
            # Any other h2 (采集模式, 主备配置, Step → 数据集映射) closes the
            # tier section; those tables mention datasets too.
            tier = match.group(1) if match else None
            if tier:
                documented.setdefault(tier, [])
            continue
        row = _ROW.match(line)
        if tier and row and row.group(1) in DATASETS:
            documented[tier].append(row.group(1))
    return documented


def test_catalog_tier_tables_match_the_registry():
    documented = _documented_by_tier()
    registered = {tier: names for tier, names in datasets_by_tier().items() if names}

    assert set(documented) == set(registered), "catalog tier sections differ from registry tiers"
    for tier in sorted(registered):
        assert sorted(documented[tier]) == sorted(registered[tier]), (
            f"{tier}: catalog lists {sorted(documented[tier])}, "
            f"registry has {sorted(registered[tier])}"
        )


def test_catalog_lists_every_dataset_exactly_once():
    listed = [name for names in _documented_by_tier().values() for name in names]
    assert len(listed) == len(set(listed)), "a dataset is documented under two tiers"
    assert set(listed) == set(DATASETS)


def test_catalog_header_states_the_registered_count():
    """The intro sentence hard-codes the total; keep it honest."""
    header = CATALOG.read_text(encoding="utf-8").split("---", 1)[0]
    assert f"**{len(DATASETS)} 个注册数据集**" in header


# --- sources.md group labels -------------------------------------------------
# Same failure mode, different file: sources.md tags each dataset with the
# schedule group and start time it runs in. Both drifted — fund_flow was labelled
# core@16:30 and margin_trading signals@17:00 when both run in capital — and one
# of them had been wrong since before the start times moved at all.

SOURCES = Path(__file__).resolve().parents[2] / "docs" / "datasets" / "sources.md"
_GROUP_ROW = re.compile(r"\|\s*分组\s*\|\s*([^|]+?)\s*\|")


def _shipped_step_groups() -> dict[str, str]:
    import sys
    from unittest.mock import patch

    from ashare_lake.config import load_config

    example = Path(__file__).resolve().parents[2] / "configs" / "ashare-lake.example.toml"
    with patch.object(sys, "platform", "linux"):
        cfg = load_config(example)
    return {
        step: f"{name}@{group.at}"
        for name, group in cfg.schedule_groups.items()
        for step in group.steps
        if step != "compact"
    }


def test_sources_group_labels_match_the_shipped_schedule():
    step_groups = _shipped_step_groups()
    mismatches = []
    for block in SOURCES.read_text(encoding="utf-8").split("\n#### ")[1:]:
        heading = block.split("\n", 1)[0].strip()
        row = _GROUP_ROW.search(block)
        if row is None:
            continue
        for dataset in (part.strip() for part in heading.split("/")):
            if dataset in step_groups:
                if row.group(1) != step_groups[dataset]:
                    mismatches.append(
                        f"{dataset}: doc={row.group(1)} config={step_groups[dataset]}"
                    )
                break
    assert mismatches == []
