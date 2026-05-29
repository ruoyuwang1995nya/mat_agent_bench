"""Registry pattern for loading MATTER v5 question bank.

Each question lives in its own subdirectory under question_bank/::

    question_bank/
    ├── SR_general_001/
    │   ├── question.yaml
    │   └── policy_excerpt.md
    ├── IG_incar_001/
    │   ├── question.yaml
    │   └── task_spec.json
    ...

The ``question.yaml`` file contains a single question (raw QuestionItem fields,
not wrapped in a ``questions:`` list).  Data file paths inside ``question.yaml``
are relative to their question directory.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from ..schemas import QuestionItem

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Question:
    """A loaded question with its directory for data file resolution."""

    item: QuestionItem
    question_dir: Path

    @property
    def id(self) -> str:
        return self.item.id

    @property
    def capability(self) -> list[str]:
        return list(self.item.capabilities)

    @property
    def task_type(self) -> str:
        return self.item.task_type

    @property
    def domain(self) -> str:
        return self.item.domain

    @property
    def intent(self) -> str:
        return self.item.intent

    @property
    def tags(self) -> list[str]:
        return [str(t) for t in self.item.tags]

    def data_file_path(self, key: str) -> Path | None:
        """Resolve a data file reference by key to an absolute path."""
        for df in self.item.data_files:
            if df.key == key:
                candidate = self.question_dir / df.path
                return candidate if candidate.exists() else None
        return None


class Registry:
    """Scans a question_bank directory and provides access to questions.

    Each subdirectory containing a ``question.yaml`` file is treated as one
    question.  No manifest required — adding a directory adds a question.

    Usage::

        registry = Registry("question_bank")
        q = registry.get_question("SR_general_001")
        ids = registry.list_question_ids()
    """

    def __init__(self, question_bank_dir: str | Path) -> None:
        self._root = Path(question_bank_dir).resolve()
        if not self._root.is_dir():
            raise FileNotFoundError(
                f'question_bank directory not found: {self._root}'
            )
        self._questions: dict[str, Question] = {}
        self._load()

    def _load(self) -> None:
        """Scan all ``*/question.yaml`` files one level below the root."""
        for q_yaml in sorted(self._root.glob('*/question.yaml')):
            self._load_question_file(q_yaml)

    def _load_question_file(self, path: Path) -> None:
        try:
            with path.open('r', encoding='utf-8') as f:
                raw = yaml.safe_load(f)
        except Exception as exc:
            _logger.warning('failed to parse %s: %s', path, exc)
            return

        if not isinstance(raw, dict):
            _logger.warning('invalid question.yaml (expected a dict): %s', path)
            return

        from ..schemas import QuestionItem  # local import to avoid circular dependency
        try:
            item = QuestionItem.model_validate(raw)
        except Exception as exc:
            _logger.warning('failed to validate %s: %s', path, exc)
            return

        question = Question(item=item, question_dir=path.parent)
        if item.id in self._questions:
            _logger.warning('duplicate question id: %s', item.id)
        self._questions[item.id] = question

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_question(self, question_id: str) -> Question:
        """Get a question by ID. Raises KeyError if not found."""
        if question_id not in self._questions:
            raise KeyError(
                f'question {question_id!r} not found in registry '
                f'({len(self._questions)} questions loaded)'
            )
        return self._questions[question_id]

    def list_question_ids(self) -> list[str]:
        """Return all question IDs in sorted order."""
        return sorted(self._questions.keys())

    def list_questions(
        self,
        *,
        capability: str | None = None,
        task_type: str | None = None,
        domain: str | None = None,
        tags: list[str] | None = None,
    ) -> list[Question]:
        """List questions with optional filters."""
        results = list(self._questions.values())
        if capability:
            results = [q for q in results if capability in q.capability]
        if task_type:
            results = [q for q in results if q.task_type == task_type]
        if domain:
            results = [q for q in results if q.domain == domain]
        if tags:
            tag_set = set(tags)
            results = [q for q in results if tag_set.issubset(set(q.tags))]
        return sorted(results, key=lambda q: q.id)

    def __len__(self) -> int:
        return len(self._questions)

    def __contains__(self, question_id: str) -> bool:
        return question_id in self._questions

    def __repr__(self) -> str:
        return f'Registry({self._root}, {len(self._questions)} questions)'
