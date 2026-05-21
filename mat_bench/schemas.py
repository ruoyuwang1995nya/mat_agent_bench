"""Pydantic models for MATTER v5 evaluation.

Scoring model:
- Verifiers produce binary (pass/fail) verdicts per checklist item.
- Each checklist item has optional weight (default 1.0).
- S_correct  = weighted average of correctness criteria  [0, 1]
- S_ground   = binary veto: 1.0 if all grounding criteria pass (or none), else 0.0
- S_efficiency = weighted average of efficiency criteria [0, 1]; 1.0 if no criteria
- overall_score = S_correct × S_ground × S_efficiency
"""

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .registry.tags import QuestionTag

# ---------------------------------------------------------------------------
# Literal type aliases
# ---------------------------------------------------------------------------

ModeLiteral = Literal['direct', 'planner']
WorkspaceResolveLiteral = Literal['recursive', 'root']

VerifyLiteral = Literal[
    'exact_match',
    'numerical_range',
    'contains_all',
    'tool_called',
    'tool_args_match',
    'tool_observation_field',
    'event_type_called',
    'source_type_used',
    'call_count_range',
    'no_retries',
    'artifact_exists',
    'token_budget',
    'turn_budget',
    'duration_budget',
    'molcrys_slab_molecular_integrity',
    'molcrys_local_env',
    'sc005_disorder_formulas',
    'llm_binary_judge',
    'batch_single_variable_sweep',
    'batch_tool_args_constant',
    'batch_consistent_calls',
    'struct_file_atom_count',
    'struct_file_formula',
    'struct_file_bond_count',
    'struct_file_bond_length',
    'struct_file_bond_angle',
    'struct_file_cell_param',
    'struct_file_stoichiometry_ratio',
    'struct_file_coordination',
    'struct_file_layer_count',
    'struct_file_count',
    'struct_file_surface_termination',
    'checkcif_no_a_alerts',
    'text_file_contains_all',
    'text_file_kpt_path',
    'text_file_numeric_range',
    'text_file_regex',
    'struct_file_space_group',
    'struct_file_parsable',
    'struct_file_min_interatomic_distance',
    'struct_file_all_occupancy_one',
    'struct_file_integer_stoichiometry',
    'struct_file_replicas_distinct',
    'answer_json_numeric',
    'json_file_artifacts',
    'atomworld_active_task',
]

AxisLiteral = Literal['correctness', 'grounding', 'efficiency']

CapabilityLiteral = Literal[
    'structure_construction',
    'structure_retrieval',
    'scientific_analysis',
    'workflow_orchestration',
    'execution_contract',
    'data_diagnosis',
    'batch_processing',
    'safety_refusal',
    'input_generation',
]

DomainLiteral = Literal[
    'battery',
    'catalysis',
    'polymer',
    'alloy',
    'semiconductor',
    'agnostic',
]

GENERIC_PROCESS_TAGS = {
    'workflow',
    'workflow_acceleration',
    'workflow_closure',
    'loop_oriented',
    'plotting',
    'structure_build',
}

CANONICAL_TAG_ALIASES = {
    'HEA': 'hea',
    'SrTiO3': 'srtio3',
    'srti03': 'srtio3',
    'Al2O3': 'al2o3',
    'Li2O': 'li2o',
    'MgO': 'mgo',
    'CeO2': 'ceo2',
    'MoS2': 'mos2',
    'hBN': 'hbn',
    'DACMOR': 'dacmor',
    'Ag111': 'ag111',
    'Si100': 'si100',
    'CuCrZr': 'cucrzr',
}


# ---------------------------------------------------------------------------
# Shared small models
# ---------------------------------------------------------------------------


