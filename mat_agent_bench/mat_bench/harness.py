"""Benchmark harness for mat-agent-bench.

Provides common orchestration logic for running any agent against the
question bank: workspace setup, agent invocation, artifact scanning,
evidence building, grading, and JSONL output.

Agent scripts follow a simple contract::

    ./my_agent.sh <workspace_dir> <prompt_file> <output_file>

The agent reads the prompt, runs in the workspace, and writes a JSON
result to output_file. See ``agents/example.sh`` for a template.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .evaluation.evidence import ArtifactRecord, EvidenceBundle, TokenUsage
from .evaluation.grade import grade_question, grade_run
from .registry import Question, Registry
from .schemas import EvalRunRecord, LLMConfig, TokenUsageRecord

_REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Workspace management
# ---------------------------------------------------------------------------


def setup_workspace(question: Question, workspace_dir: Path, qb_root: Path) -> None:
    """Create workspace directory and copy data files into it."""
    workspace_dir.mkdir(parents=True, exist_ok=True)
    for df in question.item.data_files:
        src = question.data_file_path(df.key)
        # Fallback: try resolving relative to question bank root
        if (src is None or not src.exists()) and qb_root:
            candidate = qb_root / df.path
            if candidate.exists():
                src = candidate
        if src is None or not src.exists():
            print(
                f"  warning: data file {df.key} ({df.path}) not found",
                file=sys.stderr,
            )
            continue
        dst = workspace_dir / Path(df.path).name
        shutil.copy2(str(src), str(dst))


def scan_workspace_artifacts(
    workspace_dir: Path, pre_existing: set[str]
) -> list[ArtifactRecord]:
    """Scan workspace for files created/modified by the agent."""
    artifacts = []
    for p in workspace_dir.rglob("*"):
        if not p.is_file():
            continue
        rel = str(p.relative_to(workspace_dir))
        if rel.startswith("_") or rel.startswith("."):
            continue
        if rel in pre_existing:
            continue
        artifacts.append(
            ArtifactRecord(
                path=rel,
                artifact_type=p.suffix.lstrip(".") or "unknown",
                size_bytes=p.stat().st_size,
            )
        )
    return artifacts


def snapshot_pre_existing(workspace_dir: Path) -> set[str]:
    """Record files present in workspace before agent runs."""
    pre = set()
    for p in workspace_dir.rglob("*"):
        if p.is_file():
            pre.add(str(p.relative_to(workspace_dir)))
    return pre


# ---------------------------------------------------------------------------
# Agent invocation
# ---------------------------------------------------------------------------


def run_agent_script(
    agent_script: Path,
    workspace: Path,
    prompt: str,
    *,
    timeout_seconds: int = 600,
    env_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Invoke an agent shell script and return the parsed JSON result.

    The script is called as::

        <agent_script> <workspace_dir> <prompt_file> <output_file>

    It must write a JSON object to ``<output_file>`` with at least an
    ``answer`` field.

    Returns
    -------
    dict
        Parsed JSON from the agent's output file, with defaults applied.
    """
    prompt_file = workspace / "_prompt.txt"
    output_file = workspace / "_agent_output.json"

    prompt_file.write_text(prompt, encoding="utf-8")
    # Remove stale output
    output_file.unlink(missing_ok=True)

    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)

    result = subprocess.run(
        [str(agent_script), str(workspace), str(prompt_file), str(output_file)],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        env=env,
    )

    if not output_file.is_file():
        stderr_snippet = result.stderr[:500] if result.stderr else ""
        return {
            "answer": "",
            "is_error": True,
            "error_detail": (
                f"agent script exited with code {result.returncode} "
                f"and did not write output file; stderr: {stderr_snippet}"
            ),
            "model_name": agent_script.stem,
            "num_turns": 0,
            "usage": {},
        }

    try:
        data = json.loads(output_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {
            "answer": "",
            "is_error": True,
            "error_detail": f"failed to parse agent output JSON: {exc}",
            "model_name": agent_script.stem,
            "num_turns": 0,
            "usage": {},
        }

    # Apply defaults
    data.setdefault("answer", "")
    data.setdefault("is_error", result.returncode != 0)
    data.setdefault("model_name", agent_script.stem)
    data.setdefault("num_turns", 0)
    data.setdefault("usage", {})
    if result.returncode != 0 and not data.get("is_error"):
        data["is_error"] = True
    return data


# ---------------------------------------------------------------------------
# Evidence + token usage builders
# ---------------------------------------------------------------------------


def build_evidence_bundle(
    question_id: str,
    answer: str,
    *,
    artifacts: list[ArtifactRecord],
    model_name: str | None,
    usage: dict[str, Any],
    num_turns: int,
    run_status: str,
    duration_ms: int,
    workspace_dir: str,
) -> EvidenceBundle:
    """Build an EvidenceBundle from agent output."""
    return EvidenceBundle(
        task_id=question_id,
        final_answer=answer[:8000],
        artifacts=artifacts,
        model_name=model_name,
        token_usage_run=TokenUsage.from_usage_dict(usage),
        total_steps=num_turns,
        run_status=run_status,
        duration_ms=duration_ms,
        workspace_dir=workspace_dir,
    )


def build_token_usage_record(usage: dict[str, Any]) -> TokenUsageRecord:
    """Normalize an agent's usage dict into a TokenUsageRecord."""
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or 0)
    cache_read = int(
        usage.get("cache_read_tokens")
        or usage.get("cache_read_input_tokens")
        or 0
    )
    if total_tokens == 0 and (prompt_tokens or completion_tokens):
        total_tokens = prompt_tokens + completion_tokens
    return TokenUsageRecord(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cache_read_tokens=cache_read,
        total_tokens_effective=total_tokens,
    )


