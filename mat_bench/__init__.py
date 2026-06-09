"""mat-bench: MATTER v5 benchmark evaluation toolkit.

Public API exports for programmatic use.
"""

from .evaluation import (
    BinaryEvaluator,
    EvidenceBundle,
    EvidenceExtractor,
    QuestionReport,
    RunReport,
    grade_question,
    grade_run,
)
from .registry import Question, Registry
from .reporting import (
    append_raw_run,
    build_summary,
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
    TaskTypeLiteral,
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
    'TaskTypeLiteral',
]