class DataFileRef(BaseModel):
    """Reference to a concrete input file used by a question."""

    key: str
    path: str
    oss_url: str = ''
    description: str = ''

    @field_validator('path')
    @classmethod
    def _validate_path(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('data file path cannot be empty')
        return value


class ReferenceAnswer(BaseModel):
    """Ground-truth value used by checklist scoring."""

    key: str
    value: Any
    tolerance: float | None = None
    unit: str = ''
    tool_name: str | None = None
    tool_arg: str | None = None
    workspace_resolve: WorkspaceResolveLiteral | None = Field(
        default=None,
        description=(
            'Where to resolve plain filenames for artifact_exists / text_file_* checks. '
            'None or "recursive" = match under workspace by basename (legacy). '
            '"root" = only a direct child of workspace_dir (exact path).'
        ),
    )

    @field_validator('tolerance')
    @classmethod
    def _validate_tolerance(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value < 0:
            raise ValueError('tolerance must be >= 0')
        return value


# ---------------------------------------------------------------------------
# Core scoring models
# ---------------------------------------------------------------------------


class ScoringCheckItem(BaseModel):
    """One verifiable scoring criterion (binary with optional weight)."""

    id: str
    criterion: str
    axis: AxisLiteral = Field(default='correctness')
    verify: VerifyLiteral
    weight: float = Field(default=1.0, ge=0.0)


class CriterionResult(BaseModel):
    """Per-criterion pass/fail result stored inside EvalRunRecord."""

    criterion_id: str
    axis: AxisLiteral
    passed: bool
    reason: str = ''
    verify_method: str = ''


# ---------------------------------------------------------------------------
# Question model
# ---------------------------------------------------------------------------


class QuestionItem(BaseModel):
    """Single MATTER v5 question entry."""

    id: str
    capability: CapabilityLiteral
    domain: DomainLiteral
    intent: str
    human_prompt_seed: str
    tags: list[QuestionTag] = Field(default_factory=list)
    priority: str | None = Field(default=None)
    data_files: list[DataFileRef] = Field(default_factory=list)
    reference_answers: list[ReferenceAnswer] = Field(default_factory=list)
    scoring_checklist: list[ScoringCheckItem] = Field(default_factory=list)

    @field_validator('tags', mode='before')
    @classmethod
    def _validate_tags_before(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise TypeError('tags must be a list')
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw_tag in value:
            tag = str(raw_tag).strip()
            if not tag:
                raise ValueError('tags must not contain empty strings')
            canonical = CANONICAL_TAG_ALIASES.get(tag)
            if canonical is not None:
                raise ValueError(
                    f'tag {tag!r} is not canonical; use canonical tag {canonical!r}'
                )
            if tag in GENERIC_PROCESS_TAGS:
                raise ValueError(
                    f'tag {tag!r} is a generic process tag; use a topic/tool/method tag instead'
                )
            if tag in seen:
                raise ValueError(f'tags must be unique within a question: {tag!r}')
            seen.add(tag)
            cleaned.append(tag)
        return cleaned

    @model_validator(mode='after')
    def _validate_scoring_contract(self) -> 'QuestionItem':
        tag_values = {t.value for t in self.tags}
        if self.capability in tag_values:
            raise ValueError(
                f'tag {self.capability!r} must not repeat question capability'
            )
        if self.domain in tag_values:
            raise ValueError(f'tag {self.domain!r} must not repeat question domain')
        if not self.scoring_checklist:
            raise ValueError(
                'question must include at least one scoring_checklist entry'
            )
        ref_keys = {item.key for item in self.reference_answers}
        _needs_ref = {
            'exact_match',
            'numerical_range',
            'contains_all',
            'tool_called',
            'tool_args_match',
            'tool_observation_field',
            'event_type_called',
            'source_type_used',
            'call_count_range',
            'batch_single_variable_sweep',
            'batch_tool_args_constant',
            'batch_consistent_calls',
            'duration_budget',
            'turn_budget',
            'molcrys_slab_molecular_integrity',
            'molcrys_local_env',
            'sc005_disorder_formulas',
            'text_file_contains_all',
            'text_file_kpt_path',
            'text_file_numeric_range',
            'text_file_regex',
            'struct_file_space_group',
            'struct_file_parsable',
            'struct_file_min_interatomic_distance',
            'struct_file_all_occupancy_one',
            'struct_file_integer_stoichiometry',
            'struct_file_replicas_distinct',
            'answer_json_numeric',
            'json_file_artifacts',
            'atomworld_active_task',
        }
        for item in self.scoring_checklist:
            if item.verify in _needs_ref and item.id not in ref_keys:
                raise ValueError(
                    f"scoring_checklist item '{item.id}' (verify={item.verify}) "
                    'requires a matching reference_answers entry with the same key'
                )
        if self.capability != 'safety_refusal' and not self.reference_answers:
            raise ValueError('non-safety questions must include reference_answers')
        return self


# ---------------------------------------------------------------------------
# QuestionBank (for loading YAML files)
# ---------------------------------------------------------------------------


class QuestionBank(BaseModel):
    """Question bank file model (v5 format)."""

    version: str = 'v5'
    capability: CapabilityLiteral | None = None
    domain: DomainLiteral | None = None
    questions: list[QuestionItem]

    @model_validator(mode='after')
    def _validate_questions(self) -> 'QuestionBank':
        if not self.questions:
            raise ValueError('questions cannot be empty')
        if self.capability is None:
            raise ValueError('top-level capability is required for every bank')
        mismatched_capabilities = sorted(
            q.id for q in self.questions if q.capability != self.capability
        )
        if mismatched_capabilities:
            raise ValueError(
                'top-level capability must match every question capability; '
                f'mismatched question ids: {mismatched_capabilities}'
            )
        if self.domain is None:
            raise ValueError('top-level domain is required for every bank')
        mismatched_domains = sorted(
            q.id for q in self.questions if q.domain != self.domain
        )
        if mismatched_domains:
            raise ValueError(
                'top-level domain must match every question domain; '
                f'mismatched question ids: {mismatched_domains}'
            )
        return self


# ---------------------------------------------------------------------------
# Config models
# ---------------------------------------------------------------------------


class LLMConfig(BaseModel):
    """LLM config for the evaluator judge."""

    #provider: str
    model: str
    api_key: str
    base_url: str | None = None
    temperature: float = 0.0
    max_tokens: int | None = 4096
    timeout: int = 180


# ---------------------------------------------------------------------------
# Run record models
# ---------------------------------------------------------------------------


class SafetyVetoRecord(BaseModel):
    """Safety refusal verdict for a single run."""

    triggered: bool = False
    reason: str = ''
    risk_not_detected: bool = True
    detail_non_leakage: bool = True
    safe_redirection: bool = True


class TokenUsageRecord(BaseModel):
    """Serialisable token usage summary stored in EvalRunRecord."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0
    total_tokens_effective: int = 0


class EvalRunRecord(BaseModel):
    """Atomic run record: one question, one mode, one repeat."""

    question_id: str
    capability: str = ''
    domain: str = ''
    mode: ModeLiteral
    repeat_idx: int
    prompt: str
    answer: str
    run_status: str

    criteria_results: dict[str, CriterionResult] = Field(default_factory=dict)
    passed_count: int = 0
    total_count: int = 0
    correctness_passed: int = 0
    correctness_total: int = 0
    grounding_passed: int = 0
    grounding_total: int = 0
    efficiency_passed: int = 0
    efficiency_total: int = 0

    correctness_weighted_score: float = 0.0
    grounding_weighted_score: float = 0.0
    efficiency_weighted_score: float = 0.0
    overall_weighted_score: float = 0.0
    grounding_veto: bool = False  # True = grounding veto triggered (any grounding criterion failed)

    model_name: str | None = None
    duration_ms: int = Field(default=0)
    token_usage: TokenUsageRecord = Field(default_factory=TokenUsageRecord)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    safety_veto: SafetyVetoRecord = Field(default_factory=SafetyVetoRecord)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    raw_result: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Summary models
# ---------------------------------------------------------------------------


class AxisPassRates(BaseModel):
    """Pass counts for each axis within a group."""

    correctness: tuple[int, int] = (0, 0)
    grounding: tuple[int, int] = (0, 0)
    efficiency: tuple[int, int] = (0, 0)
    overall: tuple[int, int] = (0, 0)

    def pass_rate(self, axis: str = 'overall') -> float:
        pair = getattr(self, axis, self.overall)
        passed, total = pair
        return passed / total if total > 0 else 0.0

    def fmt(self, axis: str = 'overall') -> str:
        pair = getattr(self, axis, self.overall)
        passed, total = pair
        pct = f'{100 * passed / total:.1f}%' if total > 0 else '—'
        return f'{passed}/{total} ({pct})'


class QuestionPassRate(BaseModel):
    """Per-question pass rate summary."""

    question_id: str
    capability: str
    domain: str
    runs: int = 0
    overall: tuple[int, int] = (0, 0)
    correctness: tuple[int, int] = (0, 0)
    grounding: tuple[int, int] = (0, 0)
    efficiency: tuple[int, int] = (0, 0)
    safety_veto_count: int = 0


class EvaluationSummary(BaseModel):
    """Aggregated result object: both raw pass-rates and weighted scores."""

    total_runs: int
    total_criteria: int = 0
    total_passed: int = 0
    questions_passed: int = 0
    pass_rate: float = 0.0
    weighted_pass_rate: float = 0.0

    by_capability: dict[str, AxisPassRates] = Field(default_factory=dict)
    by_domain: dict[str, AxisPassRates] = Field(default_factory=dict)
    by_question: dict[str, QuestionPassRate] = Field(default_factory=dict)
    by_mode: dict[str, AxisPassRates] = Field(default_factory=dict)
    by_model: dict[str, AxisPassRates] = Field(default_factory=dict)
    safety: dict[str, Any] = Field(default_factory=dict)