# ---------------------------------------------------------------------------
# Single question runner
# ---------------------------------------------------------------------------


def run_single_question(
    question: Question,
    *,
    agent_script: Path,
    output_dir: Path,
    qb_root: Path,
    timeout_seconds: int = 600,
    mode: str = "direct",
    repeat_idx: int = 0,
    llm_cfg: LLMConfig | None = None,
    skip_grading: bool = False,
    env_overrides: dict[str, str] | None = None,
    print_lock: threading.Lock | None = None,
) -> EvalRunRecord | None:
    """Run one question through an agent script and optionally grade it."""
    qid = question.id

    def _log(msg: str) -> None:
        line = f"  [{qid}] {msg}"
        if print_lock:
            with print_lock:
                print(line, file=sys.stderr)
        else:
            print(line, file=sys.stderr)

    workspace = output_dir / "workspaces" / qid
    setup_workspace(question, workspace, qb_root)
    pre_existing = snapshot_pre_existing(workspace)

    prompt = question.item.human_prompt_seed.strip()
    _log("running agent...")
    t0 = time.monotonic()

    try:
        agent_result = run_agent_script(
            agent_script,
            workspace,
            prompt,
            timeout_seconds=timeout_seconds,
            env_overrides=env_overrides,
        )
    except subprocess.TimeoutExpired:
        _log(f"TIMEOUT ({timeout_seconds}s)")
        agent_result = {
            "answer": "",
            "is_error": True,
            "error_detail": f"timeout after {timeout_seconds}s",
            "model_name": agent_script.stem,
            "num_turns": 0,
            "usage": {},
        }
    except Exception as exc:
        _log(f"ERROR: {exc}")
        agent_result = {
            "answer": "",
            "is_error": True,
            "error_detail": str(exc),
            "model_name": agent_script.stem,
            "num_turns": 0,
            "usage": {},
        }

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    duration_ms = int(agent_result.get("duration_ms") or elapsed_ms)
    is_error = agent_result.get("is_error", False)
    answer = str(agent_result.get("answer", ""))
    num_turns = int(agent_result.get("num_turns") or 0)
    model_name = str(agent_result.get("model_name", agent_script.stem))
    usage = agent_result.get("usage") or {}
    run_status = "error" if is_error else "completed"

    tag = "OK" if not is_error else "FAIL"
    _log(f"{tag} ({elapsed_ms / 1000:.0f}s, turns={num_turns})")

    # Build evidence
    artifacts = scan_workspace_artifacts(workspace, pre_existing)
    evidence = build_evidence_bundle(
        qid,
        answer,
        artifacts=artifacts,
        model_name=model_name,
        usage=usage,
        num_turns=num_turns,
        run_status=run_status,
        duration_ms=duration_ms,
        workspace_dir=str(workspace),
    )
    token_usage = build_token_usage_record(usage)

    if skip_grading:
        return EvalRunRecord(
            question_id=qid,
            capability=question.capability,
            domain=question.domain,
            mode=mode,  # type: ignore[arg-type]
            repeat_idx=repeat_idx,
            prompt=prompt,
            answer=answer,
            run_status=run_status,
            model_name=model_name,
            token_usage=token_usage,
            duration_ms=duration_ms,
            created_at=datetime.now(timezone.utc),
        )

    # Grade
    _log("grading...")
    try:
        report = grade_question(
            question=question,
            answer=answer,
            evidence=evidence,
            llm_cfg=llm_cfg,
            mode=mode,
            repeat_idx=repeat_idx,
            prompt=prompt,
            run_status=run_status,
            model_name=model_name,
            token_usage=token_usage,
            duration_ms=duration_ms,
        )
        _log(f"score={report.score:.3f} ({report.detail})")
        return report.record
    except Exception as exc:
        _log(f"grading error: {exc}")
        return EvalRunRecord(
            question_id=qid,
            capability=question.capability,
            domain=question.domain,
            mode=mode,  # type: ignore[arg-type]
            repeat_idx=repeat_idx,
            prompt=prompt,
            answer=answer,
            run_status="grading_error",
            model_name=model_name,
            token_usage=token_usage,
            duration_ms=duration_ms,
            created_at=datetime.now(timezone.utc),
        )


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------


