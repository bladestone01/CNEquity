"""Shared TDX/mootdx session primitives (lock + teardown)."""

from __future__ import annotations

import threading

# mootdx/tdxpy is not thread-safe (heartbeat thread + pyarrow/pandas in xdxr).
# All Quotes sessions must be serialized across orchestrator parallel steps.
TDX_SESSION_LOCK = threading.Lock()


def close_quotes_client(client: object) -> None:
    """Close a mootdx client so its heartbeat thread dies (else the process
    can't exit — a serial daily run creates one client per fetch)."""
    if client is None:
        return
    inner = getattr(client, "client", None)
    close = getattr(inner, "close", None) or getattr(client, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass
