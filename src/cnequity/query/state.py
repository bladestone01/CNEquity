"""Public, cheap dataset identity for downstream caches and artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cnequity.config import Config
from cnequity.domain.datasets import DATASETS
from cnequity.storage.state import StateStore


@dataclass(frozen=True)
class DatasetState:
    dataset: str
    revision: int | None
    revision_id: str | None
    revision_at: str | None
    run_id: str | None
    schema_version: int | None
    contract_fingerprint: str | None
    content_digest: str | None
    revision_receipt: str | None
    changed_partitions: tuple[str, ...]


def dataset_state(
    dataset: str,
    *,
    config: Config | None = None,
    data_root: str | Path | None = None,
) -> DatasetState:
    """Return the latest committed identity for *dataset* without reading Parquet."""
    if dataset not in DATASETS:
        raise ValueError(f"unknown dataset {dataset!r}")
    # Local import avoids a reader -> query package import cycle.
    from cnequity.query.reader import resolve_config

    cfg = resolve_config(config=config, data_root=data_root)
    payload = StateStore(cfg.meta_root).get_payload(dataset)
    revision = payload.get("revision")
    if revision is not None and (
        isinstance(revision, bool) or not isinstance(revision, int) or revision < 1
    ):
        raise ValueError(f"state field {dataset}.revision must be a positive integer")
    partitions = payload.get("changed_partitions") or []
    if not isinstance(partitions, list) or not all(isinstance(item, str) for item in partitions):
        raise ValueError(f"state field {dataset}.changed_partitions must be a list of strings")
    return DatasetState(
        dataset=dataset,
        revision=revision,
        revision_id=payload.get("revision_id"),
        revision_at=payload.get("revision_at"),
        run_id=payload.get("revision_run_id"),
        schema_version=payload.get("schema_version"),
        contract_fingerprint=payload.get("contract_fingerprint"),
        content_digest=payload.get("content_digest"),
        revision_receipt=payload.get("revision_receipt"),
        changed_partitions=tuple(partitions),
    )