def parse_llm_judge_config(llm_judge_str: str | None = None) -> LLMConfig:
    """Build an LLMConfig from environment variables.

    Env vars:
        MAT_BENCH_LLM_PROVIDER  (default: "openai")
        MAT_BENCH_LLM_MODEL     (required)
        MAT_BENCH_LLM_API_KEY   (required)
        MAT_BENCH_LLM_BASE_URL  (optional)
        MAT_BENCH_LLM_TEMPERATURE (optional, default: 0.0)
        MAT_BENCH_LLM_MAX_TOKENS  (optional, default: 4096)
        MAT_BENCH_LLM_TIMEOUT     (optional, default: 180)

    The *llm_judge_str* parameter (``PROVIDER/MODEL`` from ``--llm-judge``)
    is accepted for CLI convenience and overrides the provider/model env vars.
    """
    #provider = os.environ.get("MAT_BENCH_LLM_PROVIDER", "openai")
    model = os.environ.get("MAT_BENCH_LLM_MODEL", "")
    api_key = os.environ.get("MAT_BENCH_LLM_API_KEY", "")

    # --llm-judge PROVIDER/MODEL overrides env vars
    if llm_judge_str:
        parts = llm_judge_str.split("/", 1)
        if len(parts) != 2:
            raise ValueError(
                "--llm-judge must be PROVIDER/MODEL "
                "(e.g. anthropic/claude-sonnet-4-20250514)"
            )
        model=llm_judge_str

    if not model:
        raise ValueError(
            "MAT_BENCH_LLM_MODEL not set and no --llm-judge provided"
        )
    if not api_key:
        raise ValueError("MAT_BENCH_LLM_API_KEY not set")

    return LLMConfig(
        model=model,
        api_key=api_key,
        base_url=os.environ.get("MAT_BENCH_LLM_BASE_URL") or None,
        temperature=float(os.environ.get("MAT_BENCH_LLM_TEMPERATURE", "0.0")),
        max_tokens=int(os.environ.get("MAT_BENCH_LLM_MAX_TOKENS", "4096")),
        timeout=int(os.environ.get("MAT_BENCH_LLM_TIMEOUT", "180")),
    )


