"""FastAPI server for harness-less mat-bench evaluation.

Exposes the question bank over HTTP so any agent with HTTP access can
run the benchmark without a local harness.

Endpoints::

    POST /token                           Register a new persistent API token
    POST /sessions                        Create a new session (requires X-API-Token header)
    GET  /questions                       List questions (filter: capability, domain, limit)
    GET  /questions/{id}                  Full question details + data file list (requires session_id; starts duration timer)
    GET  /questions/{id}/data/{fname}     Download a data file
    POST /submit/{id}?session_id=S0001    Submit result files + metadata (multipart)
    GET  /results/{id}                    Grading result(s) for one question
    GET  /results                         Summary of all submitted results

Authentication:
    All /submit and /results endpoints require the X-API-Token header.
    Obtain a token via POST /token, then create a session via POST /sessions.

Submission form fields:
    meta      JSON string with answer, model_name, num_turns, duration_ms,
              usage (prompt/completion/total_tokens), tool_calls list, is_error
    <any>     File fields — filename attribute is used as the workspace filename

Tool call format in meta.tool_calls::

    [{"step": 1, "tool_name": "bash", "args": {"command": "..."},
      "observation_excerpt": "...", "succeeded": true}, ...]
"""

from __future__ import annotations

import functools
import json
import logging
import random
import secrets
import string
import threading
import time
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


# ---------------------------------------------------------------------------
# Module-level server state (set by init_server before uvicorn starts)
# ---------------------------------------------------------------------------

_registry: Registry | None = None
_llm_cfg: LLMConfig | None = None
_output_dir: Path | None = None
_grading_executor: ThreadPoolExecutor | None = None
_parallel_checklist_workers: int = 1

_tokens: dict[str, TokenRecord] = {}
_tokens_lock = threading.Lock()

_sessions: dict[str, SessionRecord] = {}
_sessions_lock = threading.Lock()

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

_token_store: "TokenStore | None" = None
_session_store: "SessionStore | None" = None

# When False, POST /token returns 403 (set by UI in combined mode to force human registration)
_allow_direct_registration: bool = True


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
    allow_direct_registration: bool = True,
) -> None:
    """Initialise server state. Must be called before uvicorn.run()."""
    global _registry, _llm_cfg, _output_dir, _results, _grading_executor
    global _token_store, _session_store, _task_starts, _parallel_checklist_workers
    global _grading_pending, _submission_counts, _max_submissions_per_question
    global _allow_direct_registration
    _allow_direct_registration = allow_direct_registration
    _registry = registry
    _llm_cfg = llm_cfg
    _output_dir = output_dir
    _results = {}
    _task_starts = {}
    _grading_pending = set()
    _submission_counts = {}
    _max_submissions_per_question = max(0, int(max_submissions_per_question))
    _parallel_checklist_workers = max(1, int(parallel_checklist_workers))
    output_dir.mkdir(parents=True, exist_ok=True)

    from .store import TokenStore, SessionStore
    _store_dir = store_dir or Path.home() / ".matbench"
    _token_store = TokenStore(_store_dir / "tokens.db")
    _session_store = SessionStore(_store_dir / "sessions.db")

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
    from fastapi.responses import FileResponse

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

    @app.post("/token")
    async def create_token() -> dict:
        """Register a new persistent API token. No authentication required."""
        if not _allow_direct_registration:
            raise HTTPException(
                403,
                detail="Direct token registration is disabled. Register via the web UI.",
            )
        token_str = secrets.token_hex(32)
        record = TokenRecord(token=token_str, created_at=datetime.now(timezone.utc))
        with _tokens_lock:
            _tokens[token_str] = record
        _token_store.save_token(token_str, record.created_at)
        return {"token": token_str, "created_at": record.created_at.isoformat()}

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

    # ------------------------------------------------------------------
    # Question endpoints (no auth required)
    # ------------------------------------------------------------------

    @app.get("/questions")
    async def list_questions(
        capability: str | None = None,
        domain: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """List available questions with optional filters."""
        registry = _require_registry()
        questions = registry.list_questions(capability=capability, domain=domain)
        if limit is not None:
            questions = questions[:limit]
        return [
            {
                "id": q.id,
                "capability": q.capability,
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

    @app.post("/submit/{question_id}")
    async def submit(
        question_id: str,
        request: Request,
        session_id: str = Query(..., description="Session ID from POST /sessions"),
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

        # Validate session ownership
        with _sessions_lock:
            session = _sessions.get(session_id)
        if session is None:
            raise HTTPException(404, detail=f"Session '{session_id}' not found")
        if session.token != token:
            raise HTTPException(403, detail="Session does not belong to this token")

        # Enforce per-question submission limit within a session (read-only check)
        composite_key = f"{token}:{session_id}:{question_id}"
        if _max_submissions_per_question > 0:
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

        # Compute bench-measured duration from question fetch time
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
                content = await value.read()  # type: ignore[union-attr]
                submitted[value.filename] = content  # type: ignore[union-attr]

        # Write files to workspace (scoped by session)
        if _output_dir is None:
            raise HTTPException(500, detail="Server output directory not configured")
        workspace = _output_dir / "workspaces" / session_id / question_id
        workspace.mkdir(parents=True, exist_ok=True)

        input_fnames = {Path(df.path).name for df in question.item.data_files}
        artifacts: list[ArtifactRecord] = []
        for fname, content in submitted.items():
            (workspace / fname).write_bytes(content)
            if fname not in input_fnames:
                artifacts.append(
                    ArtifactRecord(
                        path=fname,
                        artifact_type=Path(fname).suffix.lstrip(".") or "unknown",
                        size_bytes=len(content),
                    )
                )

        # Extract metadata fields
        answer = str(meta.get("answer", ""))
        model_name = session.model_name  # locked at session creation; meta's model_name is ignored
        num_turns = int(meta.get("num_turns") or 0)
        is_error = bool(meta.get("is_error", False))
        usage: dict[str, Any] = meta.get("usage") or {}
        run_status = "error" if is_error else "completed"

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

        with _results_lock:
            _grading_pending.add(composite_key)
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
            "question_id": question_id,
            "session_id": session_id,
            "status": "grading",
        }

    # ------------------------------------------------------------------
    # Results endpoints
    # ------------------------------------------------------------------

    @app.get("/results/{question_id}")
    async def get_result(
        question_id: str,
        session_id: str | None = Query(default=None),
        token: str = Depends(_require_token),
    ) -> list[dict]:
        """Get grading record(s) for a submitted question.

        If session_id is given, returns a single-element list for that session.
        Otherwise returns all records for this question across all sessions of
        the authenticated token.
        """
        with _results_lock:
            if session_id is not None:
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
                "capability": r.capability,
                "domain": r.domain,
                "run_status": r.run_status,
                "passed": r.total_count > 0 and r.passed_count >= r.total_count,
                "passed_count": r.passed_count,
                "total_count": r.total_count,
                "overall_weighted_score": r.overall_weighted_score,
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
                    "capability": r.capability,
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
