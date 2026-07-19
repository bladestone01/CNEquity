from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class RunRecord:
    run_id: str
    job_name: str
    status: str
    started_at: str
    finished_at: str | None = None
    rows_read: int = 0
    rows_written: int = 0
    error_message: str | None = None
    metadata_json: str = "{}"


@dataclass
class BatchRecord:
    run_id: str
    batch_id: str
    task_id: str
    dataset: str
    status: str
    symbols_json: str = "[]"
    window_start: str | None = None
    window_end: str | None = None
    rows_read: int = 0
    rows_written: int = 0
    retry_count: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    error_message: str | None = None


class Manifest:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        # Concurrent writers (worker processes + engine) need WAL and a
        # bounded wait instead of immediate "database is locked" errors.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS ingestion_runs (
                    run_id TEXT PRIMARY KEY,
                    job_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    rows_read INTEGER DEFAULT 0,
                    rows_written INTEGER DEFAULT 0,
                    error_message TEXT,
                    metadata_json TEXT DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS ingestion_batches (
                    run_id TEXT NOT NULL,
                    batch_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    dataset TEXT NOT NULL,
                    status TEXT NOT NULL,
                    symbols_json TEXT DEFAULT '[]',
                    window_start TEXT,
                    window_end TEXT,
                    rows_read INTEGER DEFAULT 0,
                    rows_written INTEGER DEFAULT 0,
                    retry_count INTEGER DEFAULT 0,
                    started_at TEXT,
                    finished_at TEXT,
                    error_message TEXT,
                    heartbeat_at TEXT,
                    PRIMARY KEY (run_id, batch_id)
                );
                CREATE INDEX IF NOT EXISTS idx_batches_run_status
                    ON ingestion_batches(run_id, status);
                CREATE INDEX IF NOT EXISTS idx_batches_dataset
                    ON ingestion_batches(dataset, status);
                """
            )
            cols = {row[1] for row in conn.execute("PRAGMA table_info(ingestion_batches)")}
            if "heartbeat_at" not in cols:
                conn.execute("ALTER TABLE ingestion_batches ADD COLUMN heartbeat_at TEXT")

    @staticmethod
    def _batch_activity_at(row: sqlite3.Row) -> datetime | None:
        for field in ("heartbeat_at", "started_at"):
            raw = row[field]
            if raw:
                return datetime.fromisoformat(raw)
        return None

    def start_run(self, job_name: str, metadata: dict[str, Any] | None = None) -> str:
        run_id = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO ingestion_runs (run_id, job_name, status, started_at, metadata_json)
                VALUES (?, ?, 'running', ?, ?)
                """,
                (run_id, job_name, _utcnow(), json.dumps(metadata or {})),
            )
        return run_id

    def finish_run(
        self,
        run_id: str,
        status: str,
        rows_read: int = 0,
        rows_written: int = 0,
        error_message: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE ingestion_runs
                SET status = ?, finished_at = ?, rows_read = ?, rows_written = ?, error_message = ?
                WHERE run_id = ?
                """,
                (status, _utcnow(), rows_read, rows_written, error_message, run_id),
            )

    def start_batch(
        self,
        run_id: str,
        batch_id: str,
        task_id: str,
        dataset: str,
        symbols: list[str] | None = None,
        window_start: str | None = None,
        window_end: str | None = None,
    ) -> None:
        now = _utcnow()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO ingestion_batches (
                    run_id, batch_id, task_id, dataset, status, symbols_json,
                    window_start, window_end, started_at, heartbeat_at, retry_count
                ) VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, 0)
                """,
                (
                    run_id,
                    batch_id,
                    task_id,
                    dataset,
                    json.dumps(symbols or []),
                    window_start,
                    window_end,
                    now,
                    now,
                ),
            )

    def touch_batch_heartbeat(self, run_id: str, batch_id: str) -> None:
        """Refresh liveness timestamp for a running batch."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE ingestion_batches
                SET heartbeat_at = ?
                WHERE run_id = ? AND batch_id = ? AND status = 'running'
                """,
                (_utcnow(), run_id, batch_id),
            )

    def finish_batch(
        self,
        run_id: str,
        batch_id: str,
        status: str,
        rows_read: int = 0,
        rows_written: int = 0,
        error_message: str | None = None,
        retry_count: int = 0,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE ingestion_batches
                SET status = ?, finished_at = ?, rows_read = ?, rows_written = ?,
                    error_message = ?, retry_count = ?
                WHERE run_id = ? AND batch_id = ?
                """,
                (
                    status,
                    _utcnow(),
                    rows_read,
                    rows_written,
                    error_message,
                    retry_count,
                    run_id,
                    batch_id,
                ),
            )

    def get_failed_batches(self, run_id: str) -> list[sqlite3.Row]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM ingestion_batches WHERE run_id = ? AND status = 'failed'",
                (run_id,),
            )
            return cur.fetchall()

    def incomplete_batch_counts_by_dataset(self, run_id: str) -> dict[str, int]:
        """Count batches that are not status='success' (failed, running, etc.)."""
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT dataset, COUNT(*) AS cnt
                FROM ingestion_batches
                WHERE run_id = ? AND status != 'success'
                GROUP BY dataset
                """,
                (run_id,),
            )
            return {row["dataset"]: row["cnt"] for row in cur.fetchall()}

    def incomplete_batch_count(self, run_id: str) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM ingestion_batches
                WHERE run_id = ? AND status != 'success'
                """,
                (run_id,),
            )
            return int(cur.fetchone()["cnt"])

    def incomplete_batch_counts_by_status(self, run_id: str) -> dict[str, int]:
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT status, COUNT(*) AS cnt
                FROM ingestion_batches
                WHERE run_id = ? AND status != 'success'
                GROUP BY status
                """,
                (run_id,),
            )
            return {row["status"]: row["cnt"] for row in cur.fetchall()}

    def mark_batch_stale(self, run_id: str, batch_id: str, error_message: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE ingestion_batches
                SET status = 'stale', error_message = ?
                WHERE run_id = ? AND batch_id = ?
                """,
                (error_message, run_id, batch_id),
            )

    def promote_running_to_stale(self, run_id: str, *, stale_after_seconds: float) -> int:
        """Mark running batches with expired heartbeats as stale."""
        cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)
        stale_ids: list[str] = []
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT batch_id, started_at, heartbeat_at
                FROM ingestion_batches
                WHERE run_id = ? AND status = 'running'
                """,
                (run_id,),
            )
            for row in cur:
                activity = self._batch_activity_at(row)
                if activity is None or activity <= cutoff:
                    stale_ids.append(row["batch_id"])
            for batch_id in stale_ids:
                conn.execute(
                    """
                    UPDATE ingestion_batches
                    SET status = 'stale', error_message = ?
                    WHERE run_id = ? AND batch_id = ?
                    """,
                    (
                        "batch heartbeat timed out (worker likely stuck or crashed)",
                        run_id,
                        batch_id,
                    ),
                )
        return len(stale_ids)

    def promote_stale_to_failed(self, run_id: str) -> int:
        """Promote stale batches to failed so retry can pick them up."""
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT batch_id FROM ingestion_batches
                WHERE run_id = ? AND status = 'stale'
                """,
                (run_id,),
            )
            stale_ids = [row["batch_id"] for row in cur]
            for batch_id in stale_ids:
                conn.execute(
                    """
                    UPDATE ingestion_batches
                    SET status = 'failed', finished_at = ?, error_message = ?
                    WHERE run_id = ? AND batch_id = ?
                    """,
                    (
                        _utcnow(),
                        "batch promoted from stale after heartbeat timeout",
                        run_id,
                        batch_id,
                    ),
                )
        return len(stale_ids)

    def advance_batch_timeouts(self, run_id: str, *, stale_after_seconds: float) -> dict[str, int]:
        """Apply running→stale→failed lifecycle for timed-out batches."""
        running_to_stale = self.promote_running_to_stale(
            run_id, stale_after_seconds=stale_after_seconds
        )
        stale_to_failed = self.promote_stale_to_failed(run_id)
        return {
            "running_to_stale": running_to_stale,
            "stale_to_failed": stale_to_failed,
        }

    def reconcile_orphaned_runs(
        self,
        *,
        stale_after_seconds: float = 300,
        error_message: str = "reconciled: worker exited without finish_run",
    ) -> dict[str, int]:
        """Close runs/batches stuck in running/stale with no heartbeat past *stale_after_seconds*."""
        cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)
        runs_closed = 0
        batches_closed = 0
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT run_id, started_at FROM ingestion_runs WHERE status = 'running'"
            )
            orphan_run_ids: list[str] = []
            for row in cur:
                started = datetime.fromisoformat(row["started_at"])
                if started > cutoff:
                    continue
                orphan_run_ids.append(row["run_id"])
            now = _utcnow()
            for run_id in orphan_run_ids:
                conn.execute(
                    """
                    UPDATE ingestion_runs
                    SET status = 'failed', finished_at = ?, error_message = ?
                    WHERE run_id = ?
                    """,
                    (now, error_message, run_id),
                )
                runs_closed += 1
                batch_cur = conn.execute(
                    """
                    SELECT batch_id FROM ingestion_batches
                    WHERE run_id = ? AND status IN ('running', 'stale')
                    """,
                    (run_id,),
                )
                for batch_row in batch_cur:
                    conn.execute(
                        """
                        UPDATE ingestion_batches
                        SET status = 'failed', finished_at = ?, error_message = ?
                        WHERE run_id = ? AND batch_id = ?
                        """,
                        (now, error_message, run_id, batch_row["batch_id"]),
                    )
                    batches_closed += 1
        return {"runs_closed": runs_closed, "batches_closed": batches_closed}

    def mark_stale_running_batches_failed(self, run_id: str, *, stale_after_seconds: float) -> int:
        """Backward-compatible alias: full running→stale→failed promotion."""
        result = self.advance_batch_timeouts(run_id, stale_after_seconds=stale_after_seconds)
        return result["running_to_stale"] + result["stale_to_failed"]

    def demote_success_batches(self, run_id: str, *, reason: str) -> int:
        """Mark fetch batches failed (e.g. staging evicted) so retry refetches them."""
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE ingestion_batches
                SET status = 'failed', error_message = ?
                WHERE run_id = ? AND status = 'success' AND dataset NOT IN
                    ('compact', 'derive_adj_factors', 'audit')
                """,
                (reason, run_id),
            )
            return cur.rowcount

    def get_batches_for_run(self, run_id: str) -> list[sqlite3.Row]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM ingestion_batches WHERE run_id = ? ORDER BY batch_id",
                (run_id,),
            )
            return cur.fetchall()

    def get_run(self, run_id: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM ingestion_runs WHERE run_id = ?", (run_id,)
            ).fetchone()

    def list_runs(self, job_name: str | None = None) -> list[sqlite3.Row]:
        with self._connect() as conn:
            if job_name:
                cur = conn.execute(
                    """
                    SELECT * FROM ingestion_runs WHERE job_name = ?
                    ORDER BY started_at DESC
                    """,
                    (job_name,),
                )
            else:
                cur = conn.execute("SELECT * FROM ingestion_runs ORDER BY started_at DESC")
            return cur.fetchall()

    def latest_incomplete_init_run(self) -> sqlite3.Row | None:
        from ashare_lake.orchestrator.init_phases import init_run_complete

        for run in self.list_runs("init"):
            meta = json.loads(run["metadata_json"] or "{}")
            phases = meta.get("phases") or []
            if not phases:
                continue
            batches = self.get_batches_for_run(run["run_id"])
            if init_run_complete(phases, batches):
                continue
            return run
        return None

    def latest_run(self, job_name: str | None = None) -> sqlite3.Row | None:
        with self._connect() as conn:
            if job_name:
                cur = conn.execute(
                    """
                    SELECT * FROM ingestion_runs WHERE job_name = ?
                    ORDER BY started_at DESC LIMIT 1
                    """,
                    (job_name,),
                )
            else:
                cur = conn.execute("SELECT * FROM ingestion_runs ORDER BY started_at DESC LIMIT 1")
            return cur.fetchone()

    def run_summary(self, run_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            run = conn.execute(
                "SELECT * FROM ingestion_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            batches = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM ingestion_batches WHERE run_id = ? GROUP BY status",
                (run_id,),
            ).fetchall()
        return {
            "run": dict(run) if run else None,
            "batch_counts": {row["status"]: row["cnt"] for row in batches},
        }

    def update_run_metadata(self, run_id: str, metadata: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE ingestion_runs SET metadata_json = ? WHERE run_id = ?",
                (json.dumps(metadata), run_id),
            )

    def get_run_metadata(self, run_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT metadata_json FROM ingestion_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if not row:
            return {}
        return json.loads(row["metadata_json"] or "{}")
