"""FastAPI server for harness-less mat-bench evaluation.

Exposes the question bank over HTTP so any agent with HTTP access can
run the benchmark without a local harness.

Endpoints::

    GET  /                                Bench API info (version, guide link)
    GET  /guide                           Agent HTTP API reference (plain text, no auth)
    POST /sessions                        Create a new session (requires X-API-Token header)
    GET  /questions                       List questions (filters: capability, task_type, domain, tags, limit)
    GET  /questions/{id}                  Full question details + data file list (requires session_id; starts duration timer)
    GET  /questions/{id}/data/{fname}     Download a data file
    POST /submit/{id}?session_id=S0001    Submit result files + metadata (multipart)
    GET  /results/{id}                    Grading result(s) for one question
    GET  /results                         Summary of all submitted results

Authentication:
    All /submit and /results endpoints require the X-API-Token header.
    Obtain a token from the web UI, then create a session via POST /sessions.

Submission form fields:
    meta      JSON string with answer, model_name, num_turns, duration_ms,
              usage (prompt/completion/total_tokens), tool_calls list, is_error,
              optional run_status ("timeout" receives zero points)
    <any>     File fields — filename attribute is used as the workspace filename

Tool call format in meta.tool_calls::

    [{"step": 1, "tool_name": "bash", "args": {"command": "..."},
      "observation_excerpt": "...", "succeeded": true}, ...]
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import random
import secrets
import string
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..evaluation.evidence import (
    ArtifactRecord,
    CallStatus,
    EvidenceBundle,
    ToolCallRecord,
    TokenUsage,
)
from ..evaluation.grade import grade_question
from ..harness import build_token_usage_record
from ..registry import Registry
from ..reporting.aggregator import build_summary
from ..schemas import EvalRunRecord, LLMConfig, TokenUsageRecord

# ---------------------------------------------------------------------------
# Server-internal models
# ---------------------------------------------------------------------------

_logger = logging.getLogger(__name__)


class TokenRecord(BaseModel):
    token: str
    created_at: datetime


class SessionRecord(BaseModel):
    session_id: str  # e.g. "S0001"
    token: str
    model_name: str = "unknown"
    created_at: datetime


class RunCreateRequest(BaseModel):
    """Selection request for one immutable evaluation run."""

    model_name: str | None = None
    question_ids: list[str] | None = None
    capability: str | None = None
    task_type: str | None = None
    domain: str | None = None
    tags: list[str] | None = None
    limit: int | None = None


class RunRecord(BaseModel):
    """Persisted run metadata and its activation snapshot."""

    run_id: str
    session_id: str
    model_name: str
    status: str = "active"
    source: str = "official"
    catalog_hash: str
    question_ids: list[str]
    created_at: datetime


# ---------------------------------------------------------------------------
# Module-level server state (set by init_server before uvicorn starts)
# ---------------------------------------------------------------------------

_registry: Registry | None = None
_llm_cfg: LLMConfig | None = None
_output_dir: Path | None = None
_grading_executor: ThreadPoolExecutor | None = None
_parallel_checklist_workers: int = 1
_allow_token_registration: bool = False

_tokens: dict[str, TokenRecord] = {}
_tokens_lock = threading.Lock()

_sessions: dict[str, SessionRecord] = {}
_sessions_lock = threading.Lock()

_runs: dict[str, tuple[str, RunRecord]] = {}
_runs_lock = threading.Lock()

# key = f"{token}:{session_id}:{question_id}" → (token, session_id, record)
_results: dict[str, tuple[str, str, EvalRunRecord]] = {}
_results_lock = threading.Lock()

# keys currently being graded (submitted but not yet complete)
_grading_pending: set[str] = set()

# key = f"{token}:{session_id}:{question_id}" → number of times submitted
_submission_counts: dict[str, int] = {}
_submission_counts_lock = threading.Lock()

# maximum allowed submissions per question per session (0 = unlimited)
_max_submissions_per_question: int = 1

# key = (session_id, question_id) → task start time (bench-measured)
_task_starts: dict[tuple[str, str], datetime] = {}
_task_starts_lock = threading.Lock()

_run_task_starts: dict[tuple[str, str], datetime] = {}
_run_task_starts_lock = threading.Lock()

_token_store: "TokenStore | None" = None
_session_store: "SessionStore | None" = None

_MAX_ARTIFACT_BYTES = 25 * 1024 * 1024
_MAX_ARTIFACTS = 100



# ---------------------------------------------------------------------------
# Server initialisation
# ---------------------------------------------------------------------------


def init_server(
    registry: Registry,
    output_dir: Path,
    llm_cfg: LLMConfig | None = None,
    grading_workers: int = 4,
    store_dir: Path | None = None,
    parallel_checklist_workers: int = 1,
    max_submissions_per_question: int = 1,
    allow_token_registration: bool = False,
) -> None:
    """Initialise server state. Must be called before uvicorn.run()."""
    global _registry, _llm_cfg, _output_dir, _results, _grading_executor
    global _token_store, _session_store, _task_starts, _run_task_starts
    global _parallel_checklist_workers, _runs, _allow_token_registration
    global _grading_pending, _submission_counts, _max_submissions_per_question
    _registry = registry
    _llm_cfg = llm_cfg
    _output_dir = output_dir
    _results = {}
    _task_starts = {}
    _run_task_starts = {}
    _runs = {}
    _grading_pending = set()
    _submission_counts = {}
    _max_submissions_per_question = max(0, int(max_submissions_per_question))
    _parallel_checklist_workers = max(1, int(parallel_checklist_workers))
    _allow_token_registration = allow_token_registration
    output_dir.mkdir(parents=True, exist_ok=True)

    from .store import TokenStore, SessionStore
    _store_dir = store_dir or Path.home() / ".matbench"
    _token_store = TokenStore(_store_dir / "tokens.db")
    _session_store = SessionStore(_store_dir / "sessions.db")
    interrupted_jobs = _session_store.recover_interrupted_jobs()
    if interrupted_jobs:
        _logger.warning("marked %d interrupted grading job(s) as failed", interrupted_jobs)

    with _tokens_lock:
        for t, data in _token_store.load_tokens().items():
            _tokens[t] = TokenRecord(**data)

    with _sessions_lock:
        raw_sessions = _session_store.load_sessions()
        for sid, data in raw_sessions.items():
            _sessions[sid] = SessionRecord(**data)

    with _results_lock:
        for key, (tok, sid, rec_dict) in _session_store.load_results().items():
            _results[key] = (tok, sid, EvalRunRecord(**rec_dict))
            # Each persisted result counts as 1 prior submission
            _submission_counts[key] = _submission_counts.get(key, 0) + 1

    with _task_starts_lock:
        _task_starts.update(_session_store.load_task_starts())
    with _run_task_starts_lock:
        _run_task_starts.update(_session_store.load_run_task_starts())

    with _runs_lock:
        for run_id, data in _session_store.load_runs().items():
            _runs[run_id] = (data["token"], RunRecord.model_validate(data["record"]))

    _grading_executor = ThreadPoolExecutor(max_workers=grading_workers)


def create_token_direct() -> dict:
    """Create a new API token in-process (for combined serve-all mode)."""
    token_str = secrets.token_hex(32)
    record = TokenRecord(token=token_str, created_at=datetime.now(timezone.utc))
    with _tokens_lock:
        _tokens[token_str] = record
    if _token_store:
        _token_store.save_token(token_str, record.created_at)
    return {"token": token_str, "created_at": record.created_at.isoformat()}


def _require_registry() -> Registry:
    if _registry is None:
        raise RuntimeError("Server not initialised — call init_server() first.")
    return _registry


# ---------------------------------------------------------------------------
# Sync grading helper (runs in thread pool)
# ---------------------------------------------------------------------------


def _do_grade(
    question: Any,
    answer: str,
    evidence: EvidenceBundle,
    token_usage_record: TokenUsageRecord,
    run_status: str,
    model_name: str,
    duration_ms: int,
) -> EvalRunRecord:
    """Synchronous grading call. Dispatched to thread pool via run_in_executor."""
    question_id = question.id
    _logger.info("grading start  %s  (checklist_workers=%d)", question_id, _parallel_checklist_workers)
    t0 = time.monotonic()
    try:
        report = grade_question(
            question=question,
            answer=answer,
            evidence=evidence,
            llm_cfg=_llm_cfg,
            mode="direct",
            prompt=question.item.human_prompt_seed,
            run_status=run_status,
            model_name=model_name,
            token_usage=token_usage_record,
            duration_ms=duration_ms,
            parallel_checklist_workers=_parallel_checklist_workers,
        )
        elapsed = time.monotonic() - t0
        _logger.info("grading done   %s  score=%.3f  %.1fs", question_id, report.score, elapsed)
        return report.record
    except Exception as exc:
        elapsed = time.monotonic() - t0
        _logger.error("grading error  %s  %.1fs  %s", question_id, elapsed, exc)
        return EvalRunRecord(
            question_id=question_id,
            capability=question.capability,
            domain=question.domain,
            mode="direct",  # type: ignore[arg-type]
            repeat_idx=0,
            prompt=question.item.human_prompt_seed,
            answer=answer,
            run_status="grading_error",
            model_name=model_name,
            token_usage=token_usage_record,
            duration_ms=duration_ms,
            created_at=datetime.now(timezone.utc),
        )


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

try:
    from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
    from fastapi.responses import FileResponse, PlainTextResponse

    app = FastAPI(
        title="mat-bench server",
        version="0.1.0",
        description="Harness-less benchmark server for mat-agent-bench.",
    )

    # ------------------------------------------------------------------
    # Auth dependency
    # ------------------------------------------------------------------

    async def _require_token(
        x_api_token: str | None = Header(default=None),
    ) -> str:
        """Validate X-API-Token header and return the token string."""
        if x_api_token is None:
            raise HTTPException(401, detail="X-API-Token header is required")
        with _tokens_lock:
            if x_api_token not in _tokens:
                raise HTTPException(401, detail="Invalid or unknown API token")
        return x_api_token

    # ------------------------------------------------------------------
    # Token & session endpoints
    # ------------------------------------------------------------------

    @app.get("/")
    async def bench_info() -> dict:
        """Bench API info — version and guide link."""
        return {
            "api": "mat-bench",
            "version": "0.1.0",
            "guide": "/guide",
            "token_registration": _allow_token_registration,
        }

    @app.get("/guide")
    async def agent_guide() -> PlainTextResponse:
        """Return the agent HTTP API guide as plain text/markdown."""
        path = Path(__file__).parent.parent.parent / "agents" / "agent_api_guide.md"
        return PlainTextResponse(path.read_text(encoding="utf-8"))

    @app.post("/token")
    async def register_token() -> dict:
        """Mint a token only when explicitly enabled for local development or tests."""
        if not _allow_token_registration:
            raise HTTPException(
                403,
                detail=(
                    "Token registration is disabled. Start the server with "
                    "--allow-token-registration for local development or testing."
                ),
            )
        return create_token_direct()

    @app.post("/sessions")
    async def create_session(
        token: str = Depends(_require_token),
        model_name: str = Body(..., embed=True, description="Model/agent identifier for this session"),
    ) -> dict:
        """Create a new session for the authenticated token. model_name is locked for the session."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
        session_id = f"S{ts}_{suffix}"
        with _sessions_lock:
            record = SessionRecord(
                session_id=session_id,
                token=token,
                model_name=model_name,
                created_at=datetime.now(timezone.utc),
            )
            _sessions[session_id] = record
        _session_store.save_session(session_id, token, model_name, record.created_at)
        return {"session_id": session_id, "model_name": model_name, "created_at": record.created_at.isoformat()}

    @app.get("/sessions")
    async def list_sessions(
        token: str = Depends(_require_token),
        limit: int = Query(default=10, ge=1, le=500, description="Max sessions to return (default: 10)"),
    ) -> list[dict]:
        """List sessions for the authenticated token, most recent first."""
        with _sessions_lock:
            owned = [r for r in _sessions.values() if r.token == token]
        owned.sort(key=lambda r: r.created_at, reverse=True)
        return [
            {
                "session_id": r.session_id,
                "model_name": r.model_name,
                "created_at": r.created_at.isoformat(),
            }
            for r in owned[:limit]
        ]

    # ------------------------------------------------------------------
    # Run endpoints
    # ------------------------------------------------------------------

    def _catalog_hash(questions: list[Any]) -> str:
        payload = json.dumps(
            [question.item.model_dump(mode="json") for question in questions],
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _owned_run(run_id: str, token: str) -> RunRecord:
        with _runs_lock:
            entry = _runs.get(run_id)
        if entry is None:
            raise HTTPException(404, detail=f"Run '{run_id}' not found")
        owner_token, run = entry
        if owner_token != token:
            raise HTTPException(403, detail="Run does not belong to this token")
        return run

    @app.post("/runs", status_code=201)
    async def create_run(
        body: RunCreateRequest,
        session_id: str = Query(..., description="Session ID from POST /sessions"),
        token: str = Depends(_require_token),
    ) -> dict:
        """Create an immutable, session-owned activation snapshot."""
        with _sessions_lock:
            session = _sessions.get(session_id)
        if session is None:
            raise HTTPException(404, detail=f"Session '{session_id}' not found")
        if session.token != token:
            raise HTTPException(403, detail="Session does not belong to this token")
        if body.limit is not None and body.limit < 0:
            raise HTTPException(422, detail="limit must be non-negative")

        registry = _require_registry()
        try:
            if body.question_ids:
                questions = [registry.get_question(qid) for qid in body.question_ids]
                questions = [
                    q for q in questions
                    if (not body.capability or body.capability in q.capability)
                    and (not body.task_type or body.task_type == q.task_type)
                    and (not body.domain or body.domain == q.domain)
                    and (not body.tags or set(body.tags).issubset(set(q.tags)))
                ]
            else:
                questions = registry.list_questions(
                    capability=body.capability,
                    task_type=body.task_type,
                    domain=body.domain,
                    tags=body.tags,
                )
        except KeyError as exc:
            raise HTTPException(422, detail=str(exc))

        if body.limit is not None:
            questions = questions[:body.limit]
        if not questions:
            raise HTTPException(422, detail="question selection produced no questions")

        question_ids = [q.id for q in questions]
        created_at = datetime.now(timezone.utc)
        run = RunRecord(
            run_id=f"R{created_at.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}",
            session_id=session_id,
            model_name=body.model_name or session.model_name,
            catalog_hash=_catalog_hash(questions),
            question_ids=question_ids,
            created_at=created_at,
        )
        with _runs_lock:
            _runs[run.run_id] = (token, run)
        _session_store.save_run(
            run.run_id,
            token,
            session_id,
            json.dumps(run.model_dump(mode="json"), default=str),
            created_at,
        )
        return run.model_dump(mode="json")

    @app.get("/runs/{run_id}")
    async def get_run(
        run_id: str,
        token: str = Depends(_require_token),
    ) -> dict:
        """Return one run's immutable activation metadata."""
        return _owned_run(run_id, token).model_dump(mode="json")

    @app.get("/runs/{run_id}/tasks")
    async def list_run_tasks(
        run_id: str,
        token: str = Depends(_require_token),
    ) -> list[dict]:
        """List activated tasks in their frozen run order."""
        run = _owned_run(run_id, token)
        registry = _require_registry()
        tasks = []
        for position, question_id in enumerate(run.question_ids):
            try:
                question = registry.get_question(question_id)
            except KeyError:
                tasks.append({"position": position, "question_id": question_id, "status": "unavailable"})
                continue
            tasks.append({
                "position": position,
                "question_id": question.id,
                "capability": question.capability,
                "task_type": question.task_type,
                "domain": question.domain,
                "intent": question.intent,
                "status": "activated",
            })
        return tasks

    @app.get("/runs/{run_id}/tasks/{question_id}")
    async def get_run_task(
        run_id: str,
        question_id: str,
        token: str = Depends(_require_token),
    ) -> dict:
        """Fetch an activated task and start its run-scoped timer once."""
        run = _owned_run(run_id, token)
        if question_id not in run.question_ids:
            raise HTTPException(404, detail=f"Question '{question_id}' is not activated in run '{run_id}'")
        registry = _require_registry()
        try:
            question = registry.get_question(question_id)
        except KeyError:
            raise HTTPException(410, detail=f"Question '{question_id}' is no longer available")

        task_key = (run_id, question_id)
        with _run_task_starts_lock:
            started_at = _run_task_starts.get(task_key)
        if started_at is None:
            candidate = datetime.now(timezone.utc)
            started_at = _session_store.record_run_task_start(run_id, question_id, candidate)
            with _run_task_starts_lock:
                _run_task_starts[task_key] = started_at

        return {
            "run_id": run_id,
            "question_id": question.id,
            "capability": question.capability,
            "task_type": question.task_type,
            "domain": question.domain,
            "intent": question.intent,
            "prompt": question.item.human_prompt_seed,
            "started_at": started_at.isoformat(),
            "data_files": [
                {"key": df.key, "path": df.path, "filename": Path(df.path).name}
                for df in question.item.data_files
            ],
        }

    # ------------------------------------------------------------------
    # Question endpoints (no auth required)
    # ------------------------------------------------------------------

    @app.get("/questions")
    async def list_questions(
        capability: str | None = None,
        task_type: str | None = None,
        domain: str | None = None,
        tags: list[str] | None = Query(default=None),
        q: str | None = Query(default=None, description="Case-insensitive search across ID, intent, domain, capabilities, and tags"),
        offset: int = Query(default=0, ge=0),
        limit: int | None = Query(default=None, ge=1, le=500),
    ) -> list[dict]:
        """List available questions with optional filters."""
        registry = _require_registry()
        questions = registry.list_questions(
            capability=capability,
            task_type=task_type,
            domain=domain,
            tags=tags,
        )
        if q and q.strip():
            needle = q.strip().casefold()
            questions = [
                question for question in questions
                if needle in question.id.casefold()
                or needle in question.intent.casefold()
                or needle in question.domain.casefold()
                or any(needle in value.casefold() for value in question.capability)
                or any(needle in value.casefold() for value in question.tags)
            ]
        questions = questions[offset:]
        if limit is not None:
            questions = questions[:limit]
        return [
            {
                "id": q.id,
                "capability": q.capability,
                "task_type": q.task_type,
                "domain": q.domain,
                "intent": q.intent,
                "tags": q.tags,
            }
            for q in questions
        ]

    @app.get("/questions/{question_id}")
    async def get_question(
        question_id: str,
        session_id: str = Query(..., description="Session ID from POST /sessions"),
    ) -> dict:
        """Get full question details: prompt, data file list, tags.

        Calling this endpoint records the task start time for bench-side
        duration tracking. Re-fetching the question resets the timer.
        """
        registry = _require_registry()
        try:
            q = registry.get_question(question_id)
        except KeyError:
            raise HTTPException(404, detail=f"Question '{question_id}' not found")

        with _sessions_lock:
            if session_id not in _sessions:
                raise HTTPException(404, detail=f"Session '{session_id}' not found")

        t_start = datetime.now(timezone.utc)
        with _task_starts_lock:
            _task_starts[(session_id, question_id)] = t_start
        _session_store.record_task_start(session_id, question_id, t_start)

        return {
            "id": q.id,
            "capability": q.capability,
            "domain": q.domain,
            "intent": q.intent,
            "prompt": q.item.human_prompt_seed,
            "tags": q.tags,
            "data_files": [
                {"key": df.key, "path": df.path, "filename": Path(df.path).name}
                for df in q.item.data_files
            ],
        }

    @app.get("/questions/{question_id}/data/{fname:path}")
    async def get_data_file(question_id: str, fname: str) -> FileResponse:
        """Download a data file. Match by key, full path, or basename."""
        registry = _require_registry()
        try:
            q = registry.get_question(question_id)
        except KeyError:
            raise HTTPException(404, detail=f"Question '{question_id}' not found")

        fname_base = Path(fname).name
        file_path: Path | None = None
        for df in q.item.data_files:
            if df.key == fname or df.path == fname or Path(df.path).name == fname_base:
                file_path = q.data_file_path(df.key)
                break

        if file_path is None or not file_path.exists():
            raise HTTPException(
                404, detail=f"Data file '{fname}' not found for '{question_id}'"
            )
        return FileResponse(str(file_path), filename=file_path.name)

    # ------------------------------------------------------------------
    # Submission endpoint
    # ------------------------------------------------------------------

    def _safe_artifact_name(name: str) -> Path:
        """Return a safe workspace-relative path or reject traversal."""
        if not name or "\\" in name:
            raise HTTPException(400, detail="artifact filename must be a non-empty relative path")
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            raise HTTPException(400, detail=f"unsafe artifact filename: {name!r}")
        return path

    @app.post("/submit/{question_id}")
    async def submit(
        question_id: str,
        request: Request,
        session_id: str | None = Query(default=None, description="Legacy session ID from POST /sessions"),
        run_id: str | None = Query(default=None, description="Run ID from POST /runs"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        token: str = Depends(_require_token),
    ) -> dict:
        """Submit result files + metadata. Returns immediate grading result.

        Form fields:
            meta   — JSON string (required)
            *      — any file fields; filename attribute used as workspace path
        """
        registry = _require_registry()
        try:
            question = registry.get_question(question_id)
        except KeyError:
            raise HTTPException(404, detail=f"Question '{question_id}' not found")

        run: RunRecord | None = None
        if run_id is not None:
            run = _owned_run(run_id, token)
            if question_id not in run.question_ids:
                raise HTTPException(404, detail=f"Question '{question_id}' is not activated in run '{run_id}'")
            if session_id is not None and session_id != run.session_id:
                raise HTTPException(403, detail="Session does not belong to this run")
            session_id = run.session_id
            if not idempotency_key or not idempotency_key.strip():
                raise HTTPException(400, detail="Idempotency-Key header is required for run submissions")

        if session_id is None:
            raise HTTPException(400, detail="session_id or run_id is required")

        # Validate session ownership
        with _sessions_lock:
            session = _sessions.get(session_id)
        if session is None:
            raise HTTPException(404, detail=f"Session '{session_id}' not found")
        if session.token != token:
            raise HTTPException(403, detail="Session does not belong to this token")

        # Enforce the old per-question limit only for legacy session submissions.
        composite_key = f"{token}:{session_id}:{question_id}" if run is None else f"run:{run_id}:{question_id}"
        if run is None and _max_submissions_per_question > 0:
            with _submission_counts_lock:
                if _submission_counts.get(composite_key, 0) >= _max_submissions_per_question:
                    raise HTTPException(
                        409,
                        detail=(
                            f"Submission limit ({_max_submissions_per_question}) reached "
                            f"for question '{question_id}' in session '{session_id}'."
                        ),
                    )

        # Parse multipart form
        try:
            form_data = await request.form()
        except Exception as exc:
            raise HTTPException(400, detail=f"Failed to parse form data: {exc}")

        t_submit = datetime.now(timezone.utc)

        # Compute duration from the run timer for new clients, legacy timer otherwise.
        if run is not None:
            task_key = (run_id, question_id)
            with _run_task_starts_lock:
                t_start = _run_task_starts.get(task_key)
            if t_start is None:
                t_start = _session_store.record_run_task_start(
                    run_id, question_id, t_submit
                )
                with _run_task_starts_lock:
                    _run_task_starts[task_key] = t_start
        else:
            with _task_starts_lock:
                t_start = _task_starts.get((session_id, question_id))
            if t_start is None:
                t_start = _session_store.get_task_start(session_id, question_id)
        if t_start is None:
            raise HTTPException(
                400,
                detail=(
                    f"No task start time recorded for question '{question_id}' "
                    f"in session '{session_id}'. Fetch the question via "
                    "GET /questions/{id}?session_id=... before submitting."
                ),
            )
        bench_duration_ms = int((t_submit - t_start).total_seconds() * 1000)

        meta_raw = form_data.get("meta", "{}")
        if not isinstance(meta_raw, str):
            meta_raw = "{}"
        try:
            meta: dict[str, Any] = json.loads(meta_raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(400, detail=f"Invalid 'meta' JSON: {exc}")

        # Collect submitted files
        submitted: dict[str, bytes] = {}
        for _field, value in form_data.multi_items():
            if hasattr(value, "filename") and value.filename:
                if len(submitted) >= _MAX_ARTIFACTS:
                    raise HTTPException(413, detail=f"too many artifacts (maximum {_MAX_ARTIFACTS})")
                _safe_artifact_name(str(value.filename))
                content = await value.read()  # type: ignore[union-attr]
                if len(content) > _MAX_ARTIFACT_BYTES:
                    raise HTTPException(413, detail=f"artifact exceeds {_MAX_ARTIFACT_BYTES} byte limit")
                submitted[value.filename] = content  # type: ignore[union-attr]

        attempt: dict | None = None
        if run is not None:
            attempt, created = _session_store.create_attempt(
                attempt_id=f"A{uuid.uuid4().hex}",
                token=token,
                session_id=session_id,
                run_id=run_id,
                question_id=question_id,
                idempotency_key=idempotency_key.strip(),
                created_at=t_submit,
            )
            if not created:
                existing_job = _session_store.get_grading_job_for_attempt(attempt["attempt_id"])
                return {
                    "attempt_id": attempt["attempt_id"],
                    "question_id": question_id,
                    "run_id": run_id,
                    "status": attempt["status"],
                    "job_id": existing_job["job_id"] if existing_job else None,
                    "idempotent_replay": True,
                }

        # Write files to workspace (scoped by session)
        if _output_dir is None:
            raise HTTPException(500, detail="Server output directory not configured")
        workspace_scope = run_id if run_id is not None else session_id
        workspace = _output_dir / "workspaces" / workspace_scope / question_id
        workspace.mkdir(parents=True, exist_ok=True)

        input_fnames = {Path(df.path).name for df in question.item.data_files}
        artifacts: list[ArtifactRecord] = []
        for fname, content in submitted.items():
            safe_name = _safe_artifact_name(fname)
            destination = workspace / safe_name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            if fname not in input_fnames:
                artifacts.append(
                    ArtifactRecord(
                        path=safe_name.as_posix(),
                        artifact_type=safe_name.suffix.lstrip(".") or "unknown",
                        size_bytes=len(content),
                    )
                )

        # Extract metadata fields
        answer = str(meta.get("answer", ""))
        model_name = session.model_name  # locked at session creation; meta's model_name is ignored
        num_turns = int(meta.get("num_turns") or 0)
        is_error = bool(meta.get("is_error", False))
        usage: dict[str, Any] = meta.get("usage") or {}
        reported_status = str(meta.get("run_status") or "").lower()
        run_status = "timeout" if reported_status == "timeout" else ("error" if is_error else "completed")

        # Parse self-reported tool calls (grounding axis)
        raw_tcs = meta.get("tool_calls") or []
        tool_call_records: list[ToolCallRecord] = []
        for i, tc in enumerate(raw_tcs):
            if not isinstance(tc, dict):
                continue
            status = (
                CallStatus.SUCCESS if tc.get("succeeded", True) else CallStatus.FAILED
            )
            tool_call_records.append(
                ToolCallRecord(
                    step=int(tc.get("step", i + 1)),
                    call_index=int(tc.get("call_index", 0)),
                    tool_name=str(tc.get("tool_name", "")),
                    args=tc.get("args") or {},
                    status=status,
                    observation_excerpt=str(tc.get("observation_excerpt", "")),
                )
            )

        # Build evidence bundle
        token_usage_run = TokenUsage.from_usage_dict(usage)
        evidence = EvidenceBundle(
            task_id=question_id,
            final_answer=answer[:8000],
            artifacts=artifacts,
            tool_calls=tool_call_records,
            model_name=model_name,
            token_usage_run=token_usage_run,
            total_steps=num_turns,
            run_status=run_status,
            duration_ms=bench_duration_ms,
            workspace_dir=str(workspace),
        )
        token_usage_record = build_token_usage_record(usage)

        job_id: str | None = None
        if attempt is not None:
            job_id = f"J{uuid.uuid4().hex}"
            _session_store.create_grading_job(
                job_id=job_id,
                attempt_id=attempt["attempt_id"],
                token=token,
                session_id=session_id,
                run_id=run_id,
                question_id=question_id,
                created_at=t_submit,
            )

        def _on_grade_done(fut: Any) -> None:
            try:
                record = fut.result()
            except Exception as exc:
                _logger.error("grading callback error  %s  %s", question_id, exc)
                return
            record_json = json.dumps(record.model_dump(mode="json"), ensure_ascii=False, default=str)
            with _results_lock:
                _results[composite_key] = (token, session_id, record)
                _grading_pending.discard(composite_key)
            _session_store.save_result(composite_key, token, session_id, record_json)
            if attempt is not None:
                final_status = "failed" if record.run_status == "grading_error" else "completed"
                _session_store.update_attempt_status(attempt["attempt_id"], final_status)
                _session_store.update_grading_job(
                    job_id, final_status, "grading failed" if final_status == "failed" else ""
                )

        with _results_lock:
            _grading_pending.add(composite_key)
        if job_id is not None:
            _session_store.update_grading_job(job_id, "running")
        with _submission_counts_lock:
            _submission_counts[composite_key] = _submission_counts.get(composite_key, 0) + 1

        fut = _grading_executor.submit(
            functools.partial(
                _do_grade,
                question,
                answer,
                evidence,
                token_usage_record,
                run_status,
                model_name,
                bench_duration_ms,
            )
        )
        fut.add_done_callback(_on_grade_done)

        return {
            "attempt_id": attempt["attempt_id"] if attempt is not None else None,
            "job_id": job_id,
            "question_id": question_id,
            "session_id": session_id,
            "run_id": run_id,
            "status": "grading",
        }

    @app.get("/grading-jobs/{job_id}")
    async def get_grading_job(
        job_id: str,
        token: str = Depends(_require_token),
    ) -> dict:
        """Return durable grading-job state for the authenticated owner."""
        job = _session_store.get_grading_job(job_id)
        if job is None or job["token"] != token:
            raise HTTPException(404, detail=f"Grading job '{job_id}' not found")
        return {key: value for key, value in job.items() if key != "token"}

    @app.get("/runs/{run_id}/attempts/{attempt_id}")
    async def get_attempt(
        run_id: str,
        attempt_id: str,
        token: str = Depends(_require_token),
    ) -> dict:
        """Return a durable run submission status."""
        _owned_run(run_id, token)
        attempt = _session_store.get_attempt(attempt_id)
        if attempt is None or attempt["run_id"] != run_id or attempt["token"] != token:
            raise HTTPException(404, detail=f"Attempt '{attempt_id}' not found")
        return {
            key: value
            for key, value in attempt.items()
            if key != "token"
        }

    # ------------------------------------------------------------------
    # Results endpoints
    # ------------------------------------------------------------------

    @app.get("/results/{question_id}")
    async def get_result(
        question_id: str,
        session_id: str | None = Query(default=None),
        run_id: str | None = Query(default=None),
        token: str = Depends(_require_token),
    ) -> list[dict]:
        """Get grading record(s) for a submitted question.

        If run_id or session_id is given, returns a single-element list for that
        scope. Run-scoped lookup is preferred for run submissions.
        Otherwise returns all records for this question across all sessions of
        the authenticated token.
        """
        registry = _require_registry()
        if question_id not in registry:
            raise HTTPException(404, detail=f"Question '{question_id}' not found")
        if run_id is not None:
            run = _owned_run(run_id, token)
            if question_id not in run.question_ids:
                raise HTTPException(404, detail=f"Question '{question_id}' is not activated in run '{run_id}'")
        with _results_lock:
            if run_id is not None:
                key = f"run:{run_id}:{question_id}"
                entry = _results.get(key)
                matches = [entry[2]] if entry is not None else []
                pending = not matches and key in _grading_pending
            elif session_id is not None:
                key = f"{token}:{session_id}:{question_id}"
                entry = _results.get(key)
                matches = [entry[2]] if entry is not None else []
                pending = not matches and key in _grading_pending
            else:
                matches = [
                    rec
                    for tok, sid, rec in _results.values()
                    if tok == token and rec.question_id == question_id
                ]
                pending = not matches and any(
                    k.endswith(f":{question_id}") and k.startswith(f"{token}:")
                    for k in _grading_pending
                )
        if pending:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=202, content={"status": "grading", "question_id": question_id})
        if not matches:
            raise HTTPException(404, detail=f"No result found for '{question_id}'")
        return [
            {
                "question_id": r.question_id,
                "capability": r.capabilities,
                "domain": r.domain,
                "run_status": r.run_status,
                "passed": r.total_count > 0 and r.passed_count >= r.total_count,
                "passed_count": r.passed_count,
                "total_count": r.total_count,
                "overall_weighted_score": r.overall_weighted_score,
                "criteria_results": {
                    cid: {
                        "criterion_id": cr.criterion_id,
                        "capability": cr.capability,
                        "passed": cr.passed,
                        "reason": cr.reason,
                    }
                    for cid, cr in r.criteria_results.items()
                },
            }
            for r in matches
        ]

    @app.get("/results")
    async def get_results(
        session_id: str | None = Query(default=None),
        token: str = Depends(_require_token),
    ) -> dict:
        """Get aggregate summary of submitted results for the authenticated token."""
        with _results_lock:
            if session_id is not None:
                records = [
                    rec
                    for tok, sid, rec in _results.values()
                    if tok == token and sid == session_id
                ]
            else:
                records = [rec for tok, sid, rec in _results.values() if tok == token]
        if not records:
            return {"total": 0, "results": []}
        registry = _require_registry()
        hosted_question_ids = set(registry.list_question_ids())
        records = [rec for rec in records if rec.question_id in hosted_question_ids]
        if not records:
            return {"total": 0, "results": []}
        total_q = len(registry.list_questions())
        summary = build_summary(records, total_q)
        return {
            "total": len(records),
            "questions_passed": summary.questions_passed,
            "pass_rate": summary.pass_rate,
            "weighted_pass_rate": summary.weighted_pass_rate,
            "results": [
                {
                    "question_id": r.question_id,
                    "capability": r.capabilities,
                    "domain": r.domain,
                    "run_status": r.run_status,
                    "passed_count": r.passed_count,
                    "total_count": r.total_count,
                    "overall_weighted_score": r.overall_weighted_score,
                }
                for r in records
            ],
        }

except ImportError:
    # fastapi not installed — module importable but app unavailable
    app = None  # type: ignore[assignment]
