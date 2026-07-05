"""Compact eligibility: skip datasets with failed batches in the current run."""

from __future__ import annotations

from stock_data_engine.orchestrator.manifest import Manifest


def datasets_with_failed_batches(manifest: Manifest, run_id: str) -> frozenset[str]:
    """Return dataset names that still have failed batches for *run_id*."""
    counts = manifest.failed_batch_counts_by_dataset(run_id)
    return frozenset(ds for ds, n in counts.items() if n > 0)


def compact_allowed(manifest: Manifest, run_id: str, dataset: str) -> tuple[bool, int]:
    """Return (allowed, failed_batch_count) for compacting *dataset* in *run_id*."""
    failed = manifest.failed_batch_counts_by_dataset(run_id).get(dataset, 0)
    return failed == 0, failed
