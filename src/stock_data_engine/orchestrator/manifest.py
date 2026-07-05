from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
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
                    PRIMARY KEY (run_id, batch_id)
                );
                CREATE INDEX IF NOT EXISTS idx_batches_run_status
                    ON ingestion_batches(run_id, status);
                CREATE INDEX IF NOT EXISTS idx_batches_dataset
                    ON ingestion_batches(dataset, status);
                """
            )

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
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO ingestion_batches (
                    run_id, batch_id, task_id, dataset, status, symbols_json,
                    window_start, window_end, started_at, retry_count
                ) VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?, 0)
                """,
                (
                    run_id,
                    batch_id,
                    task_id,
                    dataset,
                    json.dumps(symbols or []),
                    window_start,
                    window_end,
                    _utcnow(),
                ),
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

    def failed_batch_counts_by_dataset(self, run_id: str) -> dict[str, int]:
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT dataset, COUNT(*) AS cnt
                FROM ingestion_batches
                WHERE run_id = ? AND status = 'failed'
                GROUP BY dataset
                """,
                (run_id,),
            )
            return {row["dataset"]: row["cnt"] for row in cur.fetchall()}

    def get_batches_for_run(self, run_id: str) -> list[sqlite3.Row]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM ingestion_batches WHERE run_id = ? ORDER BY batch_id",
                (run_id,),
            )
            return cur.fetchall()

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
