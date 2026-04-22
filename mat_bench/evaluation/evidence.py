"""Evidence layer for MATTER evaluation.

Defines the standardised evidence format (EvidenceBundle) and the
EvidenceExtractor that converts a raw trajectory JSON file into an
EvidenceBundle.
"""

from __future__ import annotations

import json
import logging
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class EventType(str, Enum):
    STRUCTURE_RETRIEVAL = 'structure_retrieval'
    STRUCTURE_CONSTRUCTION = 'structure_construction'
    CALCULATION_EXECUTION = 'calculation_execution'
    SCRIPT_EXECUTION = 'script_execution'
    FILE_EDITING = 'file_editing'
    VALIDATION = 'validation'
    DATA_ANALYSIS = 'data_analysis'
    OTHER = 'other'


class SourceType(str, Enum):
    DATABASE = 'database'
    SCIENTIFIC_LIBRARY = 'scientific_library'
    MCP_TOOL = 'mcp_tool'
    BASH_SCRIPT = 'bash_script'
    MODEL_ONLY = 'model_only'
    UNKNOWN = 'unknown'


class CallStatus(str, Enum):
    SUCCESS = 'success'
    EMPTY = 'empty'
    FAILED = 'failed'
    TIMEOUT = 'timeout'
    BLOCKED = 'blocked'
    INTERRUPTED = 'interrupted'


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class EventRecord(BaseModel):
    step: int = Field(description='Step index in the trajectory (1-based)')
    event_type: EventType = Field(description='Abstract event category')
    source_type: SourceType = Field(description='Where the result came from')
    succeeded: bool = Field(description='Whether the underlying call succeeded')
    detail: str = Field(default='', description='Short human-readable note')


class ToolCallRecord(BaseModel):
    step: int = Field(description='Step index (1-based)')
    call_index: int = Field(default=0)
    tool_name: str = Field(description='Name of the tool that was called')
    tool_description: str = Field(default='')
    args: dict[str, Any] = Field(default_factory=dict)
    status: CallStatus = Field(default=CallStatus.SUCCESS)
    observation_excerpt: str = Field(default='')


class ArtifactRecord(BaseModel):
    path: str = Field(description='Relative path inside the workspace')
    artifact_type: str = Field(default='unknown')
    size_bytes: int | None = Field(default=None)


class TokenUsage(BaseModel):
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)
    cache_read_tokens: int = Field(default=0)

    def add(self, other: dict[str, int]) -> None:
        self.prompt_tokens += other.get('prompt_tokens', 0)
        self.completion_tokens += other.get('completion_tokens', 0)
        self.total_tokens += other.get('total_tokens', 0)
        self.cache_read_tokens += other.get('cache_read_tokens', 0)

    @classmethod
    def from_usage_dict(cls, raw: dict[str, Any]) -> TokenUsage:
        pt = int(raw.get('prompt_tokens') or 0)
        ct = int(raw.get('completion_tokens') or 0)
        tt = int(raw.get('total_tokens') or 0)
        cr = int(raw.get('cache_read_tokens') or 0)
        if not cr and raw.get('cache_read_input_tokens') is not None:
            try:
                cr = int(raw['cache_read_input_tokens'])
            except (TypeError, ValueError):
                cr = 0
        if pt == 0 and (
            raw.get('input_tokens') is not None
            or raw.get('cache_creation_input_tokens') is not None
            or raw.get('cache_read_input_tokens') is not None
        ):
            try:
                inp = int(raw.get('input_tokens') or 0)
                ccreate = int(raw.get('cache_creation_input_tokens') or 0)
                cr2 = int(raw.get('cache_read_input_tokens') or 0)
            except (TypeError, ValueError):
                inp, ccreate, cr2 = 0, 0, 0
            pt = inp + ccreate + cr2
            cr = cr2 or cr
            if ct == 0 and raw.get('output_tokens') is not None:
                try:
                    ct = int(raw['output_tokens'])
                except (TypeError, ValueError):
                    pass
        if tt == 0 and (pt or ct):
            tt = pt + ct
        return cls(
            prompt_tokens=pt,
            completion_tokens=ct,
            total_tokens=tt,
            cache_read_tokens=cr,
        )

    @property
    def total_tokens_effective(self) -> int:
        if self.cache_read_tokens > 0:
            return self.total_tokens - self.cache_read_tokens
        return self.total_tokens


