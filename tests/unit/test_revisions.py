from datetime import date
from pathlib import Path

import polars as pl
import pytest

from cnequity.config import Config
from cnequity.query import dataset_state
from cnequity.storage.parquet import StagingWriter, compact_dataset
from cnequity.storage.revisions import RevisionStore


def _curated_file(root: Path, value: bytes = b"first") -> Path:
    path = root / "daily_bars" / "trade_date=2024-06-18" / "part-merged.parquet"
    path.parent.mkdir(parents=True)
    path.write_bytes(value)
    return path


def test_revision_commit_publishes_receipt_and_state(tmp_path):
    curated = tmp_path / "curated"
    path = _curated_file(curated)
    store = RevisionStore(tmp_path / "meta", curated)

    receipt = store.commit(
        "daily_bars",
        run_id="run-1",
        changed_files=[path],
        schema_version=2,
        contract_fingerprint="contract-sha",
    )

    assert receipt is not None
    assert receipt.revision == 1
    assert receipt.changed_partitions == ("daily_bars/trade_date=2024-06-18",)
    assert receipt.files[0].path == "daily_bars/trade_date=2024-06-18/part-merged.parquet"
    assert receipt.files[0].size_bytes == len(b"first")
    state = store.state.get_payload("daily_bars")
    assert state["revision"] == 1
    assert state["revision_id"] == receipt.revision_id
    assert state["content_digest"] == receipt.content_digest
    assert store.latest("daily_bars") == receipt


def test_revision_increments_when_an_old_partition_changes(tmp_path):
    curated = tmp_path / "curated"
    path = _curated_file(curated)
    store = RevisionStore(tmp_path / "meta", curated)
    first = store.commit(
        "daily_bars",
        run_id="run-1",
        changed_files=[path],
        schema_version=1,
        contract_fingerprint="contract-sha",
    )
    path.write_bytes(b"historical repair")
    second = store.commit(
        "daily_bars",
        run_id="run-2",
        changed_files=[path],
        schema_version=1,
        contract_fingerprint="contract-sha",
    )

    assert first is not None and second is not None
    assert second.revision == first.revision + 1
    assert second.content_digest != first.content_digest


def test_revision_does_not_advance_without_changed_files(tmp_path):
    store = RevisionStore(tmp_path / "meta", tmp_path / "curated")
    assert (
        store.commit(
            "daily_bars",
            run_id="run-1",
            changed_files=[],
            schema_version=1,
            contract_fingerprint="contract-sha",
        )
        is None
    )
    assert store.state.get_revision("daily_bars") is None


def test_revision_rejects_files_outside_curated_root(tmp_path):
    outside = tmp_path / "outside.parquet"
    outside.write_bytes(b"data")
    store = RevisionStore(tmp_path / "meta", tmp_path / "curated")
    with pytest.raises(ValueError, match="outside curated root"):
        store.commit(
            "daily_bars",
            run_id="run-1",
            changed_files=[outside],
            schema_version=1,
            contract_fingerprint="contract-sha",
        )


def test_compact_reports_only_physical_dataset_changes(tmp_path):
    staging = tmp_path / "staging"
    curated = tmp_path / "curated"
    writer = StagingWriter(staging)
    row = pl.DataFrame(
        {
            "symbol": ["600000.SH"],
            "trade_date": [date(2024, 6, 18)],
            "open": [10.0],
            "high": [10.5],
            "low": [9.8],
            "close": [10.2],
            "volume": [100.0],
            "amount": [1020.0],
            "source": ["test"],
            "data_version": ["v1"],
            "fetched_at": ["2024-06-18T10:00:00+00:00"],
        }
    )
    writer.write_batch("daily_bars", "run-1", "batch-1", row)
    first_changes: list[Path] = []
    compact_dataset(staging, curated, "daily_bars", "run-1", changed_files=first_changes)
    assert len(first_changes) == 1

    writer.write_batch("daily_bars", "run-2", "batch-1", row)
    second_changes: list[Path] = []
    compact_dataset(staging, curated, "daily_bars", "run-2", changed_files=second_changes)
    assert second_changes == []


def test_public_dataset_state_reads_committed_identity(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    path = _curated_file(cfg.curated_root)
    receipt = RevisionStore(cfg.meta_root, cfg.curated_root).commit(
        "daily_bars",
        run_id="run-1",
        changed_files=[path],
        schema_version=3,
        contract_fingerprint="contract-sha",
    )
    assert receipt is not None

    state = dataset_state("daily_bars", config=cfg)
    assert state.revision == 1
    assert state.revision_id == receipt.revision_id
    assert state.schema_version == 3
    assert state.contract_fingerprint == "contract-sha"
    assert state.changed_partitions == ("daily_bars/trade_date=2024-06-18",)
