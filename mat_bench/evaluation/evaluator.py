"""Binary evaluator for MATTER v5 runs.

Every criterion is pass (1) or fail (0). The final record contains
passed_count / total_count and overall_weighted_score.

Scoring: overall_weighted_score = max(0, 1 - sum_failed_weights)
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .checks import (
    build_llm_context,
    check_answer_json_numeric_from_evidence,
    check_atomworld_active_task_from_evidence,
    check_batch_consistent_calls,
    check_batch_single_variable_sweep,
    check_batch_tool_args_constant,
    check_checkcif_alerts,
    check_duration_budget,
    check_molcrys_local_env_from_evidence,
    check_molcrys_slab_integrity,
    check_sc005_disorder_formulas,
    check_struct_file_atom_count,
    check_struct_file_bond_angle,
    check_struct_file_bond_count,
    check_struct_file_bond_length,
    check_struct_file_cell_param,
    check_struct_file_coordination,
    check_struct_file_count,
    check_struct_file_formula,
    check_struct_file_layer_count,
    check_struct_file_min_interatomic_distance,
    check_struct_file_space_group,
    check_struct_file_stoichiometry_ratio,
    check_struct_file_surface_termination,
    check_text_file_contains_all_from_evidence,
    check_text_file_kpt_path_from_evidence,
    check_text_file_numeric_range_from_evidence,
    check_text_file_regex_from_evidence,
    check_token_budget,
    check_turn_budget,
)
from .evidence import EvidenceBundle
from .llm import SyncLLM
from .prompts import (
    BINARY_JUDGE_SYSTEM_PROMPT,
    GROUNDING_JUDGE_SYSTEM_PROMPT,
)
from ..schemas import (
    CriterionResult,
    EvalRunRecord,
    LLMConfig,
    QuestionItem,
    ReferenceAnswer,
    SafetyVetoRecord,
    ScoringCheckItem,
    TokenUsageRecord,
)

if TYPE_CHECKING:
    from ..registry import Question

_eval_logger = logging.getLogger(__name__)


class BinaryEvaluator:
    """Binary checklist evaluator for MATTER v5."""

    def __init__(
        self,
        llm_cfg: LLMConfig | None = None,
        *,
        parallel_checklist_workers: int = 1,
    ) -> None:
        self._llm: SyncLLM | None = None
        self._parallel_checklist_workers = max(1, int(parallel_checklist_workers))
        if llm_cfg is not None:
            self._llm = SyncLLM(
                model=llm_cfg.model,
                api_key=llm_cfg.api_key,
                base_url=llm_cfg.base_url,
                temperature=llm_cfg.temperature,
                max_tokens=llm_cfg.max_tokens,
                timeout=llm_cfg.timeout,
            )

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def evaluate(
        self,
        *,
        question: QuestionItem | Question,
        answer: str,
        tool_calls: list[dict[str, Any]] | None = None,
        evidence: EvidenceBundle | None = None,
        mode: str = 'direct',
        repeat_idx: int = 0,
        prompt: str = '',
        run_status: str = 'completed',
        model_name: str | None = None,
        token_usage: TokenUsageRecord | None = None,
        duration_ms: int = 0,
    ) -> EvalRunRecord:
        question_item = question.item if hasattr(question, 'item') else question
        question_dir = question.question_dir if hasattr(question, 'question_dir') else None
        if tool_calls is None:
            tool_calls = []
        if token_usage is None:
            token_usage = TokenUsageRecord()

        checklist = question_item.scoring_checklist
        if run_status == 'timeout':
            criteria_results = {
                item.id: CriterionResult(
                    criterion_id=item.id,
                    capability=item.capability,
                    passed=False,
                    reason='run timed out before evaluation completed',
                    verify_method=item.verify,
                )
                for item in checklist
            }
            return EvalRunRecord(
                question_id=question_item.id,
                capabilities=list(question_item.capabilities),
                task_type=question_item.task_type,
                domain=question_item.domain,
                mode=mode,  # type: ignore[arg-type]
                repeat_idx=repeat_idx,
                prompt=prompt,
                answer=answer,
                run_status=run_status,
                criteria_results=criteria_results,
                passed_count=0,
                total_count=len(checklist),
                overall_weighted_score=0.0,
                model_name=model_name,
                token_usage=token_usage,
                tool_calls=tool_calls,
                safety_veto=SafetyVetoRecord(),
                created_at=datetime.now(timezone.utc),
                duration_ms=int(duration_ms),
            )

        # Regular questions: evaluate each checklist item
        ref_map = {item.key: item for item in question_item.reference_answers}
        criteria_results = {}

        total_passed = 0
        total_count = 0
        failed_weight = 0.0

        use_parallel = self._parallel_checklist_workers > 1 and len(checklist) > 1

        if not use_parallel:
            _eval_logger.debug(
                "checklist sequential  q=%s  items=%d",
                question.id, len(checklist),
            )
            item_outcomes: list[tuple[ScoringCheckItem, bool, str]] = []
            for item in checklist:
                passed_item, reason = self._check_item(
                    item=item,
                    reference_map=ref_map,
                    answer=answer,
                    question=question_item,
                    question_dir=question_dir,
                    tool_calls=tool_calls,
                    evidence=evidence,
                )
                item_outcomes.append((item, passed_item, reason))
        else:
            workers = min(self._parallel_checklist_workers, len(checklist))
            _eval_logger.info(
                "checklist parallel    q=%s  items=%d  workers=%d",
                question.id, len(checklist), workers,
            )

            def _run_checklist_item(
                item: ScoringCheckItem,
            ) -> tuple[str, bool, str]:
                passed_item, reason = self._check_item(
                    item=item,
                    reference_map=ref_map,
                    answer=answer,
                    question=question_item,
                    question_dir=question_dir,
                    tool_calls=tool_calls,
                    evidence=evidence,
                )
                return item.id, passed_item, reason

            by_id: dict[str, tuple[bool, str]] = {}
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(_run_checklist_item, it) for it in checklist]
                for fut in as_completed(futures):
                    item_id, passed_item, reason = fut.result()
                    by_id[item_id] = (passed_item, reason)

            item_outcomes = []
            for item in checklist:
                passed_item, reason = by_id[item.id]
                item_outcomes.append((item, passed_item, reason))

        for item, passed_item, reason in item_outcomes:
            criteria_results[item.id] = CriterionResult(
                criterion_id=item.id,
                capability=item.capability,
                passed=passed_item,
                reason=reason,
                verify_method=item.verify,
            )
            item_weight = item.weight if hasattr(item, 'weight') else 0.5
            total_count += 1
            if passed_item:
                total_passed += 1
            else:
                failed_weight += item_weight

        overall_weighted = self._calc_overall_weighted_score(failed_weight)

        return EvalRunRecord(
            question_id=question_item.id,
            capabilities=list(question_item.capabilities),
            task_type=question_item.task_type,
            domain=question_item.domain,
            mode=mode,  # type: ignore[arg-type]
            repeat_idx=repeat_idx,
            prompt=prompt,
            answer=answer,
            run_status=run_status,
            criteria_results=criteria_results,
            passed_count=total_passed,
            total_count=total_count,
            overall_weighted_score=overall_weighted,
            model_name=model_name,
            token_usage=token_usage,
            tool_calls=tool_calls,
            safety_veto=SafetyVetoRecord(),
            created_at=datetime.now(timezone.utc),
            duration_ms=int(duration_ms),
        )

    # ------------------------------------------------------------------
    # Weighted score calculation
    # ------------------------------------------------------------------

    def _calc_overall_weighted_score(
        self,
        failed_weight: float,
    ) -> float:
        # Penalty-deduction: score = max(0, 1 - sum_failed_weights)
        # Range: [0, 1] — 1.0 means all passed, 0.0 means penalties >= 1.0
        return max(0.0, 1.0 - failed_weight)

    # ------------------------------------------------------------------
    # Per-item dispatch
    # ------------------------------------------------------------------

    def _check_item(
        self,
        *,
        item: ScoringCheckItem,
        reference_map: dict[str, ReferenceAnswer],
        answer: str,
        question: QuestionItem,
        question_dir: Path | None,
        tool_calls: list[dict[str, Any]],
        evidence: EvidenceBundle | None,
    ) -> tuple[bool, str]:
        ref = reference_map.get(item.id)

        if item.verify == 'exact_match':
            if ref is None:
                return False, 'missing reference answer'
            return self._check_exact_match(
                answer=answer, expected=ref.value, tolerance=ref.tolerance
            )
        if item.verify == 'numerical_range':
            if ref is None:
                return False, 'missing reference answer'
            return self._check_numerical_range(
                answer=answer, expected=ref.value, tolerance=ref.tolerance
            )
        if item.verify == 'contains_all':
            if ref is None:
                return False, 'missing reference answer'
            return self._check_contains_all(answer=answer, expected=ref.value)
        if item.verify == 'tool_called':
            if ref is None:
                return False, 'missing reference answer'
            return self._check_tool_called(tool_calls=tool_calls, expected=ref.value)
        if item.verify == 'tool_args_match':
            if ref is None:
                return False, 'missing reference answer'
            return self._check_tool_args_match(tool_calls=tool_calls, ref=ref)
        if item.verify == 'tool_observation_field':
            if ref is None:
                return False, 'missing reference answer'
            return self._check_tool_observation_field(evidence=evidence, ref=ref)

        if item.verify == 'event_type_called':
            if ref is None:
                return False, 'missing reference answer'
            return self._check_event_type_called(evidence=evidence, expected=ref.value)
        if item.verify == 'source_type_used':
            if ref is None:
                return False, 'missing reference answer'
            return self._check_source_type_used(evidence=evidence, expected=ref.value)
        if item.verify == 'call_count_range':
            if ref is None:
                return False, 'missing reference answer'
            return self._check_call_count_range(evidence=evidence, expected=ref.value)
        if item.verify == 'no_retries':
            return self._check_no_retries(evidence=evidence)
        if item.verify == 'artifact_exists':
            if ref is None:
                return False, 'missing reference answer'
            return self._check_artifact_exists(evidence=evidence, ref=ref)
        if item.verify == 'token_budget':
            if ref is None:
                return False, 'missing reference answer'
            return check_token_budget(evidence=evidence, expected=ref.value)
        if item.verify == 'turn_budget':
            if ref is None:
                return False, 'missing reference answer'
            return check_turn_budget(evidence=evidence, expected=ref.value)
        if item.verify == 'duration_budget':
            if ref is None:
                return False, 'missing reference answer'
            return check_duration_budget(evidence=evidence, expected=ref.value)
        if item.verify == 'molcrys_slab_molecular_integrity':
            if ref is None:
                return False, 'missing reference answer'
            return check_molcrys_slab_integrity(evidence=evidence, ref=ref)
        if item.verify == 'sc005_disorder_formulas':
            if ref is None:
                return False, 'missing reference answer'
            return check_sc005_disorder_formulas(answer=answer)
        if item.verify == 'molcrys_local_env':
            if ref is None:
                return False, 'missing reference answer'
            return check_molcrys_local_env_from_evidence(evidence=evidence, ref=ref)
        if item.verify == 'checkcif_no_a_alerts':
            if ref is None:
                return False, 'missing reference answer'
            return check_checkcif_alerts(evidence=evidence, ref=ref)
        if item.verify == 'atomworld_active_task':
            if ref is None:
                return False, 'missing reference answer'
            return check_atomworld_active_task_from_evidence(
                evidence=evidence,
                ref=ref,
                question_dir=question_dir,
            )

        # --- struct_file_* programmatic structure checks ---
        _STRUCT_FILE_DISPATCH = {
            'struct_file_atom_count': check_struct_file_atom_count,
            'struct_file_formula': check_struct_file_formula,
            'struct_file_bond_count': check_struct_file_bond_count,
            'struct_file_bond_length': check_struct_file_bond_length,
            'struct_file_bond_angle': check_struct_file_bond_angle,
            'struct_file_cell_param': check_struct_file_cell_param,
            'struct_file_stoichiometry_ratio': check_struct_file_stoichiometry_ratio,
            'struct_file_coordination': check_struct_file_coordination,
            'struct_file_layer_count': check_struct_file_layer_count,
            'struct_file_min_interatomic_distance': (
                check_struct_file_min_interatomic_distance
            ),
            'struct_file_space_group': check_struct_file_space_group,
            'struct_file_count': check_struct_file_count,
            'struct_file_surface_termination': check_struct_file_surface_termination,
        }
        if item.verify in _STRUCT_FILE_DISPATCH:
            if ref is None:
                return False, 'missing reference answer'
            return _STRUCT_FILE_DISPATCH[item.verify](evidence=evidence, ref=ref)

        _TEXT_FILE_DISPATCH = {
            'text_file_contains_all': check_text_file_contains_all_from_evidence,
            'text_file_kpt_path': check_text_file_kpt_path_from_evidence,
            'text_file_numeric_range': check_text_file_numeric_range_from_evidence,
            'text_file_regex': check_text_file_regex_from_evidence,
            'answer_json_numeric': check_answer_json_numeric_from_evidence,
        }
        if item.verify in _TEXT_FILE_DISPATCH:
            if ref is None:
                return False, 'missing reference answer'
            return _TEXT_FILE_DISPATCH[item.verify](evidence=evidence, ref=ref)

        if item.verify == 'llm_binary_judge':
            if item.capability == 'scientific_grounding':
                return self.judge_binary(
                    criterion=item.criterion,
                    context=build_llm_context(
                        question=question,
                        answer=answer,
                        evidence=evidence,
                        include_tool_calls=False,
                    ),
                    system_prompt=GROUNDING_JUDGE_SYSTEM_PROMPT,
                )
            return self.judge_binary(
                criterion=item.criterion,
                context=build_llm_context(
                    question=question, answer=answer, evidence=evidence
                ),
            )

        if item.verify == 'batch_single_variable_sweep':
            if ref is None:
                return False, 'missing reference answer'
            return check_batch_single_variable_sweep(
                tool_calls=tool_calls, evidence=evidence, ref=ref
            )
        if item.verify == 'batch_tool_args_constant':
            if ref is None:
                return False, 'missing reference answer'
            return check_batch_tool_args_constant(
                tool_calls=tool_calls, evidence=evidence, ref=ref
            )
        if item.verify == 'batch_consistent_calls':
            if ref is None:
                return False, 'missing reference answer'
            return check_batch_consistent_calls(
                tool_calls=tool_calls, evidence=evidence, ref=ref
            )

        return False, f'unsupported verify type: {item.verify}'

    # ------------------------------------------------------------------
    # v5 LLM binary judge
    # ------------------------------------------------------------------

    def judge_binary(
        self,
        *,
        criterion: str,
        context: str,
        system_prompt: str | None = None,
    ) -> tuple[bool, str]:
        if self._llm is None:
            return False, 'no evaluator LLM configured'
        sys_content = system_prompt or BINARY_JUDGE_SYSTEM_PROMPT
        user_msg = (
            f'Criterion:\n{criterion}\n\n'
            f'Context:\n{context}\n\n'
            'Return JSON only.'
        )
        max_judge_attempts = 3
        last_parse_error = ''
        for _attempt in range(max_judge_attempts):
            reply_text = self._llm.chat(
                system=sys_content,
                user=user_msg,
            )
            try:
                data = self._parse_json(reply_text)
            except ValueError:
                last_parse_error = (
                    f'LLM response contained no JSON object'
                    f' (attempt {_attempt + 1}/{max_judge_attempts})'
                )
                _eval_logger.warning(
                    'judge_binary: JSON parse failed on attempt %d/%d for criterion %.80s',
                    _attempt + 1,
                    max_judge_attempts,
                    criterion,
                )
                continue
            raw_verdict = data.get(
                'verdict',
                data.get('criterion_met', data.get('pass', False)),
            )
            if isinstance(raw_verdict, str):
                passed = raw_verdict.strip().lower() in ('pass', 'true', '1', 'yes')
            else:
                passed = bool(raw_verdict)
            reason = str(data.get('reason', '')).strip() or 'llm_binary_judge'
            return passed, reason
        return False, last_parse_error

    # ------------------------------------------------------------------
    # Deterministic check methods
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_text(text: str) -> str:
        return ' '.join(str(text).strip().lower().split())

    def _check_exact_match(
        self, *, answer: str, expected: Any, tolerance: float | None
    ) -> tuple[bool, str]:
        if isinstance(expected, (int, float)):
            numbers = self._extract_numbers(answer)
            if not numbers:
                return False, 'no numeric value found'
            target = float(expected)
            tol = 1e-8 if tolerance is None else float(tolerance)
            best = min(numbers, key=lambda v: abs(v - target))
            hit = abs(best - target) <= tol
            return hit, f'target={target}, found={best}, tol={tol}'
        expected_norm = self._normalize_text(str(expected))
        answer_norm = self._normalize_text(answer)
        hit = expected_norm in answer_norm
        return hit, f"expected='{expected_norm}' present={hit}"

    def _check_numerical_range(
        self, *, answer: str, expected: Any, tolerance: float | None
    ) -> tuple[bool, str]:
        if not isinstance(expected, (int, float)):
            return False, 'expected reference is not numeric'
        if tolerance is None:
            return False, 'numerical_range requires tolerance'
        numbers = self._extract_numbers(answer)
        if not numbers:
            return False, 'no numeric value found'
        target = float(expected)
        tol = float(tolerance)
        best = min(numbers, key=lambda v: abs(v - target))
        hit = abs(best - target) <= tol
        return hit, f'target={target}, found={best}, tol={tol}'

    def _check_contains_all(self, *, answer: str, expected: Any) -> tuple[bool, str]:
        if isinstance(expected, list):
            tokens = [self._normalize_text(str(item)) for item in expected]
        else:
            tokens = [self._normalize_text(str(expected))]
        haystack = self._normalize_text(answer)
        missing = [t for t in tokens if t and t not in haystack]
        if missing:
            return False, f'missing tokens: {missing}'
        return True, 'all tokens found'

    @staticmethod
    def _check_tool_called(
        *,
        tool_calls: list[dict[str, Any]],
        expected: Any,
    ) -> tuple[bool, str]:
        targets = (
            [str(t) for t in expected]
            if isinstance(expected, list)
            else [str(expected)]
        )
        for call in tool_calls:
            name = call.get('tool_name', '')
            if name in targets:
                return True, f"tool '{name}' called at step {call.get('step')}"
        called_names = sorted({c.get('tool_name', '') for c in tool_calls})
        return False, f'none of {targets} called (called: {called_names})'

    @staticmethod
    def _check_tool_args_match(
        *,
        tool_calls: list[dict[str, Any]],
        ref: ReferenceAnswer,
    ) -> tuple[bool, str]:
        if not ref.tool_name or not ref.tool_arg:
            return False, 'tool_args_match requires tool_name and tool_arg in reference'
        if isinstance(ref.tool_name, str) and '|' in ref.tool_name:
            names = [n.strip() for n in ref.tool_name.split('|')]
        else:
            names = [ref.tool_name]
        matching_calls = [c for c in tool_calls if c.get('tool_name') in names]
        if not matching_calls:
            return False, f'none of {names} was ever called'
        for call in matching_calls:
            args = call.get('tool_args', {})
            if ref.tool_arg not in args:
                continue
            actual = args[ref.tool_arg]
            if ref.tolerance is not None and isinstance(ref.value, (int, float)):
                try:
                    if abs(float(actual) - float(ref.value)) <= ref.tolerance:
                        return (
                            True,
                            f'{ref.tool_arg}={actual} (expected {ref.value}±{ref.tolerance})',
                        )
                except (TypeError, ValueError):
                    continue
            else:
                if actual == ref.value:
                    return True, f'{ref.tool_arg}={actual}'
        actuals = [
            c.get('tool_args', {}).get(ref.tool_arg, '<missing>')
            for c in matching_calls
        ]
        return (
            False,
            f'no call to {names} had {ref.tool_arg}={ref.value} (found: {actuals})',
        )

    def _check_tool_observation_field(
        self, *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
    ) -> tuple[bool, str]:
        if evidence is None:
            return False, 'no EvidenceBundle provided'
        if not ref.tool_arg:
            return (
                False,
                'tool_observation_field requires tool_arg in reference',
            )

        if ref.tool_name:
            matches = [
                tc for tc in evidence.tool_calls if tc.tool_name == ref.tool_name
            ]
            if not matches:
                return False, f'tool {ref.tool_name!r} was never called'
        else:
            matches = list(evidence.tool_calls)
            if not matches:
                return False, 'no tool calls recorded'

        for tc in matches:
            raw = tc.observation_excerpt.strip()
            if not raw:
                continue
            try:
                parsed = self._parse_json(raw)
            except Exception:
                continue
            if ref.tool_arg not in parsed:
                continue

            actual = parsed.get(ref.tool_arg)
            expected = ref.value
            if isinstance(expected, (int, float)) and ref.tolerance is not None:
                try:
                    hit = abs(float(actual) - float(expected)) <= float(ref.tolerance)
                    return (
                        hit,
                        f'observation field {ref.tool_arg}={actual!r}, expected={expected!r}±{ref.tolerance}',
                    )
                except (TypeError, ValueError):
                    return (
                        False,
                        f'observation field {ref.tool_arg}={actual!r} is not numeric-comparable to {expected!r}',
                    )
            hit = actual == expected
            return (
                hit,
                f'observation field {ref.tool_arg}={actual!r}, expected={expected!r}',
            )

        tool_label = repr(ref.tool_name) if ref.tool_name else '<any>'
        return (
            False,
            f'field {ref.tool_arg!r} not found in observation excerpt for tool {tool_label}',
        )

    @staticmethod
    def _check_event_type_called(
        *, evidence: EvidenceBundle | None, expected: Any
    ) -> tuple[bool, str]:
        if evidence is None:
            return False, 'no EvidenceBundle provided'
        targets = (
            [str(t) for t in expected]
            if isinstance(expected, list)
            else [str(expected)]
        )
        for evt in evidence.events:
            if evt.event_type.value in targets and evt.succeeded:
                return (
                    True,
                    f"event_type '{evt.event_type.value}' found at step {evt.step}",
                )
        found = sorted({e.event_type.value for e in evidence.events})
        return False, f'none of {targets} found (found: {found})'

    @staticmethod
    def _check_source_type_used(
        *, evidence: EvidenceBundle | None, expected: Any
    ) -> tuple[bool, str]:
        if evidence is None:
            return False, 'no EvidenceBundle provided'
        targets = (
            [str(t) for t in expected]
            if isinstance(expected, list)
            else [str(expected)]
        )
        for evt in evidence.events:
            if evt.source_type.value in targets and evt.succeeded:
                return (
                    True,
                    f"source_type '{evt.source_type.value}' found at step {evt.step}",
                )
        found = sorted({e.source_type.value for e in evidence.events})
        return False, f'none of {targets} found (found: {found})'

    @staticmethod
    def _check_call_count_range(
        *, evidence: EvidenceBundle | None, expected: Any
    ) -> tuple[bool, str]:
        if evidence is None:
            return False, 'no EvidenceBundle provided'
        count = len(evidence.tool_calls)
        if isinstance(expected, (list, tuple)) and len(expected) >= 2:
            lo, hi = int(expected[0]), int(expected[1])
        elif isinstance(expected, dict):
            lo = int(expected.get('min', 0))
            hi = int(expected.get('max', 9999))
        else:
            return False, f'unexpected call_count_range format: {expected!r}'
        hit = lo <= count <= hi
        return hit, f'tool_calls={count}, expected=[{lo},{hi}]'

    @staticmethod
    def _check_no_retries(*, evidence: EvidenceBundle | None) -> tuple[bool, str]:
        if evidence is None:
            return True, 'no EvidenceBundle provided (skipped)'
        calls = evidence.tool_calls
        for i in range(1, len(calls)):
            if (
                calls[i].tool_name == calls[i - 1].tool_name
                and calls[i].args == calls[i - 1].args
            ):
                return (
                    False,
                    f"identical consecutive call to '{calls[i].tool_name}' at step {calls[i].step}",
                )
        return True, f'no retries detected ({len(calls)} calls)'

    @staticmethod
    def _check_artifact_exists(
        *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
    ) -> tuple[bool, str]:
        if evidence is None:
            return False, 'no EvidenceBundle provided'
        resolve = ref.workspace_resolve or 'recursive'
        if resolve not in ('recursive', 'root'):
            return False, f'invalid workspace_resolve: {ref.workspace_resolve!r}'
        if isinstance(ref.value, dict):
            return False, 'artifact_exists expects a string value, not a dict'
        needle = str(ref.value).strip()

        if resolve == 'root':
            if not evidence.workspace_dir:
                return False, 'missing workspace_dir on evidence'
            if len(Path(needle).parts) != 1:
                return (
                    False,
                    'workspace_resolve=root requires a bare filename (no path separators)',
                )
            root = Path(evidence.workspace_dir)
            p = root / needle
            if p.is_file():
                return True, f'file at workspace root: {p}'
            return (
                False,
                f"expected file at workspace root: {needle!r} (missing at {p})",
            )

        for art in evidence.artifacts:
            if needle in art.path or needle == art.artifact_type:
                return True, f'artifact found: {art.path}'
        paths = [a.path for a in evidence.artifacts]
        return False, f"artifact '{needle}' not found (artifacts: {paths})"

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_numbers(text: str) -> list[float]:
        text = text.replace('\u2212', '-')
        pattern = r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?'
        numbers: list[float] = []
        for raw in re.findall(pattern, text):
            try:
                numbers.append(float(raw))
            except Exception:  # noqa: BLE001
                continue
        return numbers

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        def _try_loads(s: str) -> dict[str, Any]:
            try:
                return json.loads(s)
            except json.JSONDecodeError:
                sanitized = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', s)
                return json.loads(sanitized)

        stripped = text.strip()
        if stripped.startswith('{') and stripped.endswith('}'):
            return _try_loads(stripped)
        start = stripped.find('{')
        end = stripped.rfind('}')
        if start >= 0 and end > start:
            return _try_loads(stripped[start : end + 1])
        raise ValueError('No JSON object found')


RubricEvaluator = BinaryEvaluator