def approximate_last_turn_usage_from_run_summary(
    usage: dict[str, Any] | None, num_turns: Any
) -> TokenUsage | None:
    if not isinstance(usage, dict) or not usage:
        return None
    try:
        turns = int(num_turns)
    except (TypeError, ValueError):
        return None
    if turns <= 0:
        return None

    whole_run = TokenUsage.from_usage_dict(usage)
    if (
        whole_run.prompt_tokens <= 0
        and whole_run.completion_tokens <= 0
        and whole_run.total_tokens <= 0
        and whole_run.cache_read_tokens <= 0
    ):
        return None

    def _avg(value: int) -> int:
        if value <= 0:
            return 0
        return max(1, round(value / turns))

    prompt_tokens = _avg(whole_run.prompt_tokens)
    completion_tokens = _avg(whole_run.completion_tokens)
    total_tokens = _avg(whole_run.total_tokens)
    cache_read_tokens = _avg(whole_run.cache_read_tokens)
    if total_tokens <= 0 and (prompt_tokens > 0 or completion_tokens > 0):
        total_tokens = prompt_tokens + completion_tokens

    return TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cache_read_tokens=cache_read_tokens,
    )


class EvidenceBundle(BaseModel):
    """Standardised evidence format consumed by the evaluator."""

    task_id: str = Field(description='Task / question ID')
    final_answer: str = Field(default='')
    events: list[EventRecord] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    model_name: str | None = Field(default=None)
    token_usage_last_turn: TokenUsage = Field(default_factory=TokenUsage)
    token_usage_run: TokenUsage = Field(default_factory=TokenUsage)
    total_steps: int = Field(default=0)
    run_status: str = Field(default='unknown')
    duration_ms: int = Field(default=0)
    workspace_dir: str = Field(default='')
    meta: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Evidence Extractor
# ---------------------------------------------------------------------------

_OBSERVATION_EXCERPT_LEN = 500


