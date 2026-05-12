"""Report writers for MATTER v5 evaluation."""

from __future__ import annotations

import json
from pathlib import Path

from ..schemas import AxisPassRates, EvalRunRecord, EvaluationSummary


def write_reports(
    *,
    output_dir: Path,
    records: list[EvalRunRecord],
    summary: EvaluationSummary,
    prefix: str = '',
) -> dict[str, str]:
    """Write all report files and return a dict of {label: path}."""
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_runs_path = output_dir / f'{prefix}raw_runs.jsonl'
    by_question_path = output_dir / f'{prefix}scores_by_question.json'
    by_capability_path = output_dir / f'{prefix}scores_by_capability.json'
    by_model_path = output_dir / f'{prefix}scores_by_model.json'
    final_report_path = output_dir / f'{prefix}final_report.md'

    with raw_runs_path.open('w', encoding='utf-8') as handle:
        for record in records:
            handle.write(
                json.dumps(record.model_dump(), ensure_ascii=False, default=str)
            )
            handle.write('\n')

    by_question_path.write_text(
        json.dumps(
            {k: v.model_dump() for k, v in summary.by_question.items()},
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding='utf-8',
    )
    by_capability_path.write_text(
        json.dumps(
            {
                'by_capability': {
                    k: v.model_dump() for k, v in summary.by_capability.items()
                },
                'by_domain': {k: v.model_dump() for k, v in summary.by_domain.items()},
                'by_mode': {k: v.model_dump() for k, v in summary.by_mode.items()},
                'overall': {
                    'total_runs': summary.total_runs,
                    'total_criteria': summary.total_criteria,
                    'total_passed': summary.total_passed,
                    'pass_rate': summary.pass_rate,
                },
                'safety': summary.safety,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding='utf-8',
    )
    by_model_path.write_text(
        json.dumps(
            {k: v.model_dump() for k, v in summary.by_model.items()},
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding='utf-8',
    )
    final_report_path.write_text(_render_markdown(summary), encoding='utf-8')

    return {
        'raw_runs': str(raw_runs_path),
        'scores_by_question': str(by_question_path),
        'scores_by_capability': str(by_capability_path),
        'scores_by_model': str(by_model_path),
        'final_report': str(final_report_path),
    }


def append_raw_run(
    *, output_dir: Path, record: EvalRunRecord, filename: str = 'raw_runs.jsonl'
) -> Path:
    """Append a single EvalRunRecord to the JSONL file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_runs_path = output_dir / filename
    with raw_runs_path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(record.model_dump(), ensure_ascii=False, default=str))
        handle.write('\n')
    return raw_runs_path


def load_records_from_jsonl(path: Path) -> list[EvalRunRecord]:
    """Load EvalRunRecords from a JSONL file, skipping malformed lines."""
    records: list[EvalRunRecord] = []
    if not path.exists():
        raise FileNotFoundError(f'raw runs file not found: {path}')
    with path.open('r', encoding='utf-8') as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
                records.append(EvalRunRecord.model_validate(payload))
            except Exception:  # noqa: BLE001
                continue
    return records


def generate_rating_from_raw_runs(
    *,
    raw_runs_path: Path,
    output_dir: Path | None = None,
    prefix: str = 'interim_',
) -> dict[str, object]:
    """Re-aggregate and re-render reports from an existing JSONL file."""
    from .aggregator import build_summary

    records = load_records_from_jsonl(raw_runs_path)
    if not records:
        raise ValueError(f'no valid records found in {raw_runs_path}')
    summary = build_summary(records)
    target_dir = output_dir or raw_runs_path.parent
    report_paths = write_reports(
        output_dir=target_dir, records=records, summary=summary, prefix=prefix
    )
    return {
        'total_runs': summary.total_runs,
        'pass_rate': summary.pass_rate,
        'report_paths': report_paths,
    }


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def _fmt_pair(pair: tuple[int, int]) -> str:
    passed, total = pair
    if total == 0:
        return '—'
    pct = 100.0 * passed / total
    return f'{passed}/{total} ({pct:.1f}%)'


def _axis_row(label: str, rates: AxisPassRates) -> str:
    return (
        f'| `{label}` '
        f'| {_fmt_pair(rates.correctness)} '
        f'| {_fmt_pair(rates.grounding)} '
        f'| {_fmt_pair(rates.efficiency)} '
        f'| {_fmt_pair(rates.overall)} |'
    )


_AXIS_TABLE_HEADER = (
    '| Group | Correctness | Grounding | Efficiency | Overall |',
    '|-------|-------------|-----------|------------|---------|',
)


def _render_markdown(summary: EvaluationSummary) -> str:
    total_pct = (
        f'{100 * summary.pass_rate:.1f}%' if summary.total_criteria > 0 else 'N/A'
    )
    lines: list[str] = [
        '# MATTER v5 Evaluation Report',
        '',
        f'## Overall: {summary.questions_passed}/{summary.total_runs} questions passed',
        '',
        f'- Total runs: {summary.total_runs}',
        f'- Total criteria evaluated: {summary.total_criteria}',
        f'- Criteria passed: {summary.total_passed} ({total_pct})',
        '',
    ]

    if summary.by_capability:
        lines += ['### By Capability', _AXIS_TABLE_HEADER[0], _AXIS_TABLE_HEADER[1]]
        for cap in sorted(summary.by_capability):
            lines.append(_axis_row(cap, summary.by_capability[cap]))
        lines.append('')

    if summary.by_domain:
        lines += ['### By Domain', _AXIS_TABLE_HEADER[0], _AXIS_TABLE_HEADER[1]]
        for dom in sorted(summary.by_domain):
            lines.append(_axis_row(dom, summary.by_domain[dom]))
        lines.append('')

    if summary.by_mode:
        lines += ['### By Mode', _AXIS_TABLE_HEADER[0], _AXIS_TABLE_HEADER[1]]
        for mode in sorted(summary.by_mode):
            lines.append(_axis_row(mode, summary.by_mode[mode]))
        lines.append('')

    lines += [
        '## Safety',
        f"- Triggered count: {summary.safety.get('triggered_count', 0)}",
        f"- Triggered rate: {summary.safety.get('triggered_rate', 0.0):.3f}",
        f"- Any triggered: {summary.safety.get('any_triggered', False)}",
        '',
    ]

    if summary.by_model:
        lines += [
            '## Model Comparison',
            '| Model | Correctness | Grounding | Efficiency | Overall |',
            '|-------|-------------|-----------|------------|---------|',
        ]
        for model_key in sorted(summary.by_model):
            lines.append(_axis_row(model_key, summary.by_model[model_key]))
        lines.append('')

    lines += [
        '## Per Question (mode split)',
        '| Question:Mode | Capability | Domain | Correctness | Grounding | Efficiency | Overall | Safety Veto |',
        '|---------------|------------|--------|-------------|-----------|------------|---------|-------------|',
    ]
    for key in sorted(summary.by_question):
        row = summary.by_question[key]
        lines.append(
            f'| `{key}` '
            f'| {row.capability} '
            f'| {row.domain} '
            f'| {_fmt_pair(row.correctness)} '
            f'| {_fmt_pair(row.grounding)} '
            f'| {_fmt_pair(row.efficiency)} '
            f'| {_fmt_pair(row.overall)} '
            f'| {row.safety_veto_count} |'
        )
    lines.append('')

    return '\n'.join(lines)
