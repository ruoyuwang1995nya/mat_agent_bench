"""High-level grading API for MATTER v5.

Inspired by mle-bench's grade_csv / grade_jsonl pattern.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..reporting.aggregator import build_summary
from .evaluator import BinaryEvaluator
from .evidence import EvidenceBundle, EvidenceExtractor
from ..registry import Question, Registry
from ..reporting.reporter import load_records_from_jsonl, write_reports
from ..schemas import (
    EvalRunRecord,
    EvaluationSummary,
    LLMConfig,
    TokenUsageRecord,
)

_logger = logging.getLogger(__name__)


@dataclass
class QuestionReport:
    """Result of grading a single question (like mle-bench's CompetitionReport)."""

    question_id: str
    passed: bool
    score: float
    record: EvalRunRecord
    detail: str = ''


@dataclass
class RunReport:
    """Result of grading an entire run."""

    total_questions: int
    total_passed: int
    pass_rate: float
    weighted_pass_rate: float
    summary: EvaluationSummary
    question_reports: list[QuestionReport] = field(default_factory=list)
    report_paths: dict[str, str] = field(default_factory=dict)


def grade_question(
    *,
    question: Question,
    answer: str,
    tool_calls: list[dict[str, Any]] | None = None,
    evidence: EvidenceBundle | None = None,
    llm_cfg: LLMConfig | None = None,
    axis_weights: dict[str, float] | None = None,
    mode: str = 'direct',
    repeat_idx: int = 0,
    prompt: str = '',
    run_status: str = 'completed',
    model_name: str | None = None,
    token_usage: TokenUsageRecord | None = None,
    duration_ms: int = 0,
) -> QuestionReport:
    """Grade a single question and return a QuestionReport."""
    evaluator = BinaryEvaluator(
        llm_cfg=llm_cfg,
        axis_weights=axis_weights,
    )
    record = evaluator.evaluate(
        question=question.item,
        answer=answer,
        tool_calls=tool_calls,
        evidence=evidence,
        mode=mode,
        repeat_idx=repeat_idx,
        prompt=prompt,
        run_status=run_status,
        model_name=model_name,
        token_usage=token_usage,
        duration_ms=duration_ms,
    )

    passed = record.passed_count == record.total_count if record.total_count > 0 else False
    score = record.overall_weighted_score

    return QuestionReport(
        question_id=question.id,
        passed=passed,
        score=score,
        record=record,
        detail=f'{record.passed_count}/{record.total_count} criteria passed',
    )


def grade_run(
    *,
    raw_runs_path: Path,
    output_dir: Path | None = None,
    prefix: str = '',
) -> RunReport:
    """Load records from a JSONL file, aggregate, and write reports."""
    records = load_records_from_jsonl(raw_runs_path)
    if not records:
        raise ValueError(f'no valid records found in {raw_runs_path}')

    summary = build_summary(records)
    target_dir = output_dir or raw_runs_path.parent

    report_paths = write_reports(
        output_dir=target_dir,
        records=records,
        summary=summary,
        prefix=prefix,
    )

    question_reports = []
    for record in records:
        passed = record.passed_count == record.total_count if record.total_count > 0 else False
        question_reports.append(
            QuestionReport(
                question_id=record.question_id,
                passed=passed,
                score=record.overall_weighted_score,
                record=record,
                detail=f'{record.passed_count}/{record.total_count} criteria passed',
            )
        )

    total_passed = sum(1 for qr in question_reports if qr.passed)

    return RunReport(
        total_questions=len(records),
        total_passed=total_passed,
        pass_rate=total_passed / len(records) if records else 0.0,
        weighted_pass_rate=summary.weighted_pass_rate,
        summary=summary,
        question_reports=question_reports,
        report_paths=report_paths,
    )
