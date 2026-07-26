"""Exclusive file lock per ingestion run (prevents concurrent retry)."""

from __future__ import annotations

import contextlib
import fcntl
from collections.abc import Iterator
from pathlib import Path


class RunLockError(RuntimeError):
    """Another process holds the run lock."""


def lock_path(meta_root: Path, run_id: str) -> Path:
    return meta_root / "locks" / f"{run_id}.lock"


def is_run_locked(meta_root: Path, run_id: str) -> bool:
    """True when another process currently holds ``run_lock`` for *run_id*."""
    path = lock_path(meta_root, run_id)
    if not path.exists():
        return False
    with open(path, "a") as lock_f:
        try:
            fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(lock_f, fcntl.LOCK_UN)
        return False


@contextlib.contextmanager
def run_lock(meta_root: Path, run_id: str, *, blocking: bool = False) -> Iterator[None]:
    """Exclusive lock scoped to *run_id* (or a global name like ``compact``).

    Non-blocking by default (retry contention should fail loud); pass
    ``blocking=True`` to queue instead — e.g. overlapping runs serializing
    their compact step.
    """
    path = lock_path(meta_root, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as lock_f:
        flags = fcntl.LOCK_EX if blocking else (fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            fcntl.flock(lock_f, flags)
        except BlockingIOError as exc:
            raise RunLockError(
                f"Run {run_id} is locked by another process; wait for it to finish before retrying."
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)
