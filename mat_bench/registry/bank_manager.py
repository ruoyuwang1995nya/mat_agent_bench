"""Multi-bank question registry management.

Supports hosting several question banks at once:

* **official** banks — read-only, supplied at startup (e.g. the bundled
  ``question_bank/`` directory), loaded from an explicit list of paths.
* **custom** banks — writable, created and populated at runtime (e.g. via
  the HTTP API). Custom banks live under a shared ``banks_root`` directory
  and are rediscovered on every restart, so questions added through the
  API survive a server restart.

Each bank directory uses the same layout as :class:`~.registry.Registry`
(one subdirectory per question, containing a ``question.yaml``). Custom
bank directories additionally carry a ``bank.yaml`` metadata file with the
bank's display name, description, and creation time.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from .registry import Question, Registry

if TYPE_CHECKING:
    from ..schemas import QuestionItem

_logger = logging.getLogger(__name__)

_BANK_META_FILE = 'bank.yaml'
_SLUG_RE = re.compile(r'^[A-Za-z0-9_-]+$')


def _validate_slug(value: str, *, kind: str) -> str:
    """Validate a bank or question id used as a directory name."""
    if not value or not _SLUG_RE.match(value):
        raise ValueError(
            f'invalid {kind} {value!r}: must be a non-empty string of '
            'letters, digits, underscores, and hyphens only'
        )
    return value


def _slugify(name: str) -> str:
    """Derive a directory-safe slug from a human-readable name."""
    slug = re.sub(r'[^A-Za-z0-9_-]+', '_', name.strip()).strip('_-')
    if not slug:
        raise ValueError(f'cannot derive a valid bank id from name {name!r}')
    return slug


def _safe_join(root: Path, relative: str | Path, *, kind: str = 'path') -> Path:
    """Join ``relative`` onto ``root`` and reject any escape from ``root``.

    Resolves symlinks/``..`` segments and verifies the result is still
    contained within ``root`` before returning it, regardless of what
    ``relative`` looks like beforehand.
    """
    root_resolved = root.resolve()
    candidate = (root_resolved / relative).resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise ValueError(f'invalid {kind} {str(relative)!r}: escapes {root}')
    return candidate


@dataclass
class BankMeta:
    """Metadata describing one hosted question bank."""

    bank_id: str
    name: str
    description: str = ''
    source: str = 'custom'  # "official" or "custom"
    created_at: datetime | None = None

    def to_dict(self) -> dict:
        return {
            'bank_id': self.bank_id,
            'name': self.name,
            'description': self.description,
            'source': self.source,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class BankManager:
    """Owns one :class:`Registry` per question bank and coordinates writes.

    Usage::

        manager = BankManager(official_dirs=[Path("question_bank")],
                               banks_root=Path.home() / ".matbench" / "question_banks")
        registry = manager.combined_registry()
        meta = manager.create_bank(name="My custom bank")
        manager.add_question(meta.bank_id, question_item)
    """

    def __init__(
        self,
        official_dirs: list[str | Path] | None = None,
        banks_root: str | Path | None = None,
    ) -> None:
        self._banks_root = Path(banks_root).resolve() if banks_root else None
        if self._banks_root is not None:
            self._banks_root.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._registries: dict[str, Registry] = {}
        self._meta: dict[str, BankMeta] = {}

        for raw_dir in official_dirs or []:
            self._load_official_bank(Path(raw_dir))
        if self._banks_root is not None:
            self._discover_custom_banks()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load_official_bank(self, path: Path) -> None:
        bank_id = path.resolve().name
        if bank_id in self._registries:
            _logger.warning('duplicate bank id %r from %s; skipping', bank_id, path)
            return
        registry = Registry(path, bank_id=bank_id)
        self._registries[bank_id] = registry
        self._meta[bank_id] = BankMeta(bank_id=bank_id, name=bank_id, source='official')

    def _discover_custom_banks(self) -> None:
        assert self._banks_root is not None
        for bank_dir in sorted(p for p in self._banks_root.iterdir() if p.is_dir()):
            bank_id = bank_dir.name
            if bank_id in self._registries:
                _logger.warning(
                    'custom bank id %r collides with an official bank; skipping %s',
                    bank_id, bank_dir,
                )
                continue
            self._load_custom_bank(bank_id, bank_dir)

    def _load_custom_bank(self, bank_id: str, bank_dir: Path) -> None:
        meta = self._read_bank_meta(bank_id, bank_dir)
        registry = Registry(bank_dir, bank_id=bank_id)
        self._registries[bank_id] = registry
        self._meta[bank_id] = meta

    @staticmethod
    def _read_bank_meta(bank_id: str, bank_dir: Path) -> BankMeta:
        meta_path = bank_dir / _BANK_META_FILE
        if not meta_path.is_file():
            return BankMeta(bank_id=bank_id, name=bank_id, source='custom')
        try:
            with meta_path.open('r', encoding='utf-8') as f:
                raw = yaml.safe_load(f) or {}
        except Exception as exc:
            _logger.warning('failed to parse %s: %s', meta_path, exc)
            return BankMeta(bank_id=bank_id, name=bank_id, source='custom')
        created_raw = raw.get('created_at')
        created_at = datetime.fromisoformat(created_raw) if created_raw else None
        return BankMeta(
            bank_id=bank_id,
            name=str(raw.get('name', bank_id)),
            description=str(raw.get('description', '')),
            source='custom',
            created_at=created_at,
        )

    @staticmethod
    def _write_bank_meta(bank_dir: Path, meta: BankMeta) -> None:
        meta_path = bank_dir / _BANK_META_FILE
        data = {
            'name': meta.name,
            'description': meta.description,
            'created_at': meta.created_at.isoformat() if meta.created_at else None,
        }
        with meta_path.open('w', encoding='utf-8') as f:
            yaml.safe_dump(data, f, sort_keys=False)

    # ------------------------------------------------------------------
    # Read access
    # ------------------------------------------------------------------

    def combined_registry(self) -> Registry:
        """Return a merged read-only view of all hosted banks."""
        with self._lock:
            registries = list(self._registries.values())
        return Registry.merge(registries)

    def list_banks(self) -> list[dict]:
        """Return metadata + question counts for every hosted bank."""
        with self._lock:
            items = [
                (meta, len(self._registries[bank_id]))
                for bank_id, meta in self._meta.items()
            ]
        return [
            {**meta.to_dict(), 'question_count': count}
            for meta, count in sorted(items, key=lambda pair: pair[0].bank_id)
        ]

    def get_bank_meta(self, bank_id: str) -> BankMeta:
        with self._lock:
            if bank_id not in self._meta:
                raise KeyError(f'question bank {bank_id!r} not found')
            return self._meta[bank_id]

    def get_registry(self, bank_id: str) -> Registry:
        with self._lock:
            if bank_id not in self._registries:
                raise KeyError(f'question bank {bank_id!r} not found')
            return self._registries[bank_id]

    # ------------------------------------------------------------------
    # Write access — custom banks only
    # ------------------------------------------------------------------

    def create_bank(
        self,
        name: str,
        description: str = '',
        bank_id: str | None = None,
    ) -> BankMeta:
        """Create a new, empty custom question bank."""
        if self._banks_root is None:
            raise RuntimeError('custom question banks are not enabled on this server')
        if not name or not name.strip():
            raise ValueError('bank name must not be empty')

        resolved_id = _validate_slug(bank_id, kind='bank id') if bank_id else _slugify(name)
        with self._lock:
            if resolved_id in self._registries:
                raise ValueError(f'question bank {resolved_id!r} already exists')

            bank_dir = _safe_join(self._banks_root, resolved_id, kind='bank id')
            bank_dir.mkdir(parents=True, exist_ok=False)
            meta = BankMeta(
                bank_id=resolved_id,
                name=name.strip(),
                description=description.strip(),
                source='custom',
                created_at=datetime.now(timezone.utc),
            )
            self._write_bank_meta(bank_dir, meta)
            self._registries[resolved_id] = Registry(bank_dir, bank_id=resolved_id)
            self._meta[resolved_id] = meta
        return meta

    def add_question(
        self,
        bank_id: str,
        question_item: QuestionItem,
        data_files: dict[str, bytes] | None = None,
    ) -> Question:
        """Validate and persist a new question inside a custom bank.

        ``data_files`` maps a relative filename (as referenced by
        ``question_item.data_files[*].path``) to its raw bytes.
        """
        with self._lock:
            if bank_id not in self._registries:
                raise KeyError(f'question bank {bank_id!r} not found')
            meta = self._meta[bank_id]
            if meta.source != 'custom':
                raise PermissionError(
                    f'question bank {bank_id!r} is read-only (source={meta.source!r})'
                )

            question_id = _validate_slug(question_item.id, kind='question id')
            for registry in self._registries.values():
                if question_id in registry:
                    raise ValueError(f'question id {question_id!r} already exists')

            assert self._banks_root is not None
            bank_dir = _safe_join(self._banks_root, bank_id, kind='bank id')
            question_dir = _safe_join(bank_dir, question_id, kind='question id')
            question_dir.mkdir(parents=True, exist_ok=False)

            try:
                self._write_question_files(question_dir, question_item, data_files or {})
                self._registries[bank_id] = Registry(bank_dir, bank_id=bank_id)
            except Exception:
                _rmtree(question_dir)
                raise

            return self._registries[bank_id].get_question(question_id)

    @staticmethod
    def _write_question_files(
        question_dir: Path,
        question_item: QuestionItem,
        data_files: dict[str, bytes],
    ) -> None:
        question_yaml = question_dir / 'question.yaml'
        data = question_item.model_dump(mode='json', exclude_none=True)
        with question_yaml.open('w', encoding='utf-8') as f:
            yaml.safe_dump(data, f, sort_keys=False)

        declared = {df.path for df in question_item.data_files}
        for rel_path, content in data_files.items():
            safe_path = _safe_relative_path(rel_path)
            destination = _safe_join(question_dir, safe_path, kind='data file path')
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        missing = declared - set(data_files.keys())
        if missing:
            raise ValueError(f'missing uploaded data file(s): {sorted(missing)}')


def _safe_relative_path(name: str) -> Path:
    """Reject absolute paths and path traversal in an uploaded filename."""
    if not name or '\\' in name:
        raise ValueError(f'invalid data file path: {name!r}')
    path = Path(name)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError(f'unsafe data file path: {name!r}')
    return path


def _rmtree(path: Path) -> None:
    """Best-effort recursive delete used to roll back a partial write."""
    import shutil

    try:
        shutil.rmtree(path)
    except OSError as exc:
        _logger.warning('failed to clean up %s: %s', path, exc)
