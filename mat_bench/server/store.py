"""Persistent SQLite-backed stores for tokens and sessions.

Two databases under a shared store directory (default: ~/.matbench/):

    tokens.db   — API tokens
    sessions.db — sessions, session counter, and evaluation results
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path


class TokenStore:
    """Thread-safe SQLite store for API tokens."""

    _CREATE = """
    CREATE TABLE IF NOT EXISTS tokens (
        token      TEXT PRIMARY KEY,
        created_at TEXT NOT NULL
    );
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(self._CREATE)
        self._conn.commit()

    def load_tokens(self) -> dict[str, dict]:
        """Return all tokens as {token_str: {"token": ..., "created_at": ...}}."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT token, created_at FROM tokens"
            ).fetchall()
        return {r[0]: {"token": r[0], "created_at": r[1]} for r in rows}

    def save_token(self, token: str, created_at: datetime) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO tokens(token, created_at) VALUES (?, ?)",
                (token, created_at.isoformat()),
            )
            self._conn.commit()


class SessionStore:
    """Thread-safe SQLite store for sessions and evaluation results."""

    _CREATE = """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        token      TEXT NOT NULL,
        model_name TEXT NOT NULL DEFAULT 'unknown',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS session_counter (
        id      INTEGER PRIMARY KEY CHECK (id = 1),
        counter INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS results (
        key         TEXT PRIMARY KEY,
        token       TEXT NOT NULL,
        session_id  TEXT NOT NULL,
        record_json TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS task_starts (
        session_id  TEXT NOT NULL,
        question_id TEXT NOT NULL,
        started_at  TEXT NOT NULL,
        PRIMARY KEY (session_id, question_id)
    );

    CREATE TABLE IF NOT EXISTS runs (
        run_id      TEXT PRIMARY KEY,
        token       TEXT NOT NULL,
        session_id  TEXT NOT NULL,
        run_json    TEXT NOT NULL,
        created_at  TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS run_task_starts (
        run_id      TEXT NOT NULL,
        question_id TEXT NOT NULL,
        started_at  TEXT NOT NULL,
        PRIMARY KEY (run_id, question_id)
    );

    CREATE TABLE IF NOT EXISTS attempts (
        attempt_id      TEXT PRIMARY KEY,
        token           TEXT NOT NULL,
        session_id      TEXT NOT NULL,
        run_id          TEXT NOT NULL,
        question_id     TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        status          TEXT NOT NULL,
        created_at      TEXT NOT NULL,
        UNIQUE (run_id, question_id, idempotency_key)
    );

    CREATE TABLE IF NOT EXISTS grading_jobs (
        job_id      TEXT PRIMARY KEY,
        attempt_id  TEXT NOT NULL UNIQUE,
        token       TEXT NOT NULL,
        session_id  TEXT NOT NULL,
        run_id      TEXT NOT NULL,
        question_id TEXT NOT NULL,
        status      TEXT NOT NULL,
        error       TEXT NOT NULL DEFAULT '',
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    );
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(self._CREATE)
        self._conn.execute(
            "INSERT OR IGNORE INTO session_counter(id, counter) VALUES (1, 0)"
        )
        # Migrate existing DBs that predate the model_name column
        existing = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(sessions)").fetchall()
        }
        if "model_name" not in existing:
            self._conn.execute(
                "ALTER TABLE sessions ADD COLUMN model_name TEXT NOT NULL DEFAULT 'unknown'"
            )
        self._conn.commit()

    def load_sessions(self) -> dict[str, dict]:
        """Return sessions as {session_id: {...}}."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT session_id, token, model_name, created_at FROM sessions"
            ).fetchall()
        return {
            r[0]: {"session_id": r[0], "token": r[1], "model_name": r[2], "created_at": r[3]}
            for r in rows
        }

    def save_session(
        self,
        session_id: str,
        token: str,
        model_name: str,
        created_at: datetime,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO sessions(session_id, token, model_name, created_at)"
                " VALUES (?, ?, ?, ?)",
                (session_id, token, model_name, created_at.isoformat()),
            )
            self._conn.commit()

    def load_results(self) -> dict[str, tuple[str, str, dict]]:
        """Return {key: (token, session_id, record_dict)}."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, token, session_id, record_json FROM results"
            ).fetchall()
        return {r[0]: (r[1], r[2], json.loads(r[3])) for r in rows}

    def save_result(
        self,
        key: str,
        token: str,
        session_id: str,
        record_json: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO results(key, token, session_id, record_json)"
                " VALUES (?, ?, ?, ?)",
                (key, token, session_id, record_json),
            )
            self._conn.commit()

    def load_task_starts(self) -> dict[tuple[str, str], datetime]:
        """Return all task start times as {(session_id, question_id): started_at}."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT session_id, question_id, started_at FROM task_starts"
            ).fetchall()
        return {(r[0], r[1]): datetime.fromisoformat(r[2]) for r in rows}

    def record_task_start(
        self, session_id: str, question_id: str, started_at: datetime
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO task_starts(session_id, question_id, started_at)"
                " VALUES (?, ?, ?)",
                (session_id, question_id, started_at.isoformat()),
            )
            self._conn.commit()

    def get_task_start(
        self, session_id: str, question_id: str
    ) -> datetime | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT started_at FROM task_starts"
                " WHERE session_id = ? AND question_id = ?",
                (session_id, question_id),
            ).fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    def load_runs(self) -> dict[str, dict]:
        """Return persisted run records keyed by run ID."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT run_id, token, session_id, run_json FROM runs"
            ).fetchall()
        return {
            row[0]: {
                "run_id": row[0],
                "token": row[1],
                "session_id": row[2],
                "record": json.loads(row[3]),
            }
            for row in rows
        }

    def save_run(
        self,
        run_id: str,
        token: str,
        session_id: str,
        run_json: str,
        created_at: datetime,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO runs(run_id, token, session_id, run_json, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (run_id, token, session_id, run_json, created_at.isoformat()),
            )
            self._conn.commit()

    def record_run_task_start(
        self, run_id: str, question_id: str, started_at: datetime
    ) -> datetime:
        """Record the first task start and return the authoritative timestamp."""
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO run_task_starts(run_id, question_id, started_at)"
                " VALUES (?, ?, ?)",
                (run_id, question_id, started_at.isoformat()),
            )
            row = self._conn.execute(
                "SELECT started_at FROM run_task_starts"
                " WHERE run_id = ? AND question_id = ?",
                (run_id, question_id),
            ).fetchone()
            self._conn.commit()
        return datetime.fromisoformat(row[0])

    def load_run_task_starts(self) -> dict[tuple[str, str], datetime]:
        """Return run-scoped task starts for in-process access."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT run_id, question_id, started_at FROM run_task_starts"
            ).fetchall()
        return {(row[0], row[1]): datetime.fromisoformat(row[2]) for row in rows}

    def create_attempt(
        self,
        attempt_id: str,
        token: str,
        session_id: str,
        run_id: str,
        question_id: str,
        idempotency_key: str,
        created_at: datetime,
    ) -> tuple[dict, bool]:
        """Create one attempt atomically, returning (record, created)."""
        with self._lock:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO attempts("
                "attempt_id, token, session_id, run_id, question_id, "
                "idempotency_key, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    attempt_id,
                    token,
                    session_id,
                    run_id,
                    question_id,
                    idempotency_key,
                    "queued",
                    created_at.isoformat(),
                ),
            )
            row = self._conn.execute(
                "SELECT attempt_id, token, session_id, run_id, question_id, "
                "idempotency_key, status, created_at FROM attempts "
                "WHERE run_id = ? AND question_id = ? AND idempotency_key = ?",
                (run_id, question_id, idempotency_key),
            ).fetchone()
            self._conn.commit()
        return (
            {
                "attempt_id": row[0],
                "token": row[1],
                "session_id": row[2],
                "run_id": row[3],
                "question_id": row[4],
                "idempotency_key": row[5],
                "status": row[6],
                "created_at": row[7],
            },
            cursor.rowcount == 1,
        )

    def get_attempt(self, attempt_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT attempt_id, token, session_id, run_id, question_id, "
                "idempotency_key, status, created_at FROM attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "attempt_id": row[0],
            "token": row[1],
            "session_id": row[2],
            "run_id": row[3],
            "question_id": row[4],
            "idempotency_key": row[5],
            "status": row[6],
            "created_at": row[7],
        }

    def update_attempt_status(self, attempt_id: str, status: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE attempts SET status = ? WHERE attempt_id = ?",
                (status, attempt_id),
            )
            self._conn.commit()

    def create_grading_job(
        self,
        job_id: str,
        attempt_id: str,
        token: str,
        session_id: str,
        run_id: str,
        question_id: str,
        created_at: datetime,
    ) -> dict:
        """Create a queued grading job for an attempt."""
        timestamp = created_at.isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO grading_jobs(job_id, attempt_id, token, session_id, run_id, "
                "question_id, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    attempt_id,
                    token,
                    session_id,
                    run_id,
                    question_id,
                    "queued",
                    timestamp,
                    timestamp,
                ),
            )
            self._conn.commit()
        return self.get_grading_job(job_id)  # type: ignore[return-value]

    def get_grading_job(self, job_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT job_id, attempt_id, token, session_id, run_id, question_id, "
                "status, error, created_at, updated_at FROM grading_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return self._job_from_row(row)

    def get_grading_job_for_attempt(self, attempt_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT job_id, attempt_id, token, session_id, run_id, question_id, "
                "status, error, created_at, updated_at FROM grading_jobs WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
        if row is None:
            return None
        return self._job_from_row(row)

    @staticmethod
    def _job_from_row(row: tuple) -> dict:
        return {
            "job_id": row[0],
            "attempt_id": row[1],
            "token": row[2],
            "session_id": row[3],
            "run_id": row[4],
            "question_id": row[5],
            "status": row[6],
            "error": row[7],
            "created_at": row[8],
            "updated_at": row[9],
        }

    def update_grading_job(
        self, job_id: str, status: str, error: str = ""
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE grading_jobs SET status = ?, error = ?, updated_at = ? "
                "WHERE job_id = ?",
                (status, error, datetime.now().isoformat(), job_id),
            )
            self._conn.commit()

    def recover_interrupted_jobs(self) -> int:
        """Mark jobs that cannot resume without persisted evidence as failed."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT job_id, attempt_id FROM grading_jobs "
                "WHERE status IN ('queued', 'running')"
            ).fetchall()
            now = datetime.now().isoformat()
            for job_id, attempt_id in rows:
                self._conn.execute(
                    "UPDATE grading_jobs SET status = 'failed', "
                    "error = ?, updated_at = ? WHERE job_id = ?",
                    ("grading interrupted by server restart", now, job_id),
                )
                self._conn.execute(
                    "UPDATE attempts SET status = 'grading_interrupted' WHERE attempt_id = ?",
                    (attempt_id,),
                )
            self._conn.commit()
        return len(rows)