def parse_agent_env(env_args: list[str] | None) -> dict[str, str]:
    """Parse ``KEY=VALUE`` pairs from --agent-env into a dict."""
    if not env_args:
        return {}
    env = {}
    for item in env_args:
        if "=" not in item:
            raise ValueError(f"--agent-env must be KEY=VALUE, got: {item!r}")
        k, v = item.split("=", 1)
        env[k] = v
    return env


def build_cli_parser() -> argparse.ArgumentParser:
    """Build the shared CLI argument parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Run mat-agent-bench with any agent. "
            "The agent is a shell script invoked per-question."
        ),
    )
    parser.add_argument(
        "--agent",
        type=str,
        required=True,
        help="Path to agent shell script (e.g. agents/claude_code.sh).",
    )
    parser.add_argument(
        "--agent-env",
        nargs="*",
        metavar="KEY=VALUE",
        help="Extra environment variables passed to the agent script.",
    )
    parser.add_argument(
        "--question-bank-dir",
        type=str,
        default="question_bank",
        help="Path to question_bank directory (default: question_bank).",
    )
    parser.add_argument(
        "--questions",
        nargs="*",
        help="Specific question ID(s) to run.",
    )
    parser.add_argument(
        "--capability",
        type=str,
        default=None,
        help="Filter by capability (e.g. input_generation).",
    )
    parser.add_argument(
        "--domain",
        type=str,
        default=None,
        help="Filter by domain (e.g. agnostic, battery).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Run only the first N questions.",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Output directory (default: runs/<agent>_<timestamp>).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Timeout in seconds per question (default: 600).",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="direct",
        choices=["direct", "planner"],
        help="Evaluation mode (default: direct).",
    )
    parser.add_argument(
        "--llm-judge",
        type=str,
        default=None,
        metavar="PROVIDER/MODEL",
        help=(
            "LLM judge for llm_binary_judge criteria, e.g. "
            "'anthropic/claude-sonnet-4-20250514'. "
            "API key from ANTHROPIC_API_KEY / OPENAI_API_KEY env var."
        ),
    )
    parser.add_argument(
        "--skip-grading",
        action="store_true",
        help="Skip grading — just run and output pre-grading JSONL.",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Generate full reports after grading.",
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=1,
        help="Concurrent tasks (default: 1).",
    )
    return parser


# ---------------------------------------------------------------------------
# Top-level benchmark runner
# ---------------------------------------------------------------------------


def run_benchmark(args: argparse.Namespace) -> int:
    """Execute the full benchmark pipeline. Returns exit code."""
    # Resolve agent script
    agent_script = Path(args.agent)
    if not agent_script.is_absolute():
        agent_script = _REPO_ROOT / agent_script
    if not agent_script.is_file():
        print(f"error: agent script not found: {agent_script}", file=sys.stderr)
        return 1
    if not os.access(str(agent_script), os.X_OK):
        print(
            f"error: agent script is not executable: {agent_script}", file=sys.stderr
        )
        return 1

    # Parse agent env
    try:
        env_overrides = parse_agent_env(args.agent_env)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # Load question bank
    qb_dir = Path(args.question_bank_dir)
    if not qb_dir.is_absolute():
        qb_dir = _REPO_ROOT / qb_dir
    registry = Registry(qb_dir)
    print(f"Loaded {len(registry)} questions from {qb_dir}", file=sys.stderr)

    # Select questions
    if args.questions:
        questions: list[Question] = []
        for qid in args.questions:
            try:
                questions.append(registry.get_question(qid))
            except KeyError as exc:
                print(f"warning: {exc}", file=sys.stderr)
    else:
        questions = registry.list_questions(
            capability=args.capability,
            domain=args.domain,
        )

    if args.limit:
        questions = questions[: args.limit]

    if not questions:
        print("error: no questions selected", file=sys.stderr)
        return 1
    print(f"Selected {len(questions)} questions", file=sys.stderr)

    # Output directory
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        agent_name = agent_script.stem
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = _REPO_ROOT / "runs" / f"{agent_name}_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output: {output_dir}", file=sys.stderr)

    # LLM judge config
    llm_cfg = None
    if not args.skip_grading:
        has_env = os.environ.get("MAT_BENCH_LLM_MODEL") and os.environ.get("MAT_BENCH_LLM_API_KEY")
        if args.llm_judge or has_env:
            try:
                llm_cfg = parse_llm_judge_config(args.llm_judge or None)
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1

    # Run questions
    records: list[EvalRunRecord] = []
    print_lock = threading.Lock() if args.jobs > 1 else None
    n_total = len(questions)

    def _run_one(idx: int, q: Question) -> EvalRunRecord | None:
        tag = f"[{idx + 1}/{n_total}]"
        if print_lock:
            with print_lock:
                print(
                    f"{tag} {q.id} ({q.capability}/{q.domain})", file=sys.stderr
                )
        else:
            print(f"{tag} {q.id} ({q.capability}/{q.domain})", file=sys.stderr)
        return run_single_question(
            q,
            agent_script=agent_script,
            output_dir=output_dir,
            qb_root=qb_dir,
            timeout_seconds=args.timeout,
            mode=args.mode,
            llm_cfg=llm_cfg,
            skip_grading=args.skip_grading,
            env_overrides=env_overrides,
            print_lock=print_lock,
        )

    if args.jobs <= 1:
        for i, q in enumerate(questions):
            rec = _run_one(i, q)
            if rec:
                records.append(rec)
    else:
        workers = min(args.jobs, len(questions))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_map = {
                pool.submit(_run_one, i, q): i for i, q in enumerate(questions)
            }
            results_indexed: list[tuple[int, EvalRunRecord | None]] = []
            for fut in as_completed(future_map):
                idx = future_map[fut]
                try:
                    rec = fut.result()
                except Exception as exc:
                    print(f"error in task {idx}: {exc}", file=sys.stderr)
                    rec = None
                results_indexed.append((idx, rec))
            for _, rec in sorted(results_indexed):
                if rec:
                    records.append(rec)

    if not records:
        print("error: no records produced", file=sys.stderr)
        return 1

    # Write raw_runs.jsonl
    raw_runs_path = output_dir / "raw_runs.jsonl"
    with raw_runs_path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec.model_dump(), ensure_ascii=False, default=str))
            f.write("\n")
    print(f"\nWrote {len(records)} records to {raw_runs_path}", file=sys.stderr)

    # Summary stats
    n_ok = sum(1 for r in records if r.run_status == "completed")
    n_fail = len(records) - n_ok
    print(f"Completed: {n_ok}, Failed: {n_fail}", file=sys.stderr)

    if not args.skip_grading:
        total_passed = sum(r.passed_count for r in records)
        total_criteria = sum(r.total_count for r in records)
        avg_score = (
            sum(r.overall_weighted_score for r in records) / len(records)
            if records
            else 0
        )
        print(
            f"Criteria: {total_passed}/{total_criteria} passed, "
            f"avg weighted score: {avg_score:.3f}",
            file=sys.stderr,
        )

    # Generate reports
    if args.report and not args.skip_grading:
        print("\nGenerating reports...", file=sys.stderr)
        try:
            run_report = grade_run(
                raw_runs_path=raw_runs_path,
                output_dir=output_dir,
                prefix="",
            )
            print(f"Pass rate: {run_report.pass_rate:.1%}", file=sys.stderr)
            print(
                f"Weighted pass rate: {run_report.weighted_pass_rate:.3f}",
                file=sys.stderr,
            )
            for label, path in run_report.report_paths.items():
                print(f"  {label}: {path}", file=sys.stderr)
        except Exception as exc:
            print(f"report generation error: {exc}", file=sys.stderr)

    print(f"\nDone. Results in: {output_dir}", file=sys.stderr)
    print(f"To grade/re-grade: mat-bench grade {raw_runs_path}", file=sys.stderr)
    return 1 if n_fail > 0 else 0
