"""Exclusive file lock per ingestion run (prevents concurrent retry)."""

from __future__ import annotations

import contextlib
import fcntl
from collections.abc import Iterator
from pathlib import Path


class RunLockError(RuntimeError):
    """Another process holds the run lock."""


@contextlib.contextmanager
def run_lock(meta_root: Path, run_id: str, *, blocking: bool = False) -> Iterator[None]:
    """Exclusive lock scoped to *run_id* (or a global name like ``compact``).

    Non-blocking by default (retry contention should fail loud); pass
    ``blocking=True`` to queue instead — e.g. overlapping runs serializing
    their compact step.
    """
    lock_dir = meta_root / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    path = lock_dir / f"{run_id}.lock"
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
