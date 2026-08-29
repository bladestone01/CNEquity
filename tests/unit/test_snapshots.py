import json
from datetime import date, datetime, timezone
from pathlib import Path

import polars as pl
import pytest

from cnequity.config import Config
from cnequity.storage.snapshots import SnapshotStore
from cnequity.storage.state import StateStore


def _write_bars(cfg: Config) -> None:
    path = cfg.curated_root / "daily_bars" / "trade_date=2024-06-18"
    path.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["600000.SH"],
            "trade_date": [date(2024, 6, 18)],
            "close": [10.0],
            "fetched_at": [datetime(2024, 6, 18, tzinfo=timezone.utc)],
        }
    ).write_parquet(path / "part-merged.parquet")


def test_snapshot_create_verify_and_restore(tmp_path):
    cfg = Config(data_root=tmp_path / "lake")
    _write_bars(cfg)
    StateStore(cfg.meta_root).set_date("daily_bars", date(2024, 6, 18))
    store = SnapshotStore(cfg)
    manifest = store.create("research-2024", ["daily_bars"])
    assert manifest.exists()
    verification = store.verify("research-2024")
    assert verification.passed and verification.verified_files == 1

    restored = store.restore("research-2024", tmp_path / "restored")
    file = restored / "curated" / "daily_bars" / "trade_date=2024-06-18" / "part-merged.parquet"
    assert file.exists()
    assert pl.read_parquet(file)["close"].to_list() == [10.0]
    assert (restored / "meta" / "restored-snapshot.json").exists()
    assert StateStore(restored / "meta").get_date("daily_bars") == date(2024, 6, 18)


def test_snapshot_detects_tampering_and_refuses_restore(tmp_path):
    cfg = Config(data_root=tmp_path / "lake")
    _write_bars(cfg)
    store = SnapshotStore(cfg)
    store.create("tampered", ["daily_bars"])
    stored = next((store.path("tampered") / "data").rglob("*.parquet"))
    stored.write_bytes(b"damaged")
    assert not store.verify("tampered").passed
    with pytest.raises(ValueError, match="verification failed"):
        store.restore("tampered", tmp_path / "restored")


def test_restore_refuses_active_or_nonempty_target(tmp_path):
    cfg = Config(data_root=tmp_path / "lake")
    _write_bars(cfg)
    store = SnapshotStore(cfg)
    store.create("safe", ["daily_bars"])
    with pytest.raises(ValueError, match="active data root"):
        store.restore("safe", cfg.data_root)
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "keep.txt").write_text("user data")
    with pytest.raises(FileExistsError, match="not empty"):
        store.restore("safe", target)
    assert (target / "keep.txt").read_text() == "user data"


def test_snapshot_rejects_unknown_dataset_and_duplicate_name(tmp_path):
    cfg = Config(data_root=tmp_path / "lake")
    _write_bars(cfg)
    store = SnapshotStore(cfg)
    with pytest.raises(ValueError, match="unknown dataset"):
        store.create("bad", ["not-a-dataset"])
    store.create("once", ["daily_bars"])
    with pytest.raises(FileExistsError, match="already exists"):
        store.create("once", ["daily_bars"])


def test_snapshot_copies_and_restores_referenced_revision_receipt(tmp_path):
    cfg = Config(data_root=tmp_path / "lake")
    _write_bars(cfg)
    receipt_relative = Path("revisions/daily_bars/00000001-test.json")
    receipt = cfg.meta_root / receipt_relative
    receipt.parent.mkdir(parents=True)
    receipt.write_text(
        json.dumps(
            {
                "dataset": "daily_bars",
                "revision": 1,
                "revision_id": "test",
                "committed_at": "2026-08-29T00:00:00+00:00",
                "run_id": "run-test",
                "schema_version": 1,
                "contract_fingerprint": "contract-test",
                "content_digest": "content-test",
                "changed_partitions": [],
                "files": [],
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )
    state = StateStore(cfg.meta_root)
    state._write_payload(
        state._path("daily_bars"),
        {
            "revision": 1,
            "revision_id": "test",
            "revision_receipt": receipt_relative.as_posix(),
        },
    )

    store = SnapshotStore(cfg)
    store.create("with-revision", ["daily_bars"])
    restored = store.restore("with-revision", tmp_path / "restored")

    restored_receipt = restored / "meta" / receipt_relative
    assert restored_receipt.is_file()
    assert json.loads(restored_receipt.read_text())["revision_id"] == "test"


def test_snapshot_rejects_traversal_in_manifest(tmp_path):
    cfg = Config(data_root=tmp_path / "lake")
    _write_bars(cfg)
    store = SnapshotStore(cfg)
    manifest_path = store.create("unsafe", ["daily_bars"])
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][0]["path"] = "../../outside.parquet"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="unsafe snapshot path"):
        store.verify("unsafe")
    with pytest.raises(ValueError, match="unsafe snapshot path"):
        store.restore("unsafe", tmp_path / "restored")
