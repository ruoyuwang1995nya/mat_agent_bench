"""Aggregation utilities for MATTER v5 evaluation outputs."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..schemas import AxisPassRates, EvalRunRecord, EvaluationSummary, QuestionPassRate


def build_summary(records: list[EvalRunRecord], total_questions: int = 0) -> EvaluationSummary:
    """Aggregate a list of EvalRunRecords into an EvaluationSummary.

    Args:
        records: Evaluated run records.
        total_questions: Total questions in the benchmark bank. When > 0, the
            weighted_pass_rate is computed as sum(scores) / total_questions so
            that unanswered questions count as zero. When 0 (default), falls
            back to dividing by len(records) (average over answered only).
    """
    if not records:
        return EvaluationSummary(
            total_runs=0,
            total_criteria=0,
            total_passed=0,
            pass_rate=0.0,
            weighted_pass_rate=0.0,
        )

    total_criteria = 0
    total_passed = 0
    questions_passed = 0
    total_weighted_score = 0.0
    safety_triggered = 0

    cap_acc: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0, 0])
    cap_weighted: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    task_acc: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0, 0])
    task_weighted: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    dom_acc: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0, 0])
    dom_weighted: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    mode_acc: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0, 0])
    mode_weighted: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    model_acc: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0, 0])
    model_weighted: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    q_acc: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0, 0, 0])
    q_weighted: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    q_meta: dict[tuple[str, str], dict[str, Any]] = {}

    for record in records:
        cp = record.correctness_passed
        ct = record.correctness_total
        gp = record.grounding_passed
        gt = record.grounding_total
        ep = record.efficiency_passed
        et = record.efficiency_total

        total_criteria += record.total_count
        total_passed += record.passed_count
        if record.total_count > 0 and record.passed_count == record.total_count:
            questions_passed += 1
        total_weighted_score += record.overall_weighted_score

        if record.safety_veto.triggered:
            safety_triggered += 1

        for cap in record.capabilities:
            _add6(cap_acc[cap], cp, ct, gp, gt, ep, et)
            cap_weighted[cap][0] += record.correctness_weighted_score
            cap_weighted[cap][1] += record.grounding_weighted_score
            cap_weighted[cap][2] += record.efficiency_weighted_score

        if record.task_type:
            _add6(task_acc[record.task_type], cp, ct, gp, gt, ep, et)
            task_weighted[record.task_type][0] += record.correctness_weighted_score
            task_weighted[record.task_type][1] += record.grounding_weighted_score
            task_weighted[record.task_type][2] += record.efficiency_weighted_score

        _add6(dom_acc[record.domain], cp, ct, gp, gt, ep, et)
        dom_weighted[record.domain][0] += record.correctness_weighted_score
        dom_weighted[record.domain][1] += record.grounding_weighted_score
        dom_weighted[record.domain][2] += record.efficiency_weighted_score

        _add6(mode_acc[record.mode], cp, ct, gp, gt, ep, et)
        mode_weighted[record.mode][0] += record.correctness_weighted_score
        mode_weighted[record.mode][1] += record.grounding_weighted_score
        mode_weighted[record.mode][2] += record.efficiency_weighted_score

        model_key = record.model_name or 'unknown'
        _add6(model_acc[model_key], cp, ct, gp, gt, ep, et)
        model_weighted[model_key][0] += record.correctness_weighted_score
        model_weighted[model_key][1] += record.grounding_weighted_score
        model_weighted[model_key][2] += record.efficiency_weighted_score

        qk = (record.question_id, record.mode)
        _add7(q_acc[qk], cp, ct, gp, gt, ep, et, 1 if record.safety_veto.triggered else 0)
        q_weighted[qk][0] += record.correctness_weighted_score
        q_weighted[qk][1] += record.grounding_weighted_score
        q_weighted[qk][2] += record.efficiency_weighted_score

        if qk not in q_meta:
            q_meta[qk] = {
                'capabilities': list(record.capabilities),
                'task_type': record.task_type,
                'domain': record.domain,
            }

    pass_rate = total_passed / total_criteria if total_criteria > 0 else 0.0
    n_denom = total_questions if total_questions > 0 else len(records)
    weighted_pass_rate = total_weighted_score / n_denom if n_denom > 0 else 0.0

    by_capability = {
        k: _to_axis_pass_rates(v)
        for k, v in cap_acc.items()
    }
    by_task_type = {
        k: _to_axis_pass_rates(v)
        for k, v in task_acc.items()
    }
    by_domain = {
        k: _to_axis_pass_rates(v)
        for k, v in dom_acc.items()
    }
    by_mode = {
        k: _to_axis_pass_rates(v)
        for k, v in mode_acc.items()
    }
    by_model = {
        k: _to_axis_pass_rates(v)
        for k, v in model_acc.items()
    }

    by_question: dict[str, QuestionPassRate] = {}
    for (question_id, mode), slots in q_acc.items():
        cp, ct, gp, gt, ep, et, sv = slots
        meta = q_meta.get((question_id, mode), {})
        overall_p = cp + gp + ep
        overall_t = ct + gt + et
        q_record_count = sum(
            1 for r in records if r.question_id == question_id and r.mode == mode
        )
        key = f'{question_id}:{mode}'
        by_question[key] = QuestionPassRate(
            question_id=question_id,
            capabilities=meta.get('capabilities', []),
            task_type=meta.get('task_type', ''),
            domain=meta.get('domain', ''),
            runs=q_record_count,
            overall=(overall_p, overall_t),
            correctness=(cp, ct),
            grounding=(gp, gt),
            efficiency=(ep, et),
            safety_veto_count=sv,
        )

    safety = {
        'triggered_count': safety_triggered,
        'total_runs': len(records),
        'triggered_rate': safety_triggered / len(records) if records else 0.0,
        'any_triggered': safety_triggered > 0,
    }

    return EvaluationSummary(
        total_runs=len(records),
        total_criteria=total_criteria,
        total_passed=total_passed,
        questions_passed=questions_passed,
        pass_rate=pass_rate,
        weighted_pass_rate=weighted_pass_rate,
        by_capability=by_capability,
        by_task_type=by_task_type,
        by_domain=by_domain,
        by_question=by_question,
        by_mode=by_mode,
        by_model=by_model,
        safety=safety,
    )


def _add6(slots: list[int], cp: int, ct: int, gp: int, gt: int, ep: int, et: int) -> None:
    slots[0] += cp
    slots[1] += ct
    slots[2] += gp
    slots[3] += gt
    slots[4] += ep
    slots[5] += et


def _add7(slots: list[int], cp: int, ct: int, gp: int, gt: int, ep: int, et: int, sv: int) -> None:
    slots[0] += cp
    slots[1] += ct
    slots[2] += gp
    slots[3] += gt
    slots[4] += ep
    slots[5] += et
    slots[6] += sv


def _to_axis_pass_rates(raw_slots: list[int]) -> AxisPassRates:
    cp, ct, gp, gt, ep, et = raw_slots[:6]
    overall_p = cp + gp + ep
    overall_t = ct + gt + et
    return AxisPassRates(
        correctness=(cp, ct),
        grounding=(gp, gt),
        efficiency=(ep, et),
        overall=(overall_p, overall_t),
    )
