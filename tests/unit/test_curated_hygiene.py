"""Stray directories under the curated layer must not go unnoticed."""

import polars as pl

from ashare_lake.config import Config
from ashare_lake.quality.audit import _unregistered_curated_dirs


def _mkdataset(cfg: Config, name: str) -> None:
    part = cfg.curated_root / name / "trade_date=2026-07-21"
    part.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"symbol": ["600519.SH"]}).write_parquet(part / "part-merged.parquet")


def test_clean_curated_layer_has_no_findings(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    _mkdataset(cfg, "daily_bars")
    _mkdataset(cfg, "corporate_actions")

    assert _unregistered_curated_dirs(cfg) == []


def test_missing_curated_root_is_not_a_finding(tmp_path):
    assert _unregistered_curated_dirs(Config(data_root=tmp_path / "data")) == []


def test_flags_a_backup_directory_left_inside_curated(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    _mkdataset(cfg, "corporate_actions")
    _mkdataset(cfg, "corporate_actions.bak.20260709T122646Z")

    findings = _unregistered_curated_dirs(cfg)

    assert len(findings) == 1
    assert findings[0]["check"] == "unregistered_curated_dir"
    assert findings[0]["severity"] == "warning"
    assert findings[0]["stray_dirs"] == ["corporate_actions.bak.20260709T122646Z"]


def test_lists_every_stray_dir_and_counts_them(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    _mkdataset(cfg, "daily_bars")
    for name in ("old_bars", "scratch", "daily_bars.old"):
        _mkdataset(cfg, name)

    finding = _unregistered_curated_dirs(cfg)[0]

    assert finding["stray_count"] == 3
    assert set(finding["stray_dirs"]) == {"old_bars", "scratch", "daily_bars.old"}
