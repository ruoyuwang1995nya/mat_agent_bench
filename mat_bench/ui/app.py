"""FastAPI application for the mat-bench web UI."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from ..registry.registry import Registry
from ..reporting.aggregator import build_summary
from ..schemas import EvalRunRecord, QuestionItem

_logger = logging.getLogger(__name__)
_STATIC_DIR = Path(__file__).parent / "static"

# In-memory session store: {cookie_value: username}
_sessions: dict[str, str] = {}


# ---------------------------------------------------------------------------
# UI database (users + user_tokens)
# ---------------------------------------------------------------------------

class _UiDb:
    """SQLite store for UI users and per-user token metadata."""

    _CREATE = """
    CREATE TABLE IF NOT EXISTS users (
        username   TEXT PRIMARY KEY,
        salt       TEXT NOT NULL,
        pw_hash    TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS user_tokens (
        token      TEXT PRIMARY KEY,
        username   TEXT NOT NULL,
        agent_name TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(self._CREATE)
        self._conn.commit()

    def get_user(self, username: str) -> dict | None:
        row = self._conn.execute(
            "SELECT username, salt, pw_hash FROM users WHERE username = ?", (username,)
        ).fetchone()
        return {"username": row[0], "salt": row[1], "pw_hash": row[2]} if row else None

    def create_user(self, username: str, salt: str, pw_hash: str) -> None:
        self._conn.execute(
            "INSERT INTO users(username, salt, pw_hash, created_at) VALUES (?, ?, ?, ?)",
            (username, salt, pw_hash, datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    def save_token(self, token: str, username: str, agent_name: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO user_tokens(token, username, agent_name, created_at)"
            " VALUES (?, ?, ?, ?)",
            (token, username, agent_name, datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    def get_user_tokens(self, username: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT token, agent_name, created_at FROM user_tokens"
            " WHERE username = ? ORDER BY created_at DESC",
            (username,),
        ).fetchall()
        return [{"token": r[0], "agent_name": r[1], "created_at": r[2]} for r in rows]

    def get_token_agents(self) -> dict[str, str]:
        """Return {token: agent_name} for all tokens."""
        rows = self._conn.execute("SELECT token, agent_name FROM user_tokens").fetchall()
        return {r[0]: r[1] for r in rows}


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def _hash_password(password: str) -> tuple[str, str]:
    salt = os.urandom(16).hex()
    return salt, hashlib.sha256((salt + password).encode()).hexdigest()


def _verify_password(password: str, salt: str, pw_hash: str) -> bool:
    return hashlib.sha256((salt + password).encode()).hexdigest() == pw_hash


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class _AuthBody(BaseModel):
    username: str
    password: str


class _TokenRequestBody(BaseModel):
    agent_name: str


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(
    question_bank_dir: Path | None = None,
    store_dir: Path | None = None,
    backend_url: str = "http://localhost:8765",
    registry=None,
    llm_cfg=None,
    grading_workers: int = 4,
    output_dir: Path | None = None,
    parallel_checklist_workers: int = 1,
) -> FastAPI:
    """Create and return the UI FastAPI application.

    When *registry* is supplied the benchmark backend is mounted at ``/bench``
    on the same process — no separate backend server is needed.
    """
    _REPO_ROOT = Path(__file__).resolve().parent.parent.parent
    qb_dir = Path(question_bank_dir or _REPO_ROOT / "question_bank")
    store = Path(store_dir or Path.home() / ".matbench")
    sessions_db = store / "sessions.db"
    ui_db = _UiDb(store / "ui.db")

    _combined = registry is not None
    if _combined:
        from datetime import datetime as _dt
        from ..server.app import app as _bench_app, init_server, create_token_direct as _mint_token
        _ts = _dt.now().strftime('%Y%m%d_%H%M%S')
        _out_dir = output_dir or (store / 'runs' / f'serve_{_ts}')
        init_server(
            registry=registry,
            output_dir=_out_dir,
            llm_cfg=llm_cfg,
            grading_workers=grading_workers,
            store_dir=store,
            parallel_checklist_workers=parallel_checklist_workers,
        )
        _backend_url = ""
    else:
        _bench_app = None
        _mint_token = None
        _backend_url = backend_url.rstrip("/")

    app = FastAPI(title="mat-bench UI", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
    if _combined:
        app.mount("/bench", _bench_app)

    # ------------------------------------------------------------------
    # Auth dependency
    # ------------------------------------------------------------------

    def _require_user(request: Request) -> str:
        token = request.cookies.get("mat_bench_session")
        if not token or token not in _sessions:
            raise HTTPException(401, "Not authenticated")
        return _sessions[token]

    # ------------------------------------------------------------------
    # Root
    # ------------------------------------------------------------------

    @app.get("/")
    async def root():
        return RedirectResponse("/static/index.html")

    # ------------------------------------------------------------------
    # Auth endpoints
    # ------------------------------------------------------------------

    @app.post("/api/auth/register")
    async def register(body: _AuthBody):
        username = body.username.strip()
        if not username:
            raise HTTPException(422, "Username cannot be empty")
        if len(body.password) < 6:
            raise HTTPException(422, "Password must be at least 6 characters")
        if ui_db.get_user(username):
            raise HTTPException(409, "Username already taken")
        salt, pw_hash = _hash_password(body.password)
        ui_db.create_user(username, salt, pw_hash)
        return {"username": username}

    @app.post("/api/auth/login")
    async def login(body: _AuthBody, response: Response):
        user = ui_db.get_user(body.username.strip())
        if not user or not _verify_password(body.password, user["salt"], user["pw_hash"]):
            raise HTTPException(401, "Invalid username or password")
        session_token = str(uuid.uuid4())
        _sessions[session_token] = user["username"]
        response.set_cookie(
            "mat_bench_session",
            session_token,
            httponly=True,
            samesite="lax",
            max_age=60 * 60 * 24 * 7,
        )
        return {"username": user["username"]}

    @app.post("/api/auth/logout")
    async def logout(request: Request, response: Response, _: str = Depends(_require_user)):
        session_token = request.cookies.get("mat_bench_session")
        if session_token:
            _sessions.pop(session_token, None)
        response.delete_cookie("mat_bench_session")
        return {"ok": True}

    @app.get("/api/auth/me")
    async def me(request: Request):
        token = request.cookies.get("mat_bench_session")
        if not token or token not in _sessions:
            raise HTTPException(401, "Not authenticated")
        return {"username": _sessions[token]}

    # ------------------------------------------------------------------
    # Config endpoint
    # ------------------------------------------------------------------

    @app.get("/api/config")
    async def config():
        return {"server_url": "/bench" if _combined else _backend_url}

    # ------------------------------------------------------------------
    # Question bank endpoints
    # ------------------------------------------------------------------

    @app.get("/api/questions")
    async def list_questions(
        capability: str | None = Query(None),
        domain: str | None = Query(None),
        tags: list[str] | None = Query(None),
    ):
        try:
            registry = Registry(qb_dir)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc))
        questions = registry.list_questions(capability=capability, domain=domain, tags=tags)
        return [
            {
                "id": q.id,
                "capability": q.capability,
                "domain": q.domain,
                "intent": q.intent,
                "tags": q.tags,
                "tag_count": len(q.tags),
                "checklist_count": len(q.item.scoring_checklist),
            }
            for q in questions
        ]

    @app.post("/api/questions/upload")
    async def upload_question(file: UploadFile = File(...)):
        content = await file.read()
        try:
            raw = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise HTTPException(422, f"YAML parse error: {exc}")
        if not isinstance(raw, dict):
            raise HTTPException(422, "Expected a YAML mapping at the top level")
        try:
            item = QuestionItem.model_validate(raw)
        except ValidationError as exc:
            raise HTTPException(422, str(exc))
        dest_dir = qb_dir / item.id
        if dest_dir.exists():
            raise HTTPException(409, f"Question '{item.id}' already exists")
        dest_dir.mkdir(parents=True)
        (dest_dir / "question.yaml").write_bytes(content)
        return {"id": item.id, "capability": item.capability, "domain": item.domain}

    # ------------------------------------------------------------------
    # Token endpoints (auth required)
    # ------------------------------------------------------------------

    @app.get("/api/tokens")
    async def list_tokens(username: str = Depends(_require_user)):
        user_tokens = ui_db.get_user_tokens(username)
        token_set = {t["token"] for t in user_tokens}

        token_stats: dict[str, dict] = {t: {"eval_count": 0, "models": set()} for t in token_set}
        for token, _sid, rec in _read_results(sessions_db):
            if token in token_stats:
                token_stats[token]["eval_count"] += 1
                if rec.model_name:
                    token_stats[token]["models"].add(rec.model_name)

        return [
            {
                "token": t["token"],
                "agent_name": t["agent_name"],
                "created_at": t["created_at"],
                "evaluation_count": token_stats[t["token"]]["eval_count"],
                "models": sorted(token_stats[t["token"]]["models"]),
            }
            for t in user_tokens
        ]

    @app.post("/api/tokens")
    def generate_token(body: _TokenRequestBody, username: str = Depends(_require_user)):
        if not body.agent_name.strip():
            raise HTTPException(422, "agent_name cannot be empty")
        if _combined:
            data = _mint_token()
        else:
            try:
                req = urllib.request.Request(f"{_backend_url}/token", data=b"", method="POST")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                raise HTTPException(exc.code, exc.read().decode()) from exc
            except Exception as exc:
                raise HTTPException(503, f"Backend unavailable at {_backend_url}: {exc}") from exc
        ui_db.save_token(data["token"], username, body.agent_name.strip())
        return data

    # ------------------------------------------------------------------
    # Sessions endpoint (auth required)
    # ------------------------------------------------------------------

    @app.get("/api/sessions")
    async def list_sessions(username: str = Depends(_require_user)):
        user_tokens = ui_db.get_user_tokens(username)
        token_to_agent = {t["token"]: t["agent_name"] for t in user_tokens}
        token_set = set(token_to_agent)

        # Build session metadata from the sessions table (authoritative source)
        session_meta: dict[str, dict] = {}
        for session_id, token, created_at in _read_sessions(sessions_db):
            if token not in token_set:
                continue
            session_meta[session_id] = {
                "session_id": session_id,
                "token": token,
                "agent_name": token_to_agent[token],
                "created_at": created_at,
                "records": [],
            }

        # Attach results to their sessions
        for token, session_id, rec in _read_results(sessions_db):
            if session_id in session_meta:
                session_meta[session_id]["records"].append(rec)

        result = []
        for sd in session_meta.values():
            recs = sd["records"]
            ms = build_summary(recs) if recs else None
            result.append(
                {
                    "session_id": sd["session_id"],
                    "token": sd["token"],
                    "agent_name": sd["agent_name"],
                    "question_count": len({r.question_id for r in recs}),
                    "eval_count": len(recs),
                    "pass_rate": round(ms.pass_rate, 4) if ms else None,
                    "weighted_score": round(ms.weighted_pass_rate, 4) if ms else None,
                    "questions_passed": ms.questions_passed if ms else None,
                    "models": sorted({r.model_name for r in recs if r.model_name}),
                    "created_at": sd["created_at"],
                }
            )

        result.sort(key=lambda x: x["created_at"], reverse=True)
        return result

    # ------------------------------------------------------------------
    # Template endpoints
    # ------------------------------------------------------------------

    @app.get("/api/templates/benchmark")
    async def get_benchmark_template():
        path = _REPO_ROOT / "agents" / "mat_bench_skill.md"
        return {"content": path.read_text()}

    @app.get("/api/templates/question")
    async def get_question_template():
        path = _REPO_ROOT / "agents" / "run_question.md"
        return {"content": path.read_text()}

    # ------------------------------------------------------------------
    # Leaderboard endpoints
    # ------------------------------------------------------------------

    @app.get("/api/leaderboard")
    async def get_leaderboard():
        rows = _read_results(sessions_db)

        if not rows:
            return {"leaderboard": [], "total_evaluations": 0}

        token_to_agent = ui_db.get_token_agents()

        by_agent: dict[str, list[EvalRunRecord]] = {}
        agent_models: dict[str, set[str]] = {}
        for token, _, rec in rows:
            agent_name = token_to_agent.get(token, "unknown")
            by_agent.setdefault(agent_name, []).append(rec)
            agent_models.setdefault(agent_name, set())
            if rec.model_name:
                agent_models[agent_name].add(rec.model_name)

        leaderboard = []
        for agent_name, recs in by_agent.items():
            ms = build_summary(recs)
            leaderboard.append(
                {
                    "agent": agent_name,
                    "models": sorted(agent_models.get(agent_name, set())),
                    "total_evaluations": ms.total_runs,
                    "pass_rate": round(ms.pass_rate, 4),
                    "weighted_score": round(ms.weighted_pass_rate, 4),
                    "questions_passed": ms.questions_passed,
                    "by_capability": {
                        k: round(v.pass_rate(), 4) for k, v in ms.by_capability.items()
                    },
                    "by_domain": {
                        k: round(v.pass_rate(), 4) for k, v in ms.by_domain.items()
                    },
                }
            )

        leaderboard.sort(key=lambda x: x["questions_passed"], reverse=True)
        return {"leaderboard": leaderboard, "total_evaluations": len(rows)}

    return app


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _read_results(sessions_db: Path) -> list[tuple[str, str, EvalRunRecord]]:
    """Read all (token, session_id, EvalRunRecord) rows from sessions.db."""
    if not sessions_db.exists():
        return []
    out: list[tuple[str, str, EvalRunRecord]] = []
    try:
        conn = sqlite3.connect(str(sessions_db), check_same_thread=False)
        rows = conn.execute("SELECT token, session_id, record_json FROM results").fetchall()
        conn.close()
        for token, session_id, record_json in rows:
            try:
                out.append((token, session_id, EvalRunRecord.model_validate(json.loads(record_json))))
            except Exception as exc:
                _logger.debug("skip bad result row: %s", exc)
    except Exception as exc:
        _logger.warning("cannot read sessions.db: %s", exc)
    return out


def _read_sessions(sessions_db: Path) -> list[tuple[str, str, str]]:
    """Read all (session_id, token, created_at) rows from sessions.db."""
    if not sessions_db.exists():
        return []
    try:
        conn = sqlite3.connect(str(sessions_db), check_same_thread=False)
        rows = conn.execute("SELECT session_id, token, created_at FROM sessions").fetchall()
        conn.close()
        return [(r[0], r[1], r[2]) for r in rows]
    except Exception as exc:
        _logger.warning("cannot read sessions table: %s", exc)
        return []
