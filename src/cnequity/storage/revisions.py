"""Committed dataset revisions and immutable change receipts.

Coverage watermarks answer how far a dataset reaches; they do not identify its
contents.  A repair to an old partition therefore needs a separate, monotonic
revision that downstream caches and research artifacts can pin.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cnequity.storage.atomic import write_json_atomic
from cnequity.storage.state import StateStore


def sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 digest for *path*."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class RevisionFile:
    """One curated file changed by a committed dataset mutation."""

    path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class DatasetRevision:
    """Immutable receipt for one committed dataset generation."""

    dataset: str
    revision: int
    revision_id: str
    committed_at: str
    run_id: str
    schema_version: int
    contract_fingerprint: str
    content_digest: str
    changed_partitions: tuple[str, ...]
    files: tuple[RevisionFile, ...]
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RevisionStore:
    """Publish revision receipts and advance dataset state under one lock."""

    def __init__(self, meta_root: Path, curated_root: Path):
        self.meta_root = Path(meta_root)
        self.curated_root = Path(curated_root).resolve()
        self.root = self.meta_root / "revisions"
        self.root.mkdir(parents=True, exist_ok=True)
        self.state = StateStore(self.meta_root)

    def _relative_file(self, path: Path) -> Path:
        resolved = path.resolve()
        try:
            return resolved.relative_to(self.curated_root)
        except ValueError as exc:
            raise ValueError(f"revision file is outside curated root: {path}") from exc

    def _file_records(self, files: list[Path]) -> tuple[RevisionFile, ...]:
        records: list[RevisionFile] = []
        for path in sorted({Path(item) for item in files}, key=lambda item: str(item)):
            if not path.is_file():
                raise FileNotFoundError(path)
            relative = self._relative_file(path)
            records.append(
                RevisionFile(
                    path=relative.as_posix(),
                    size_bytes=path.stat().st_size,
                    sha256=sha256_file(path),
                )
            )
        return tuple(records)

    @staticmethod
    def _content_digest(
        dataset: str,
        schema_version: int,
        contract_fingerprint: str,
        files: tuple[RevisionFile, ...],
    ) -> str:
        payload = {
            "dataset": dataset,
            "schema_version": schema_version,
            "contract_fingerprint": contract_fingerprint,
            "files": [asdict(item) for item in files],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def commit(
        self,
        dataset: str,
        *,
        run_id: str,
        changed_files: list[Path],
        schema_version: int,
        contract_fingerprint: str,
        metadata: dict[str, Any] | None = None,
    ) -> DatasetRevision | None:
        """Commit a new revision, or return ``None`` when no file changed.

        The receipt is written before state is advanced.  If a process crashes
        between those atomic writes, the receipt is an unreferenced recovery
        artifact and the next commit safely reuses the still-uncommitted integer.
        """
        if schema_version < 1:
            raise ValueError("schema_version must be a positive integer")
        if not contract_fingerprint:
            raise ValueError("contract_fingerprint must not be empty")
        files = self._file_records(changed_files)
        if not files:
            return None

        with self.state.transaction(dataset) as state:
            current = state.get("revision", 0)
            if isinstance(current, bool) or not isinstance(current, int) or current < 0:
                raise ValueError(f"state field {dataset}.revision must be a non-negative integer")
            revision = current + 1
            committed_at = datetime.now(timezone.utc).isoformat()
            revision_id = uuid.uuid4().hex
            partitions = tuple(sorted({str(Path(item.path).parent) for item in files}))
            receipt = DatasetRevision(
                dataset=dataset,
                revision=revision,
                revision_id=revision_id,
                committed_at=committed_at,
                run_id=run_id,
                schema_version=schema_version,
                contract_fingerprint=contract_fingerprint,
                content_digest=self._content_digest(
                    dataset, schema_version, contract_fingerprint, files
                ),
                changed_partitions=partitions,
                files=files,
                metadata=dict(metadata or {}),
            )
            receipt_path = self.root / dataset / f"{revision:08d}-{revision_id}.json"
            write_json_atomic(receipt_path, receipt.to_dict(), indent=2)

            state.update(
                {
                    "revision": revision,
                    "revision_id": revision_id,
                    "revision_at": committed_at,
                    "revision_run_id": run_id,
                    "schema_version": schema_version,
                    "contract_fingerprint": contract_fingerprint,
                    "content_digest": receipt.content_digest,
                    "revision_receipt": str(receipt_path.relative_to(self.meta_root)),
                    "changed_partitions": list(partitions),
                    "updated_at": committed_at,
                }
            )
        return receipt

    def latest(self, dataset: str) -> DatasetRevision | None:
        """Read the receipt referenced by the current committed state."""
        state = self.state.get_payload(dataset)
        relative = state.get("revision_receipt")
        if not relative:
            return None
        path = self.meta_root / str(relative)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["files"] = tuple(RevisionFile(**item) for item in payload.get("files", []))
        payload["changed_partitions"] = tuple(payload.get("changed_partitions", []))
        return DatasetRevision(**payload)
