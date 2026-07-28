"""Exclusive file lock per ingestion run (prevents concurrent retry)."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path

from ashare_lake.file_lock import LockUnavailable, exclusive_lock, is_locked


class RunLockError(RuntimeError):
    """Another process holds the run lock."""


def lock_path(meta_root: Path, run_id: str) -> Path:
    return meta_root / "locks" / f"{run_id}.lock"


def is_run_locked(meta_root: Path, run_id: str) -> bool:
    """True when another process currently holds ``run_lock`` for *run_id*."""
    return is_locked(lock_path(meta_root, run_id))


@contextlib.contextmanager
def run_lock(meta_root: Path, run_id: str, *, blocking: bool = False) -> Iterator[None]:
    """Exclusive lock scoped to *run_id* (or a global name like ``compact``).

    Non-blocking by default (retry contention should fail loud); pass
    ``blocking=True`` to queue instead — e.g. overlapping runs serializing
    their compact step.
    """
    path = lock_path(meta_root, run_id)
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(exclusive_lock(path, blocking=blocking))
        except LockUnavailable as exc:
            raise RunLockError(
                f"Run {run_id} is locked by another process; wait for it to finish before retrying."
            ) from exc
        yield
