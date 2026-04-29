"""FastAPI server for harness-less mat-bench evaluation.

Exposes the question bank over HTTP so any agent with HTTP access can
run the benchmark without a local harness.

Endpoints::

    POST /token                           Register a new persistent API token
    POST /sessions                        Create a new session (requires X-API-Token header)
    GET  /questions                       List questions (filter: capability, domain, limit)
    GET  /questions/{id}                  Full question details + data file list
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

import asyncio
import functools
import json
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .evaluation.evidence import (
    ArtifactRecord,
    CallStatus,
    EvidenceBundle,
    ToolCallRecord,
    TokenUsage,
)
from .evaluation.grade import grade_question
from .harness import build_token_usage_record
from .registry import Registry
from .reporting.aggregator import build_summary
from .schemas import EvalRunRecord, LLMConfig, TokenUsageRecord

# ---------------------------------------------------------------------------
# Server-internal models
# ---------------------------------------------------------------------------


class TokenRecord(BaseModel):
    token: str
    created_at: datetime


class SessionRecord(BaseModel):
    session_id: str  # e.g. "S0001"
    token: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Module-level server state (set by init_server before uvicorn starts)
# ---------------------------------------------------------------------------

_registry: Registry | None = None
_llm_cfg: LLMConfig | None = None
_output_dir: Path | None = None
_grading_executor: ThreadPoolExecutor | None = None

_tokens: dict[str, TokenRecord] = {}
_tokens_lock = threading.Lock()

_sessions: dict[str, SessionRecord] = {}
_session_counter: int = 0
_sessions_lock = threading.Lock()

# key = f"{token}:{session_id}:{question_id}" → (token, session_id, record)
_results: dict[str, tuple[str, str, EvalRunRecord]] = {}
_results_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Disk persistence helpers
# ---------------------------------------------------------------------------


def _load_tokens() -> None:
    """Load tokens from disk. Called once from init_server."""
    global _tokens
    if _output_dir is None:
        return
    path = _output_dir / "tokens.json"
    if not path.exists():
        return
    with _tokens_lock:
        data = json.loads(path.read_text(encoding="utf-8"))
        _tokens = {
            entry["token"]: TokenRecord(**entry)
            for entry in data.get("tokens", [])
        }


def _flush_tokens() -> None:
    """Write _tokens to disk. Caller must hold _tokens_lock."""
    if _output_dir is None:
        return
    path = _output_dir / "tokens.json"
    payload = {"tokens": [t.model_dump(mode="json") for t in _tokens.values()]}
    path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")


def _load_sessions() -> None:
    """Load sessions and counter from disk. Called once from init_server."""
    global _sessions, _session_counter
    if _output_dir is None:
        return
    path = _output_dir / "sessions.json"
    if not path.exists():
        return
    with _sessions_lock:
        data = json.loads(path.read_text(encoding="utf-8"))
        _session_counter = int(data.get("counter", 0))
        _sessions = {
            entry["session_id"]: SessionRecord(**entry)
            for entry in data.get("sessions", [])
        }


def _flush_sessions() -> None:
    """Write _sessions and _session_counter to disk. Caller must hold _sessions_lock."""
    if _output_dir is None:
        return
    path = _output_dir / "sessions.json"
    payload = {
        "counter": _session_counter,
        "sessions": [s.model_dump(mode="json") for s in _sessions.values()],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")


def _flush_results() -> None:
    """Write all results to raw_runs.jsonl. Caller must hold _results_lock."""
    if _output_dir is None:
        return
    raw_runs_path = _output_dir / "raw_runs.jsonl"
    with raw_runs_path.open("w", encoding="utf-8") as f:
        for token, session_id, record in _results.values():
            row = record.model_dump(mode="json")
            row["_token"] = token
            row["_session_id"] = session_id
            f.write(json.dumps(row, ensure_ascii=False, default=str))
            f.write("\n")


# ---------------------------------------------------------------------------
# Server initialisation
# ---------------------------------------------------------------------------


def init_server(
    registry: Registry,
    output_dir: Path,
    llm_cfg: LLMConfig | None = None,
    grading_workers: int = 4,
) -> None:
    """Initialise server state. Must be called before uvicorn.run()."""
    global _registry, _llm_cfg, _output_dir, _results, _grading_executor
    _registry = registry
    _llm_cfg = llm_cfg
    _output_dir = output_dir
    _results = {}
    output_dir.mkdir(parents=True, exist_ok=True)

    _load_tokens()
    _load_sessions()

    _grading_executor = ThreadPoolExecutor(max_workers=grading_workers)


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
        )
        return report.record
    except Exception as exc:
        import sys
        print(f"  [{question_id}] grading error: {exc}", file=sys.stderr)
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
    from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
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
        token_str = secrets.token_hex(32)
        record = TokenRecord(token=token_str, created_at=datetime.now(timezone.utc))
        with _tokens_lock:
            _tokens[token_str] = record
            _flush_tokens()
        return {"token": token_str, "created_at": record.created_at.isoformat()}

    @app.post("/sessions")
    async def create_session(token: str = Depends(_require_token)) -> dict:
        """Create a new session for the authenticated token."""
        global _session_counter
        with _sessions_lock:
            _session_counter += 1
            session_id = f"S{_session_counter:04d}"
            record = SessionRecord(
                session_id=session_id,
                token=token,
                created_at=datetime.now(timezone.utc),
            )
            _sessions[session_id] = record
            _flush_sessions()
        return {"session_id": session_id, "created_at": record.created_at.isoformat()}

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
    async def get_question(question_id: str) -> dict:
        """Get full question details: prompt, data file list, tags."""
        registry = _require_registry()
        try:
            q = registry.get_question(question_id)
        except KeyError:
            raise HTTPException(404, detail=f"Question '{question_id}' not found")
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

        # Parse multipart form
        try:
            form_data = await request.form()
        except Exception as exc:
            raise HTTPException(400, detail=f"Failed to parse form data: {exc}")

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
        model_name = str(meta.get("model_name", "unknown"))
        num_turns = int(meta.get("num_turns") or 0)
        duration_ms = int(meta.get("duration_ms") or 0)
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
            duration_ms=duration_ms,
            workspace_dir=str(workspace),
        )
        token_usage_record = build_token_usage_record(usage)

        # Grade in thread pool (parallel-safe)
        loop = asyncio.get_running_loop()
        record = await loop.run_in_executor(
            _grading_executor,
            functools.partial(
                _do_grade,
                question,
                answer,
                evidence,
                token_usage_record,
                run_status,
                model_name,
                duration_ms,
            ),
        )

        composite_key = f"{token}:{session_id}:{question_id}"
        with _results_lock:
            _results[composite_key] = (token, session_id, record)
            _flush_results()

        return {
            "question_id": record.question_id,
            "session_id": session_id,
            "run_status": record.run_status,
            "passed_count": record.passed_count,
            "total_count": record.total_count,
            "overall_weighted_score": record.overall_weighted_score,
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
            else:
                matches = [
                    rec
                    for tok, sid, rec in _results.values()
                    if tok == token and rec.question_id == question_id
                ]
        if not matches:
            raise HTTPException(404, detail=f"No result found for '{question_id}'")
        return [r.model_dump(mode="json") for r in matches]

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
        summary = build_summary(records)
        return {
            "total": len(records),
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
