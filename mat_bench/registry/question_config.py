"""Question enablement configuration for registry views."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .registry import Registry


DEFAULT_QUESTION_CONFIG_DIR = '.matbench'
DEFAULT_QUESTION_CONFIG = 'questions.yaml'


class QuestionFilterConfig(BaseModel):
    """Include/exclude filters stored in a question config file."""

    model_config = ConfigDict(extra='forbid')

    capability: str | None = None
    task_type: str | None = None
    domain: str | None = None
    tags: list[str] | None = None


class QuestionSelectionConfig(BaseModel):
    """Persistent question enablement settings."""

    model_config = ConfigDict(extra='forbid')

    enabled_questions: list[str] | None = None
    disabled_questions: list[str] = Field(default_factory=list)
    include: QuestionFilterConfig = Field(default_factory=QuestionFilterConfig)
    exclude: QuestionFilterConfig = Field(default_factory=QuestionFilterConfig)
    limit: int | None = None


def default_question_config_path() -> Path:
    """Return the conventional per-user question config path."""
    return Path.home() / DEFAULT_QUESTION_CONFIG_DIR / DEFAULT_QUESTION_CONFIG


def resolve_question_config_path(path: str | Path | None) -> Path | None:
    """Resolve an explicit config path or the default file if it exists."""
    if path is not None:
        return Path(path).expanduser().resolve()
    default_path = default_question_config_path()
    return default_path.resolve() if default_path.is_file() else None


def load_question_config(path: str | Path) -> QuestionSelectionConfig:
    """Load a question selection config from YAML."""
    config_path = Path(path).expanduser()
    with config_path.open('r', encoding='utf-8') as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ValueError(f'question config must contain a YAML mapping: {config_path}')
    config = QuestionSelectionConfig.model_validate(raw)
    if config.limit is not None and config.limit < 0:
        raise ValueError('question config limit must be non-negative')
    return config


def save_question_config(path: str | Path, config: QuestionSelectionConfig) -> None:
    """Write a question selection config to YAML."""
    config_path = Path(path).expanduser()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(mode='json', exclude_none=True)
    with config_path.open('w', encoding='utf-8') as f:
        yaml.safe_dump(data, f, sort_keys=False)


def apply_question_config(
    registry: Registry,
    config: QuestionSelectionConfig,
) -> Registry:
    """Return a registry view after applying persistent enablement config."""
    return registry.filtered(
        question_ids=config.enabled_questions,
        capability=config.include.capability,
        task_type=config.include.task_type,
        domain=config.include.domain,
        tags=config.include.tags,
        exclude_question_ids=config.disabled_questions,
        exclude_capability=config.exclude.capability,
        exclude_task_type=config.exclude.task_type,
        exclude_domain=config.exclude.domain,
        exclude_tags=config.exclude.tags,
        limit=config.limit,
    )


def load_and_apply_question_config(
    registry: Registry,
    path: str | Path | None,
) -> tuple[Registry, Path | None]:
    """Apply an explicit or default config file if one is available."""
    config_path = resolve_question_config_path(path)
    if config_path is None:
        return registry, None
    config = load_question_config(config_path)
    return apply_question_config(registry, config), config_path


def load_question_config_or_default(path: str | Path) -> QuestionSelectionConfig:
    """Load an existing config or return an empty config for mutation commands."""
    config_path = Path(path).expanduser()
    if not config_path.is_file():
        return QuestionSelectionConfig()
    return load_question_config(config_path)


def enable_questions(config: QuestionSelectionConfig, question_ids: list[str]) -> None:
    """Mark questions enabled in config, removing them from disabled list."""
    disabled = [qid for qid in config.disabled_questions if qid not in set(question_ids)]
    config.disabled_questions = disabled
    if config.enabled_questions is not None:
        enabled = list(dict.fromkeys([*config.enabled_questions, *question_ids]))
        config.enabled_questions = enabled


def disable_questions(config: QuestionSelectionConfig, question_ids: list[str]) -> None:
    """Mark questions disabled in config."""
    disabled = list(dict.fromkeys([*config.disabled_questions, *question_ids]))
    config.disabled_questions = disabled
    if config.enabled_questions is not None:
        config.enabled_questions = [qid for qid in config.enabled_questions if qid not in set(question_ids)]