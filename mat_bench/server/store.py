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
