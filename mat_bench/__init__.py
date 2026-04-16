"""mat-bench: MATTER v5 benchmark evaluation toolkit.

Public API exports for programmatic use.
"""

from .aggregator import build_summary
from .evaluator import BinaryEvaluator
from .evidence import EvidenceBundle, EvidenceExtractor
from .grade import QuestionReport, RunReport, grade_question, grade_run
from .registry import Question, Registry
from .reporter import (
    append_raw_run,
    generate_rating_from_raw_runs,
    load_records_from_jsonl,
    write_reports,
)
from .schemas import (
    CriterionResult,
    EvalRunRecord,
    EvaluationSummary,
    LLMConfig,
    QuestionBank,
    QuestionItem,
    ReferenceAnswer,
    ScoringCheckItem,
    TokenUsageRecord,
)

__all__ = [
    # Registry
    'Registry',
    'Question',
    # Grading
    'grade_question',
    'grade_run',
    'QuestionReport',
    'RunReport',
    # Evaluator
    'BinaryEvaluator',
    # Evidence
    'EvidenceBundle',
    'EvidenceExtractor',
    # Aggregation & Reporting
    'build_summary',
    'write_reports',
    'append_raw_run',
    'load_records_from_jsonl',
    'generate_rating_from_raw_runs',
    # Schemas
    'QuestionItem',
    'QuestionBank',
    'ScoringCheckItem',
    'ReferenceAnswer',
    'CriterionResult',
    'EvalRunRecord',
    'EvaluationSummary',
    'LLMConfig',
    'TokenUsageRecord',
]
