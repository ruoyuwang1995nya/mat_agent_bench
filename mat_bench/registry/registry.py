"""Registry pattern for loading MATTER v5 question bank.

Inspired by mle-bench's Registry + Competition pattern.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from ..schemas import QuestionItem

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Question:
    """A loaded question with resolved data file paths."""

    item: QuestionItem
    bank_file: Path
    data_dir: Path | None = None

    @property
    def id(self) -> str:
        return self.item.id

    @property
    def capability(self) -> str:
        return self.item.capability

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
                if self.data_dir and (self.data_dir / df.path).exists():
                    return self.data_dir / df.path
                # Try relative to bank file
                candidate = self.bank_file.parent / df.path
                if candidate.exists():
                    return candidate
                return None
        return None


class Registry:
    """Scans a question_bank directory and provides access to questions.

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
        """Scan all bank YAML files referenced by manifest.yaml."""
        manifest_path = self._root / 'manifest.yaml'
        if manifest_path.exists():
            self._load_from_manifest(manifest_path)
        else:
            # Fallback: scan all YAML files recursively
            self._load_all_yamls()

    def _load_from_manifest(self, manifest_path: Path) -> None:
        with manifest_path.open('r', encoding='utf-8') as f:
            manifest = yaml.safe_load(f)
        banks = manifest.get('banks', [])
        for bank_entry in banks:
            bank_path = self._root / bank_entry['path']
            if not bank_path.exists():
                _logger.warning('bank file not found: %s', bank_path)
                continue
            self._load_bank_file(bank_path)

    def _load_all_yamls(self) -> None:
        for yaml_path in sorted(self._root.rglob('*.yaml')):
            if yaml_path.name == 'manifest.yaml':
                continue
            self._load_bank_file(yaml_path)

    def _load_bank_file(self, path: Path) -> None:
        try:
            with path.open('r', encoding='utf-8') as f:
                raw = yaml.safe_load(f)
        except Exception as exc:
            _logger.warning('failed to parse %s: %s', path, exc)
            return

        if not isinstance(raw, dict) or 'questions' not in raw:
            return

        from ..schemas import QuestionItem  # local import to avoid circular dependency
        for q_raw in raw['questions']:
            try:
                item = QuestionItem.model_validate(q_raw)
            except Exception as exc:
                _logger.warning(
                    'failed to validate question in %s: %s', path.name, exc
                )
                continue
            question = Question(
                item=item,
                bank_file=path,
                data_dir=path.parent,
            )
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
        domain: str | None = None,
        tags: list[str] | None = None,
    ) -> list[Question]:
        """List questions with optional filters."""
        results = list(self._questions.values())
        if capability:
            results = [q for q in results if q.capability == capability]
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
