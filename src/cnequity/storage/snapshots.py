"""Portable, immutable lake snapshots with checksummed restore support."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from cnequity.config import Config
from cnequity.domain.contracts import contract_fingerprint, dataset_contract
from cnequity.domain.datasets import DATASETS
from cnequity.provenance import runtime_lineage
from cnequity.storage.atomic import write_json_atomic
from cnequity.storage.state import StateStore

_SNAPSHOT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"unsafe snapshot path: {raw}")
    return path


@dataclass(frozen=True)
class SnapshotFile:
    dataset: str
    layer: str
    path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class SnapshotVerification:
    snapshot: str
    passed: bool
    verified_files: int
    missing: tuple[str, ...]
    mismatched: tuple[str, ...]


class SnapshotStore:
    """Create and restore snapshots below ``meta/snapshots`` or an explicit root."""

    def __init__(self, config: Config, snapshot_root: Path | None = None):
        self.config = config
        self.root = Path(snapshot_root or config.meta_root / "snapshots")

    @staticmethod
    def _validate_name(name: str) -> str:
        if not _SNAPSHOT_NAME.fullmatch(name):
            raise ValueError(
                "snapshot name must be 1-80 characters using letters, digits, '.', '_' or '-'"
            )
        return name

    def path(self, name: str) -> Path:
        return self.root / self._validate_name(name)

    def _source_root(self, dataset: str) -> tuple[str, Path]:
        spec = DATASETS[dataset]
        base = self.config.derived_root if spec.layer == "derived" else self.config.curated_root
        return spec.layer, base / dataset

    def create(self, name: str, datasets: list[str]) -> Path:
        """Copy selected datasets into a new immutable snapshot directory."""
        selected = sorted(set(datasets))
        if not selected:
            raise ValueError("at least one dataset is required")
        unknown = sorted(set(selected) - set(DATASETS))
        if unknown:
            raise ValueError(f"unknown dataset(s): {', '.join(unknown)}")
        destination = self.path(name)
        if destination.exists():
            raise FileExistsError(f"snapshot already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = Path(tempfile.mkdtemp(prefix=f".{name}-", dir=destination.parent))
        state = StateStore(self.config.meta_root)
        dataset_states: dict[str, dict] = {}
        records: list[SnapshotFile] = []
        try:
            dataset_states = {dataset: state.get_payload(dataset) for dataset in selected}
            for dataset in selected:
                layer, source = self._source_root(dataset)
                if not source.is_dir():
                    raise FileNotFoundError(f"dataset has no stored files: {dataset}")
                files = sorted(source.rglob("*.parquet"))
                if not files:
                    raise FileNotFoundError(f"dataset has no parquet files: {dataset}")
                for source_file in files:
                    relative = source_file.relative_to(source)
                    stored = temp / "data" / layer / dataset / relative
                    stored.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source_file, stored)
                    records.append(
                        SnapshotFile(
                            dataset=dataset,
                            layer=layer,
                            path=(Path("data") / layer / dataset / relative).as_posix(),
                            size_bytes=stored.stat().st_size,
                            sha256=_sha256(stored),
                        )
                    )

            meta_root = self.config.meta_root.resolve()
            for dataset, payload in dataset_states.items():
                receipt = payload.get("revision_receipt")
                if not receipt:
                    continue
                receipt_source = (meta_root / _safe_relative(str(receipt))).resolve()
                try:
                    receipt_relative = receipt_source.relative_to(meta_root)
                except ValueError as exc:
                    raise ValueError(f"revision receipt is outside meta root: {receipt}") from exc
                if not receipt_source.is_file():
                    raise FileNotFoundError(
                        f"dataset state references missing revision receipt: {receipt_source}"
                    )
                stored = temp / "meta" / receipt_relative
                stored.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(receipt_source, stored)
                records.append(
                    SnapshotFile(
                        dataset=dataset,
                        layer="meta",
                        path=(Path("meta") / receipt_relative).as_posix(),
                        size_bytes=stored.stat().st_size,
                        sha256=_sha256(stored),
                    )
                )

            manifest = {
                "format": "cnequity.lake-snapshot",
                "format_version": 1,
                "name": name,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "datasets": selected,
                "dataset_states": dataset_states,
                "contracts": {
                    dataset: {
                        "schema_version": dataset_contract(dataset)["schema_version"],
                        "fingerprint": contract_fingerprint(dataset),
                    }
                    for dataset in selected
                },
                "lineage": runtime_lineage(self.config),
                "files": [asdict(item) for item in records],
            }
            write_json_atomic(temp / "manifest.json", manifest, indent=2, ensure_ascii=False)
            os.replace(temp, destination)
        except Exception:
            shutil.rmtree(temp, ignore_errors=True)
            raise
        return destination / "manifest.json"

    def _manifest(self, name: str) -> tuple[Path, dict]:
        snapshot = self.path(name)
        manifest = snapshot / "manifest.json"
        if not manifest.is_file():
            raise FileNotFoundError(manifest)
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        if payload.get("format") != "cnequity.lake-snapshot" or payload.get("format_version") != 1:
            raise ValueError(f"unsupported snapshot manifest: {manifest}")
        return snapshot, payload

    def verify(self, name: str) -> SnapshotVerification:
        snapshot, manifest = self._manifest(name)
        missing: list[str] = []
        mismatched: list[str] = []
        verified = 0
        for record in manifest.get("files", []):
            relative = _safe_relative(str(record["path"]))
            path = snapshot / relative
            if not path.is_file():
                missing.append(relative.as_posix())
                continue
            if (
                path.stat().st_size != int(record["size_bytes"])
                or _sha256(path) != record["sha256"]
            ):
                mismatched.append(relative.as_posix())
                continue
            verified += 1
        return SnapshotVerification(
            snapshot=name,
            passed=not missing and not mismatched and verified == len(manifest.get("files", [])),
            verified_files=verified,
            missing=tuple(missing),
            mismatched=tuple(mismatched),
        )

    def restore(self, name: str, target_data_root: Path) -> Path:
        """Restore into a new or empty explicit target; existing data is never overwritten."""
        verification = self.verify(name)
        if not verification.passed:
            raise ValueError(
                f"snapshot verification failed: missing={verification.missing}, "
                f"mismatched={verification.mismatched}"
            )
        target = Path(target_data_root).resolve()
        if target == self.config.data_root.resolve():
            raise ValueError("restore target must not be the active data root")
        if target.exists() and any(target.iterdir()):
            raise FileExistsError(f"restore target is not empty: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = Path(tempfile.mkdtemp(prefix=f".{target.name}-restore-", dir=target.parent))
        snapshot, manifest = self._manifest(name)
        try:
            for record in manifest["files"]:
                relative = _safe_relative(str(record["path"]))
                if relative.parts[0] == "data":
                    # Stored data paths include a leading data/ while the
                    # restore root itself is the lake's data directory.
                    restored_relative = Path(*relative.parts[1:])
                elif relative.parts[0] == "meta":
                    restored_relative = relative
                else:
                    raise ValueError(f"unsupported snapshot file root: {relative}")
                destination = temp / restored_relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(snapshot / relative, destination)
            meta = temp / "meta"
            meta.mkdir(parents=True, exist_ok=True)
            for dataset, state in manifest.get("dataset_states", {}).items():
                if state:
                    write_json_atomic(
                        meta / "state" / f"{dataset}.json",
                        state,
                        indent=2,
                        ensure_ascii=False,
                    )
            write_json_atomic(
                meta / "restored-snapshot.json", manifest, indent=2, ensure_ascii=False
            )
            if target.exists():
                target.rmdir()
            os.replace(temp, target)
        except Exception:
            shutil.rmtree(temp, ignore_errors=True)
            raise
        return target