class EvidenceExtractor:
    """Convert a trajectory JSON file into an EvidenceBundle."""

    def __init__(
        self,
        mapping_path: Path | str | None = None,
        agent_name_filter: str | None = None,
    ) -> None:
        self._mapping_path = Path(mapping_path) if mapping_path else None
        self._agent_name_filter = agent_name_filter
        self._mapping: list[dict[str, Any]] = []
        if self._mapping_path is not None:
            self._load_mapping()

    def extract(
        self,
        trajectory_path: Path | str,
        task_id: str = '',
        final_answer: str = '',
    ) -> EvidenceBundle:
        traj_path = Path(trajectory_path)
        if not traj_path.exists():
            logger.warning('Trajectory file not found: %s', traj_path)
            return EvidenceBundle(task_id=task_id, final_answer=final_answer)

        try:
            raw: list[dict[str, Any]] = json.loads(
                traj_path.read_text(encoding='utf-8')
            )
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning('Failed to read trajectory %s: %s', traj_path, exc)
            return EvidenceBundle(task_id=task_id, final_answer=final_answer)

        return self._build_bundle(raw, task_id=task_id, final_answer=final_answer)

    def _load_mapping(self) -> None:
        if self._mapping_path is None:
            self._mapping = []
            return
        if not self._mapping_path.exists():
            logger.warning(
                'evidence_mapping.yaml not found at %s; event classification will be empty',
                self._mapping_path,
            )
            self._mapping = []
            return
        try:
            data = yaml.safe_load(self._mapping_path.read_text(encoding='utf-8'))
            self._mapping = data.get('mappings', []) if isinstance(data, dict) else []
        except Exception as exc:
            logger.warning('Failed to load evidence_mapping.yaml: %s', exc)
            self._mapping = []

    def _build_bundle(
        self,
        raw: list[dict[str, Any]],
        task_id: str,
        final_answer: str,
    ) -> EvidenceBundle:
        if not task_id and raw:
            traj = raw[0].get('trajectory', {})
            task_id = traj.get('task_id', '')

        tool_desc_map = self._build_tool_description_map(raw)
        model_name = self._extract_model_name(raw)

        best_usage_key: tuple[int, int] = (-1, -1)
        best_usage: dict[str, Any] | None = None
        run_usage = TokenUsage()
        step_serial = 0

        events: list[EventRecord] = []
        tool_calls: list[ToolCallRecord] = []
        total_steps = 0
        run_status = 'unknown'

        for entry in raw:
            traj = entry.get('trajectory', {})
            agent_name = traj.get('agent_name', '')
            if self._agent_name_filter and agent_name != self._agent_name_filter:
                continue

            run_status = entry.get('status', run_status)

            for step_dict in traj.get('steps', []):
                step_id = step_dict.get('step_id', 0)
                total_steps = max(total_steps, step_id)
                step_serial += 1

                asst_msg = step_dict.get('assistant_message', {})
                meta = asst_msg.get('meta', {}) if isinstance(asst_msg, dict) else {}
                usage = meta.get('usage', {})
                if isinstance(usage, dict) and usage:
                    key = (step_id, step_serial)
                    if best_usage is None or key > best_usage_key:
                        best_usage_key = key
                        best_usage = usage
                    tu_step = TokenUsage.from_usage_dict(usage)
                    run_usage.add(
                        {
                            'prompt_tokens': tu_step.prompt_tokens,
                            'completion_tokens': tu_step.completion_tokens,
                            'total_tokens': tu_step.total_tokens,
                            'cache_read_tokens': tu_step.cache_read_tokens,
                        }
                    )

                tool_responses = step_dict.get('tool_responses', [])
                resp_by_id: dict[str, dict[str, Any]] = {}
                for tr in tool_responses:
                    if isinstance(tr, dict):
                        cid = tr.get('tool_call_id', '')
                        if cid:
                            resp_by_id[cid] = tr

                raw_tool_calls = (
                    asst_msg.get('tool_calls', []) if isinstance(asst_msg, dict) else []
                ) or []

                for call_idx, tc in enumerate(raw_tool_calls):
                    if not isinstance(tc, dict):
                        continue
                    func = tc.get('function', {})
                    tool_name = func.get('name', '')
                    if not tool_name:
                        continue

                    args = self._parse_args(func.get('arguments', '{}'))
                    call_id = tc.get('id', '')
                    resp = resp_by_id.get(call_id, {})
                    status = self._parse_call_status(resp)
                    observation_excerpt = self._make_excerpt(resp)
                    tool_description = tool_desc_map.get(tool_name, '')

                    tcr = ToolCallRecord(
                        step=step_id,
                        call_index=call_idx,
                        tool_name=tool_name,
                        tool_description=tool_description,
                        args=args,
                        status=status,
                        observation_excerpt=observation_excerpt,
                    )
                    tool_calls.append(tcr)

                    event = self._map_tool_to_event(
                        tool_name=tool_name, args=args, step=step_id, status=status,
                    )
                    if event:
                        events.append(event)

        last_turn_usage = (
            TokenUsage.from_usage_dict(best_usage)
            if best_usage is not None
            else TokenUsage()
        )

        return EvidenceBundle(
            task_id=task_id,
            final_answer=final_answer,
            events=events,
            tool_calls=tool_calls,
            model_name=model_name,
            token_usage_last_turn=last_turn_usage,
            token_usage_run=run_usage,
            total_steps=total_steps,
            run_status=run_status,
        )

    def _build_tool_description_map(self, raw: list[dict[str, Any]]) -> dict[str, str]:
        desc_map: dict[str, str] = {}
        if not raw:
            return desc_map
        traj = raw[0].get('trajectory', {})
        for dialog in traj.get('dialogs', []):
            if not isinstance(dialog, dict):
                continue
            for tool_spec in dialog.get('tools', []):
                if not isinstance(tool_spec, dict):
                    continue
                func = tool_spec.get('function', {})
                name = func.get('name', '')
                desc = func.get('description', '')
                if name:
                    desc_map[name] = desc
        return desc_map

    def _extract_model_name(self, raw: list[dict[str, Any]]) -> str | None:
        for entry in raw:
            traj = entry.get('trajectory', {})
            meta = traj.get('meta', {})
            if isinstance(meta, dict) and meta.get('model_name'):
                return str(meta['model_name'])
            for step in traj.get('steps', []):
                asst = step.get('assistant_message', {})
                if isinstance(asst, dict):
                    ameta = asst.get('meta', {})
                    model = ameta.get('model')
                    if model:
                        return str(model)
        return None

    def _parse_args(self, raw_args: Any) -> dict[str, Any]:
        if isinstance(raw_args, dict):
            return raw_args
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args)
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
        return {}

    def _parse_call_status(self, response: dict[str, Any]) -> CallStatus:
        if not response:
            return CallStatus.SUCCESS

        content = response.get('content', '')
        meta_info = (response.get('meta') or {}).get('info', {})

        if isinstance(meta_info, dict):
            if 'success' in meta_info:
                if not meta_info['success']:
                    return CallStatus.FAILED
                if not content or (isinstance(content, str) and not content.strip()):
                    return CallStatus.EMPTY
                return CallStatus.SUCCESS

        if isinstance(content, str):
            lower = content.lower()
            if 'blocked' in lower or 'loop detected' in lower or 'guard' in lower:
                return CallStatus.BLOCKED
            if 'timeout' in lower or 'timed out' in lower:
                return CallStatus.TIMEOUT
            if 'interrupted' in lower or 'cancelled' in lower:
                return CallStatus.INTERRUPTED
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    status_str = str(parsed.get('status', '')).lower()
                    if status_str == 'success':
                        result = parsed.get(
                            'result', parsed.get('data', parsed.get('content'))
                        )
                        if result is None or result == '' or result == [] or result == {}:
                            return CallStatus.EMPTY
                        return CallStatus.SUCCESS
                    elif status_str in ('error', 'failed', 'failure'):
                        return CallStatus.FAILED
                    elif status_str == 'timeout':
                        return CallStatus.TIMEOUT
            except (json.JSONDecodeError, ValueError):
                pass
            if not content.strip():
                return CallStatus.EMPTY

        return CallStatus.SUCCESS

    def _make_excerpt(self, response: dict[str, Any]) -> str:
        if not response:
            return ''
        content = response.get('content', '')
        if not isinstance(content, str):
            try:
                content = json.dumps(content, ensure_ascii=False)
            except Exception:
                content = str(content)
        return content[:_OBSERVATION_EXCERPT_LEN]

    def _map_tool_to_event(
        self,
        tool_name: str,
        args: dict[str, Any],
        step: int,
        status: CallStatus,
    ) -> EventRecord | None:
        for rule in self._mapping:
            pattern: str = rule.get('pattern', '')
            if not self._name_matches(tool_name, pattern):
                continue
            when: dict[str, str] = rule.get('when_args_contains', {})
            if when and not self._args_match(args, when):
                continue
            event_type = EventType(rule.get('event_type', EventType.OTHER.value))
            source_type = SourceType(rule.get('source_type', SourceType.UNKNOWN.value))
            return EventRecord(
                step=step,
                event_type=event_type,
                source_type=source_type,
                succeeded=status == CallStatus.SUCCESS,
                detail=rule.get('detail', tool_name),
            )
        return None

    @staticmethod
    def _name_matches(tool_name: str, pattern: str) -> bool:
        if pattern.endswith('*') and pattern.startswith('*'):
            return pattern[1:-1] in tool_name
        if pattern.endswith('*'):
            return tool_name.startswith(pattern[:-1])
        if pattern.startswith('*'):
            return tool_name.endswith(pattern[1:])
        return tool_name == pattern

    @staticmethod
    def _args_match(args: dict[str, Any], when: dict[str, str]) -> bool:
        for key, substring in when.items():
            val = args.get(key, '')
            if substring not in str(val):
                return False
        return True
