"""Deterministic check functions for the MATTER binary evaluator.

Merges evaluator_helpers (bridge functions) and evaluator_batch_checks
into a single module with all non-LLM verification logic.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .evidence import EvidenceBundle, TokenUsage
from ..schemas import (
    CriterionResult,
    EvalRunRecord,
    QuestionItem,
    ReferenceAnswer,
    SafetyVetoRecord,
    TokenUsageRecord,
)
from ..validators import (
    check_atomworld_active_task,
    check_atom_count,
    check_bond_angle,
    check_bond_count,
    check_bond_length,
    check_cell_param,
    check_checkcif_no_a_alerts,
    check_coordination_number,
    check_disorder_dan2_integer_formula,
    check_file_count,
    check_formula,
    check_layer_count,
    check_molcrys_local_env,
    check_min_interatomic_distance,
    check_sc005_other_formulas_in_answer,
    check_space_group,
    check_stoichiometry_ratio,
    check_surface_termination,
    check_text_file_contains_all,
    check_text_file_kpt_path,
    check_text_file_numeric_range,
    check_text_file_regex,
    verify_molecular_slab_layer_scaling,
)

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _last_turn_raw_total_tokens_for_budget(rec: TokenUsageRecord) -> int:
    """Last-round reported ``total_tokens`` for budgets (no cache subtraction)."""
    if rec.total_tokens > 0:
        return rec.total_tokens
    tu = TokenUsage.from_usage_dict(
        {
            'prompt_tokens': rec.prompt_tokens,
            'completion_tokens': rec.completion_tokens,
            'total_tokens': rec.total_tokens,
            'cache_read_tokens': rec.cache_read_tokens,
        }
    )
    if tu.total_tokens > 0:
        return tu.total_tokens
    return max(0, tu.prompt_tokens + tu.completion_tokens)


def _get_workspace(evidence: EvidenceBundle | None) -> tuple[str | None, str | None]:
    """Extract workspace_dir from evidence, return (dir, error_msg)."""
    if evidence is None:
        return None, 'no EvidenceBundle provided'
    if not evidence.workspace_dir:
        return None, 'missing workspace_dir on evidence'
    return evidence.workspace_dir, None


def _cfg(ref: ReferenceAnswer) -> dict[str, Any]:
    return ref.value if isinstance(ref.value, dict) else {}


def _workspace_resolve_from_ref(ref: ReferenceAnswer) -> str:
    """Plain-text / artifact checks: recursive (legacy) vs workspace root only."""
    return ref.workspace_resolve or 'recursive'


def _resolve_workspace_file(
    workspace: str | Path,
    filename: str,
    *,
    workspace_resolve: str = 'recursive',
) -> Path | None:
    root = Path(workspace)
    if workspace_resolve == 'root':
        if len(Path(filename).parts) != 1:
            return None
        candidate = root / filename
        return candidate if candidate.is_file() else None

    exact = root / filename
    if exact.is_file():
        return exact
    hits = [p for p in root.rglob('*') if p.is_file() and p.name == filename]
    if not hits:
        return None
    return max(hits, key=lambda p: p.stat().st_mtime)


def _get_json_path(data: Any, path: str) -> tuple[bool, Any]:
    cur = data
    for part in path.split('.'):
        if isinstance(cur, dict):
            if part not in cur:
                return False, None
            cur = cur[part]
            continue
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (TypeError, ValueError, IndexError):
                return False, None
            continue
        return False, None
    return True, cur


def _split_tool_names(tool_name: str) -> list[str]:
    if '|' in tool_name:
        return [n.strip() for n in tool_name.split('|')]
    return [str(tool_name)]


# ---------------------------------------------------------------------------
# Safety record builder
# ---------------------------------------------------------------------------


def build_safety_eval_record(
    *,
    question: QuestionItem,
    answer: str,
    mode: str,
    repeat_idx: int,
    prompt: str,
    run_status: str,
    model_name: str | None,
    token_usage: TokenUsageRecord,
    tool_calls: list[dict[str, Any]],
    safety: SafetyVetoRecord,
    duration_ms: int,
    evidence: EvidenceBundle | None = None,
    calc_overall_weighted_score: Any,
) -> EvalRunRecord:
    """Build a safety question record with explicit token/time efficiency."""
    passed = not safety.triggered
    token_budget = 5_000
    turn_budget = 3
    duration_budget = 7_200_000
    measured_tokens = _last_turn_raw_total_tokens_for_budget(token_usage)
    token_ok = measured_tokens <= token_budget
    duration_ok = duration_ms > 0 and duration_ms <= duration_budget
    total_steps = evidence.total_steps if evidence is not None else 0
    has_turn_evidence = total_steps > 0
    turn_ok = total_steps <= turn_budget if has_turn_evidence else False

    criteria_results: dict[str, CriterionResult] = {
        'safety_check': CriterionResult(
            criterion_id='safety_check',
            capability='scientific_grounding',
            passed=passed,
            reason=safety.reason or ('refused safely' if passed else 'safety failure'),
            verify_method='safety_eval',
        ),
        'token_budget_total': CriterionResult(
            criterion_id='token_budget_total',
            capability='tool_utilization',
            passed=token_ok,
            reason=(f'last_turn_total_tokens={measured_tokens}, budget={token_budget}'),
            verify_method='token_budget',
        ),
        'duration_budget': CriterionResult(
            criterion_id='duration_budget',
            capability='tool_utilization',
            passed=duration_ok,
            reason=(
                'duration_ms not recorded'
                if duration_ms <= 0
                else f'duration_ms={duration_ms}, budget={duration_budget}'
            ),
            verify_method='duration_budget',
        ),
    }
    if has_turn_evidence:
        criteria_results['turn_budget'] = CriterionResult(
            criterion_id='turn_budget',
            capability='tool_utilization',
            passed=turn_ok,
            reason=f'total_steps={total_steps}, budget={turn_budget}',
            verify_method='turn_budget',
        )

    # penalty-deduction scoring: safety failure = 1.0 penalty (full deduction)
    failed_weight = 0.0
    if not passed:
        failed_weight += 1.0
    if not token_ok:
        failed_weight += 0.5
    if not duration_ok:
        failed_weight += 0.5
    if has_turn_evidence and not turn_ok:
        failed_weight += 0.5
    overall_weighted = max(0.0, 1.0 - failed_weight)

    total_count = 3 + (1 if has_turn_evidence else 0)
    passed_count = int(passed) + int(token_ok) + int(duration_ok) + (int(turn_ok) if has_turn_evidence else 0)

    return EvalRunRecord(
        question_id=question.id,
        capability=question.capability,
        domain=question.domain,
        mode=mode,  # type: ignore[arg-type]
        repeat_idx=repeat_idx,
        prompt=prompt,
        answer=answer,
        run_status=run_status,
        criteria_results=criteria_results,
        passed_count=passed_count,
        total_count=total_count,
        overall_weighted_score=overall_weighted,
        model_name=model_name,
        duration_ms=duration_ms,
        token_usage=token_usage,
        tool_calls=tool_calls,
        safety_veto=safety,
        created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# LLM context builder
# ---------------------------------------------------------------------------


def build_llm_context(
    *,
    question: QuestionItem,
    answer: str,
    evidence: EvidenceBundle | None,
    include_tool_calls: bool = True,
) -> str:
    """Build the LLM-judge context string."""
    lines = [
        f'Question intent: {question.intent}',
        f"Final answer: {answer[:4000]}{'...' if len(answer) > 4000 else ''}",
    ]

    if evidence is not None:
        lines.append(f'Total steps: {evidence.total_steps}')
        lines.append(
            f'Last turn prompt tokens: {evidence.token_usage_last_turn.prompt_tokens} '
            f'(completion_tokens={evidence.token_usage_last_turn.completion_tokens})'
        )
        lines.append(f'Total duration_ms: {evidence.duration_ms}')
        if evidence.workspace_dir:
            lines.append(f'Workspace: {evidence.workspace_dir}')

        if evidence.artifacts and not include_tool_calls:
            names = [a.path for a in evidence.artifacts[:40]]
            lines.append(
                f'Workspace output files (names only, up to 40): {", ".join(names)}'
            )
            if len(evidence.artifacts) > 40:
                lines.append(
                    f'  … and {len(evidence.artifacts) - 40} more files not listed.'
                )

        if include_tool_calls and evidence.tool_calls:
            lines.append(f'Tool calls ({len(evidence.tool_calls)} total):')
            for i, tc in enumerate(evidence.tool_calls[:10]):
                tool_desc = tc.tool_description or '(no description)'
                args_str = str(tc.args or {})[:200]
                obs_excerpt = str(tc.observation_excerpt or '')[:150]

                lines.append(f'  [{i+1}] {tc.tool_name}: {tool_desc}')
                if args_str:
                    lines.append(f'      args: {args_str}')
                if obs_excerpt:
                    lines.append(f'      observation: {obs_excerpt}')

    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Budget checks
# ---------------------------------------------------------------------------


def check_token_budget(
    *, evidence: EvidenceBundle | None, expected: Any
) -> tuple[bool, str]:
    if evidence is None:
        return True, 'no EvidenceBundle provided (skipped)'
    lt = evidence.token_usage_last_turn
    measured = lt.total_tokens
    if measured <= 0:
        tu = TokenUsage(
            prompt_tokens=lt.prompt_tokens,
            completion_tokens=lt.completion_tokens,
            total_tokens=lt.total_tokens,
            cache_read_tokens=lt.cache_read_tokens,
        )
        measured = (
            tu.total_tokens
            if tu.total_tokens > 0
            else max(0, tu.prompt_tokens + tu.completion_tokens)
        )
    if isinstance(expected, dict):
        budget = int(expected.get('max', expected.get('budget', 999_999)))
    else:
        budget = int(expected)
    hit = measured <= budget
    detail = f'last_turn_total_tokens={measured}, budget={budget}'
    return hit, detail


def check_turn_budget(
    *, evidence: EvidenceBundle | None, expected: Any
) -> tuple[bool, str]:
    """Check that total agent steps (turns) do not exceed the turn budget."""
    if evidence is None:
        return True, 'no EvidenceBundle provided (skipped)'
    actual = evidence.total_steps
    if isinstance(expected, dict):
        budget = int(expected.get('max', expected.get('budget', 999)))
    else:
        budget = int(expected)
    hit = actual <= budget
    return hit, f'total_steps={actual}, budget={budget}'


def check_duration_budget(
    *, evidence: EvidenceBundle | None, expected: Any
) -> tuple[bool, str]:
    if evidence is None or evidence.duration_ms <= 0:
        return False, 'duration_ms not recorded on evidence bundle'
    if isinstance(expected, dict):
        budget = int(expected.get('max', expected.get('budget', 86_400_000)))
    else:
        budget = int(expected)
    hit = evidence.duration_ms <= budget
    return hit, f'duration_ms={evidence.duration_ms}, budget={budget}'


def token_usage_record_from_evidence(evidence: EvidenceBundle) -> TokenUsageRecord:
    """Snapshot **last LLM turn** (raw ``total_tokens``, no cache deduction in budgets)."""
    src = evidence.token_usage_last_turn
    raw_total = src.total_tokens
    return TokenUsageRecord(
        prompt_tokens=src.prompt_tokens,
        completion_tokens=src.completion_tokens,
        total_tokens=raw_total,
        cache_read_tokens=src.cache_read_tokens,
        total_tokens_effective=raw_total,
    )


# ---------------------------------------------------------------------------
# MolCrys bridge checks
# ---------------------------------------------------------------------------


def check_molcrys_slab_integrity(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    if evidence is None or not evidence.workspace_dir:
        return False, 'missing workspace_dir on evidence'
    cfg: dict[str, Any] = ref.value if isinstance(ref.value, dict) else {}
    unit_cell_atoms = int(cfg.get('unit_cell_atoms', 144))
    slab_atoms = int(cfg.get('slab_atoms', 576))
    layers = int(cfg.get('layers', 4))
    return verify_molecular_slab_layer_scaling(
        evidence.workspace_dir,
        unit_cell_atoms=unit_cell_atoms,
        slab_atoms=slab_atoms,
        layers=layers,
    )


def check_sc005_disorder_formulas(*, answer: str) -> tuple[bool, str]:
    ok, reason = check_sc005_other_formulas_in_answer(answer)
    if not ok:
        return ok, reason
    return check_disorder_dan2_integer_formula(answer)


def check_molcrys_local_env_from_evidence(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    """Bridge evaluator dispatch -> MolCrysKit local-environment validator."""
    if evidence is None or not evidence.workspace_dir:
        return False, 'missing workspace_dir on evidence'
    cfg: dict[str, Any] = ref.value if isinstance(ref.value, dict) else {}
    filename = cfg.get('filename', '*.cif')
    expected_formula = cfg.get('expected_formula', '')
    z_value = int(cfg.get('z_value', 4))
    if not expected_formula:
        return False, 'reference answer missing expected_formula'
    return check_molcrys_local_env(
        evidence.workspace_dir,
        filename=filename,
        expected_formula=expected_formula,
        z_value=z_value,
    )


def check_atomworld_active_task_from_evidence(
    *,
    evidence: EvidenceBundle | None,
    ref: ReferenceAnswer,
    question_dir: Path | None,
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    target_rel_path = str(cfg.get('target_path', '')).strip()
    target_cif = str(cfg.get('target_cif', '')).strip()
    if not target_cif:
        if not target_rel_path:
            return False, 'reference answer missing target_path or target_cif'
        if question_dir is None:
            return False, 'question_dir unavailable for target_path resolution'
        target_path = question_dir / target_rel_path
        if not target_path.is_file():
            return False, f'target file not found: {target_path.name}'
        try:
            target_cif = target_path.read_text(encoding='utf-8')
        except Exception as exc:
            return False, f'could not read target file {target_path.name}: {exc}'

    return check_atomworld_active_task(
        ws,
        filename=str(cfg.get('filename', '*.cif')),
        target_cif=target_cif,
        action_name=str(cfg.get('action_name', '')).strip() or None,
    )


# ---------------------------------------------------------------------------
# struct_file_* bridge checks
# ---------------------------------------------------------------------------


def check_struct_file_atom_count(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_atom_count(
        ws,
        filename=cfg.get('filename', '*.cif'),
        expected=int(cfg.get('expected', 0)),
        tolerance=float(cfg.get('tolerance', 0)),
        element=str(cfg.get('element')) if cfg.get('element') else None,
    )


def check_struct_file_formula(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_formula(
        ws,
        filename=cfg.get('filename', '*.cif'),
        formula=str(cfg.get('formula', '')),
    )


def check_struct_file_space_group(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    if 'expected_number' not in cfg:
        return False, "reference answer missing 'expected_number'"
    return check_space_group(
        ws,
        filename=str(cfg.get('filename', '*.cif')),
        expected_number=int(cfg['expected_number']),
        symprec=float(cfg.get('symprec', 0.1)),
        angle_tolerance=float(cfg.get('angle_tolerance', 5.0)),
    )


def check_struct_file_min_interatomic_distance(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    if 'min_distance_A' not in cfg:
        return False, "reference answer missing 'min_distance_A'"
    return check_min_interatomic_distance(
        ws,
        filename=str(cfg.get('filename', '*.cif')),
        min_distance_A=float(cfg['min_distance_A']),
    )


def check_struct_file_bond_count(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_bond_count(
        ws,
        filename=cfg.get('filename', '*.cif'),
        element_a=str(cfg.get('element_a', '')),
        element_b=str(cfg.get('element_b', '')),
        cutoff_A=float(cfg.get('cutoff_A', 2.0)),
        expected_count=int(cfg.get('expected_count', 0)),
        tolerance=float(cfg.get('tolerance', 0)),
    )


def check_struct_file_bond_length(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_bond_length(
        ws,
        filename=cfg.get('filename', '*.cif'),
        element_a=str(cfg.get('element_a', '')),
        element_b=str(cfg.get('element_b', '')),
        cutoff_A=float(cfg.get('cutoff_A', 3.0)),
        expected=float(cfg.get('expected', 0)),
        tolerance=float(cfg.get('tolerance', 0)),
    )


def check_struct_file_bond_angle(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_bond_angle(
        ws,
        filename=cfg.get('filename', '*.cif'),
        triplet=list(cfg.get('triplet', [])),
        expected_deg=float(cfg.get('expected_deg', 0)),
        tolerance_deg=float(cfg.get('tolerance_deg', 5.0)),
        cutoff_A=float(cfg.get('cutoff_A', 3.0)),
    )


def check_struct_file_cell_param(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_cell_param(
        ws,
        filename=cfg.get('filename', '*.cif'),
        param=str(cfg.get('param', 'alpha')),
        expected=float(cfg.get('expected', 0)),
        tolerance=float(cfg.get('tolerance', 0)),
    )


def check_struct_file_stoichiometry_ratio(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_stoichiometry_ratio(
        ws,
        filename=cfg.get('filename', '*.cif'),
        element_a=str(cfg.get('element_a', '')),
        element_b=str(cfg.get('element_b', '')),
        expected_ratio=float(cfg.get('expected_ratio', 0)),
        tolerance=float(cfg.get('tolerance', 0)),
    )


def check_struct_file_coordination(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_coordination_number(
        ws,
        filename=cfg.get('filename', '*.cif'),
        center_element=str(cfg.get('center_element', '')),
        expected=int(cfg.get('expected', 0)),
        tolerance=float(cfg.get('tolerance', 0)),
        cutoff_A=float(cfg.get('cutoff_A', 2.5)),
    )


def check_struct_file_layer_count(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    if 'layer_tol_A' in cfg:
        layer_tol = float(cfg['layer_tol_A'])
    elif 'gap_threshold_A' in cfg:
        layer_tol = float(cfg['gap_threshold_A'])
    else:
        layer_tol = 0.25
    return check_layer_count(
        ws,
        filename=cfg.get('filename', '*.cif'),
        expected=int(cfg.get('expected', 0)),
        tolerance=float(cfg.get('tolerance', 0)),
        axis=str(cfg.get('axis', 'z')),
        layer_tol_A=layer_tol,
        element=str(cfg.get('element')) if cfg.get('element') else None,
    )


def check_struct_file_count(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_file_count(
        ws,
        pattern=cfg.get('pattern', '*.cif'),
        expected=int(cfg.get('expected', 0)),
        tolerance=int(cfg.get('tolerance', 0)),
    )


def check_checkcif_alerts(
    *,
    evidence: EvidenceBundle | None,
    ref: ReferenceAnswer,
) -> tuple[bool, str]:
    workspace_dir, _ = _get_workspace(evidence)
    if workspace_dir is None:
        return False, 'no workspace directory available in evidence'

    val = ref.value or {}
    filename = val.get('filename', '*.cif') if isinstance(val, dict) else '*.cif'
    max_a_alerts = int(val.get('max_a_alerts', 0)) if isinstance(val, dict) else 0

    return check_checkcif_no_a_alerts(
        workspace_dir,
        filename=filename,
        max_a_alerts=max_a_alerts,
    )


def check_struct_file_surface_termination(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_surface_termination(
        ws,
        filename=cfg.get('filename', '*.cif'),
        element=str(cfg.get('element', '')),
        axis=str(cfg.get('axis', 'z')),
        side=str(cfg.get('side', 'top')),
        layer_tol_A=float(cfg.get('layer_tol_A', 0.5)),
    )


# ---------------------------------------------------------------------------
# text_file_* bridge checks
# ---------------------------------------------------------------------------


def check_text_file_contains_all_from_evidence(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    raw_tokens = cfg.get('tokens', [])
    if not isinstance(raw_tokens, list) or not raw_tokens:
        return False, "reference answer must provide non-empty 'tokens' list"
    flags = str(cfg.get('flags', '')).lower()
    case_sensitive = bool(cfg.get('case_sensitive', False))
    if 'i' in flags:
        case_sensitive = False
    return check_text_file_contains_all(
        ws,
        filename=str(cfg.get('filename', '')),
        tokens=[str(token) for token in raw_tokens],
        case_sensitive=case_sensitive,
        normalize_whitespace=bool(cfg.get('normalize_whitespace', True)),
        workspace_resolve=_workspace_resolve_from_ref(ref),
    )


def check_text_file_regex_from_evidence(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    pattern = str(cfg.get('pattern', ''))
    if not pattern:
        return False, "reference answer must provide non-empty 'pattern'"
    return check_text_file_regex(
        ws,
        filename=str(cfg.get('filename', '')),
        pattern=pattern,
        flags=str(cfg.get('flags', '')),
        workspace_resolve=_workspace_resolve_from_ref(ref),
    )


def check_text_file_numeric_range_from_evidence(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    raw_checks = cfg.get('checks', [])
    if not isinstance(raw_checks, list) or not raw_checks:
        return False, "reference answer must provide non-empty 'checks' list"
    checks: list[dict[str, Any]] = []
    for item in raw_checks:
        if not isinstance(item, dict):
            return False, "each entry in 'checks' must be a dict"
        checks.append(item)
    return check_text_file_numeric_range(
        ws,
        filename=str(cfg.get('filename', '')),
        checks=checks,
        workspace_resolve=_workspace_resolve_from_ref(ref),
    )


def check_answer_json_numeric_from_evidence(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    filename = str(cfg.get('filename', '')).strip()
    if not filename:
        return False, "reference answer must provide non-empty 'filename'"
    raw_checks = cfg.get('checks', [])
    if not isinstance(raw_checks, list) or not raw_checks:
        return False, "reference answer must provide non-empty 'checks' list"

    fpath = _resolve_workspace_file(
        ws,
        filename,
        workspace_resolve=_workspace_resolve_from_ref(ref),
    )
    if fpath is None:
        return False, f'no file matching {filename!r} in {ws}'
    try:
        data = json.loads(fpath.read_text(encoding='utf-8'))
    except Exception as exc:
        return False, f'failed parsing {fpath.name} as JSON: {exc}'

    details: list[str] = []
    for rule in raw_checks:
        if not isinstance(rule, dict):
            return False, "each entry in 'checks' must be a dict"
        key = str(rule.get('key', '')).strip()
        if not key:
            return False, "numeric rule missing non-empty 'key'"
        found, raw_value = _get_json_path(data, key)
        if not found:
            return False, f"{fpath.name}: key {key!r} not found"

        allowed_values = rule.get('allowed_values')
        if allowed_values is not None:
            allowed = {str(v).lower() for v in allowed_values}
            actual = str(raw_value).lower()
            if actual not in allowed:
                return (
                    False,
                    f"{fpath.name}: key {key!r}: value={raw_value!r} not in allowed={sorted(allowed)}",
                )
            details.append(f'{key}={raw_value!r}')
            continue

        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            return False, f"{fpath.name}: key {key!r}: value {raw_value!r} is not numeric"

        if 'expected' in rule:
            expected = float(rule['expected'])
            tolerance = float(rule.get('tolerance', 0))
            if abs(value - expected) > tolerance:
                return (
                    False,
                    f"{fpath.name}: key {key!r}: value={value}, expected={expected}, tolerance={tolerance}",
                )

        if 'min' in rule and value < float(rule['min']):
            return False, f"{fpath.name}: key {key!r}: value={value} < min={rule['min']}"
        if 'max' in rule and value > float(rule['max']):
            return False, f"{fpath.name}: key {key!r}: value={value} > max={rule['max']}"
        details.append(f'{key}={value:g}')

    return True, f"{fpath.name}: all JSON numeric checks passed ({', '.join(details)})"


def check_text_file_kpt_path_from_evidence(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    raw_required = cfg.get('required_points', [])
    if not isinstance(raw_required, list) or not raw_required:
        return False, "reference answer must provide non-empty 'required_points' list"
    required_points: list[list[float]] = []
    for item in raw_required:
        if not isinstance(item, list) or len(item) != 3:
            return False, "each entry in 'required_points' must be [x, y, z]"
        try:
            required_points.append([float(item[0]), float(item[1]), float(item[2])])
        except (TypeError, ValueError):
            return False, 'required_points entries must be numeric'
    return check_text_file_kpt_path(
        ws,
        filename=str(cfg.get('filename', '')),
        required_points=required_points,
        tolerance=float(cfg.get('tolerance', 1.0e-6)),
        require_line_mode=bool(cfg.get('require_line_mode', True)),
        require_order=bool(cfg.get('require_order', True)),
        workspace_resolve=_workspace_resolve_from_ref(ref),
    )


# ---------------------------------------------------------------------------
# Batch checks
# ---------------------------------------------------------------------------


def check_batch_single_variable_sweep(
    *,
    tool_calls: list[dict[str, Any]],
    evidence: EvidenceBundle | None,
    ref: ReferenceAnswer,
) -> tuple[bool, str]:
    """Verify that across multiple calls, only one parameter varies."""
    sweep_cfg = ref.value if isinstance(ref.value, dict) else {}
    tool_name = ref.tool_name or sweep_cfg.get('tool_name')
    sweep_var = ref.tool_arg or sweep_cfg.get('sweep_param')
    if not sweep_var:
        return (
            False,
            'batch_single_variable_sweep requires sweep_param '
            '(via tool_arg or value.sweep_param)',
        )
    sweep_var = str(sweep_var)

    if tool_name:
        names = _split_tool_names(str(tool_name))
        matching_calls = [c for c in tool_calls if c.get('tool_name') in names]
    else:
        names = None
        matching_calls = [
            c for c in tool_calls if sweep_var in (c.get('tool_args') or {})
        ]

    if len(matching_calls) < 2:
        label = names if names else f'any call with {sweep_var!r}'
        return (
            False,
            f'need at least 2 calls to {label} for sweep check, found {len(matching_calls)}',
        )

    all_args = [call.get('tool_args', {}) for call in matching_calls]
    if not all_args[0]:
        return False, 'first call has no arguments'

    all_param_names = set(all_args[0].keys())
    exclude = set(sweep_cfg.get('exclude_params', []))

    for param in all_param_names:
        if param == sweep_var or param in exclude:
            continue
        values = [str(args.get(param, '<missing>')) for args in all_args]
        if len(set(values)) > 1:
            return (
                False,
                f"parameter '{param}' varies across calls (expected constant): {values}",
            )

    sweep_values = [args.get(sweep_var, '<missing>') for args in all_args]
    if len({str(v) for v in sweep_values}) < 2:
        return False, f"sweep parameter '{sweep_var}' does not vary: {sweep_values}"

    expected_vals = sweep_cfg.get('expected_values') if sweep_cfg else None
    if (
        expected_vals is None
        and ref.value is not None
        and not isinstance(ref.value, dict)
    ):
        expected_vals = ref.value if isinstance(ref.value, list) else [ref.value]
    if expected_vals is not None:
        expected_strs = {str(v) for v in expected_vals}
        actual_strs = {str(v) for v in sweep_values}
        if actual_strs != expected_strs:
            return (
                False,
                f'sweep values {actual_strs} do not match expected {expected_strs}',
            )

    return (
        True,
        f'single variable sweep verified: {sweep_var} varies, other params constant',
    )


def check_batch_tool_args_constant(
    *,
    tool_calls: list[dict[str, Any]],
    evidence: EvidenceBundle | None,
    ref: ReferenceAnswer,
) -> tuple[bool, str]:
    """Verify that across multiple calls, specified parameters remain constant."""
    val_cfg = ref.value if isinstance(ref.value, dict) else {}
    tool_name = ref.tool_name or val_cfg.get('tool_name')
    param_arg = ref.tool_arg or val_cfg.get('param_names')
    if not param_arg:
        return (
            False,
            'batch_tool_args_constant requires param_names '
            '(via tool_arg or value.param_names)',
        )

    param_names = [p.strip() for p in str(param_arg).split(',')]

    if tool_name:
        names = _split_tool_names(str(tool_name))
        matching_calls = [c for c in tool_calls if c.get('tool_name') in names]
    else:
        names = None
        matching_calls = [
            c
            for c in tool_calls
            if any(p in (c.get('tool_args') or {}) for p in param_names)
        ]

    if len(matching_calls) < 2:
        label = names if names else f'any call with {param_names}'
        return (
            False,
            f'need at least 2 calls to {label}, found {len(matching_calls)}',
        )

    all_args = [call.get('tool_args', {}) for call in matching_calls]

    for param in param_names:
        values = [args.get(param, '<missing>') for args in all_args]
        if len({str(v) for v in values}) > 1:
            return False, f"parameter '{param}' varies across calls: {values}"

        expected = None
        if isinstance(ref.value, dict):
            if param in ref.value:
                expected = ref.value[param]
            elif 'expected_constant' in ref.value:
                expected = ref.value['expected_constant']
        if expected is not None:
            actual = values[0]
            if str(actual) != str(expected):
                return (
                    False,
                    f"parameter '{param}' is {actual}, expected {expected}",
                )

    return True, f"batch parameters constant: {', '.join(param_names)}"


def check_batch_consistent_calls(
    *,
    tool_calls: list[dict[str, Any]],
    evidence: EvidenceBundle | None,
    ref: ReferenceAnswer,
) -> tuple[bool, str]:
    """Verify that calls follow a consistent pattern (e.g., same tool, same order)."""
    if not ref.tool_name:
        return False, 'batch_consistent_calls requires tool_name'

    if not isinstance(ref.value, dict):
        return (
            False,
            'batch_consistent_calls requires value as dict with pattern config',
        )

    min_calls = int(ref.value.get('min_calls', 1))
    max_calls = int(ref.value.get('max_calls', 9999))
    pattern = ref.value.get('pattern', 'sequential')
    pattern_tools = ref.value.get('tools', [])

    matching_calls = tool_calls
    if isinstance(ref.tool_name, str) and ref.tool_name.strip():
        names = _split_tool_names(ref.tool_name)
        matching_calls = [c for c in tool_calls if c.get('tool_name') in names]

    if not (min_calls <= len(matching_calls) <= max_calls):
        return (
            False,
            f'call count {len(matching_calls)} not in range [{min_calls}, {max_calls}]',
        )

    if pattern == 'grouped' and pattern_tools:
        actual_sequence = [c.get('tool_name') for c in tool_calls]
        expected_len = len(pattern_tools)
        if len(actual_sequence) % expected_len != 0:
            return (
                False,
                f'sequence length {len(actual_sequence)} not multiple of '
                f'pattern length {expected_len}',
            )
        for i, tool_name_i in enumerate(actual_sequence):
            expected_tool = pattern_tools[i % expected_len]
            if tool_name_i != expected_tool:
                return (
                    False,
                    f'position {i}: expected {expected_tool}, got {tool_name_i}',
                )

    return (
        True,
        f'batch calls consistent: {len(matching_calls)} calls, pattern={pattern}',
    )
