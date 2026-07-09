"""CLI entry point for mat-bench.

Subcommands:
    mat-bench run           Run benchmark with an agent script
    mat-bench serve         Start benchmark API and web UI together on a single port
    mat-bench list          List questions in the question bank
    mat-bench grade         Grade a JSONL submission file
    mat-bench report        Re-generate reports from existing JSONL
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _add_question_config_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        '--question-config',
        type=str,
        default=None,
        metavar='FILE',
        help=(
            'YAML file with enabled/disabled question settings. If omitted, '
            '~/.matbench/questions.yaml is used when present.'
        ),
    )


def _apply_question_config_or_exit(registry, path: str | None):
    from pydantic import ValidationError

    from .registry.question_config import load_and_apply_question_config

    try:
        return load_and_apply_question_config(registry, path)
    except (OSError, ValueError, KeyError, ValidationError) as exc:
        print(f'error: failed to apply question config: {exc}', file=sys.stderr)
        sys.exit(1)


def _add_run_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        'run',
        help='Run benchmark with an agent script',
        description=(
            'Run mat-agent-bench with any agent. '
            'The agent is a shell script invoked per-question.'
        ),
    )
    p.add_argument(
        '--agent',
        type=str,
        required=True,
        help='Path to agent shell script (e.g. agents/claude_code.sh).',
    )
    p.add_argument(
        '--agent-env',
        nargs='*',
        metavar='KEY=VALUE',
        help='Extra environment variables passed to the agent script.',
    )
    p.add_argument(
        '--question-bank-dir',
        type=str,
        default='question_bank',
        help='Path to question_bank directory (default: question_bank).',
    )
    _add_question_config_arg(p)
    p.add_argument(
        '--questions',
        nargs='*',
        help='Specific question ID(s) to run.',
    )
    p.add_argument(
        '--capability',
        type=str,
        default=None,
        help='Filter by capability (e.g. input_generation).',
    )
    p.add_argument(
        '--domain',
        type=str,
        default=None,
        help='Filter by domain (e.g. agnostic, battery).',
    )
    p.add_argument(
        '--limit',
        type=int,
        default=None,
        help='Run only the first N questions.',
    )
    p.add_argument(
        '--output-dir',
        type=str,
        default=None,
        help='Output directory (default: runs/<agent>_<timestamp>).',
    )
    p.add_argument(
        '--timeout',
        type=int,
        default=600,
        help='Timeout in seconds per question (default: 600).',
    )
    p.add_argument(
        '--mode',
        type=str,
        default='direct',
        choices=['direct', 'planner'],
        help='Evaluation mode (default: direct).',
    )
    p.add_argument(
        '--llm-judge',
        type=str,
        default=os.environ.get('MAT_BENCH_LLM_JUDGE'),
        metavar='PROVIDER/MODEL',
        help=(
            "LLM judge for llm_binary_judge criteria, e.g. "
            "'anthropic/claude-sonnet-4-20250514'. "
            "Falls back to MAT_BENCH_LLM_JUDGE env var."
        ),
    )
    p.add_argument(
        '--skip-grading',
        action='store_true',
        help='Skip grading — just run and output pre-grading JSONL.',
    )
    p.add_argument(
        '--report',
        action='store_true',
        help='Generate full reports after grading.',
    )
    p.add_argument(
        '-j',
        '--jobs',
        type=int,
        default=1,
        help='Concurrent tasks (default: 1).',
    )
    p.set_defaults(func=_cmd_run)


def _cmd_run(args: argparse.Namespace) -> None:
    from .harness import run_benchmark

    rc = run_benchmark(args)
    sys.exit(rc)


def _load_env_file(path: str) -> None:
    """Load KEY=VALUE pairs from a .env file into os.environ (no override)."""
    env_path = Path(path)
    if not env_path.is_file():
        return
    with env_path.open(encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _add_serve_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        'serve',
        help='Start benchmark API and web UI together on a single port',
        description=(
            'Launch both the benchmark API and the web UI in one process on a '
            'single port. The bench API is mounted at /bench, the UI at /. '
        ),
    )
    p.add_argument(
        '--host',
        type=str,
        default='0.0.0.0',
        help='Host to bind to (default: 0.0.0.0).',
    )
    p.add_argument(
        '--port',
        type=int,
        default=8080,
        help='Port to listen on (default: 8080).',
    )
    p.add_argument(
        '--question-bank-dir',
        type=str,
        default=None,
        help='Path to question_bank directory (default: question_bank next to the package).',
    )
    _add_question_config_arg(p)
    p.add_argument(
        '--questions',
        nargs='*',
        default=None,
        metavar='ID',
        help='Specific question ID(s) to host.',
    )
    p.add_argument(
        '--capability',
        type=str,
        default=None,
        help='Only host questions with this capability.',
    )
    p.add_argument(
        '--task-type',
        type=str,
        default=None,
        dest='task_type',
        help='Only host questions with this task type.',
    )
    p.add_argument(
        '--domain',
        type=str,
        default=None,
        help='Only host questions from this domain.',
    )
    p.add_argument(
        '--tags',
        type=str,
        nargs='*',
        default=None,
        help='Only host questions containing all selected tags.',
    )
    p.add_argument(
        '--exclude-questions',
        nargs='*',
        default=None,
        metavar='ID',
        help='Exclude specific question ID(s) from the hosted set.',
    )
    p.add_argument(
        '--exclude-capability',
        type=str,
        default=None,
        help='Exclude questions with this capability.',
    )
    p.add_argument(
        '--exclude-task-type',
        type=str,
        default=None,
        dest='exclude_task_type',
        help='Exclude questions with this task type.',
    )
    p.add_argument(
        '--exclude-domain',
        type=str,
        default=None,
        help='Exclude questions from this domain.',
    )
    p.add_argument(
        '--exclude-tags',
        type=str,
        nargs='*',
        default=None,
        help='Exclude questions containing any selected tag.',
    )
    p.add_argument(
        '--limit',
        type=int,
        default=None,
        help='Host only the first N selected questions after filtering.',
    )
    p.add_argument(
        '--store-dir',
        type=str,
        default=None,
        metavar='DIR',
        help='mat-bench store directory for data (default: ~/.matbench).',
    )
    p.add_argument(
        '--llm-judge',
        type=str,
        default=os.environ.get('MAT_BENCH_LLM_JUDGE'),
        metavar='PROVIDER/MODEL',
        help=(
            "LLM judge for llm_binary_judge criteria, e.g. "
            "'anthropic/claude-sonnet-4-20250514'. "
            "Falls back to MAT_BENCH_LLM_JUDGE env var."
        ),
    )
    p.add_argument(
        '--env-file',
        type=str,
        default='.env',
        metavar='FILE',
        help='Path to .env file for LLM config (default: .env if it exists).',
    )
    p.add_argument(
        '--grading-workers',
        type=int,
        default=4,
        help='Number of parallel grading threads (default: 4).',
    )
    p.add_argument(
        '--parallel-checklist-workers',
        type=int,
        default=1,
        metavar='N',
        help='Parallel LLM judge calls per question checklist (default: 1).',
    )
    p.add_argument(
        '--log-level',
        type=str,
        default='info',
        choices=['debug', 'info', 'warning', 'error', 'critical'],
        help='Uvicorn log level (default: info).',
    )
    p.set_defaults(func=_cmd_serve)


def _cmd_serve(args: argparse.Namespace) -> None:
    _load_env_file(args.env_file)
    try:
        import uvicorn
    except ImportError:
        print(
            'error: uvicorn is required for mat-bench serve.\n'
            'Install it with: pip install "mat-bench[server]"',
            file=sys.stderr,
        )
        sys.exit(1)

    from .registry import Registry
    from .harness import parse_llm_judge_config
    from .ui.app import create_app
    from datetime import datetime

    _REPO_ROOT = Path(__file__).resolve().parent.parent

    qb_dir = Path(args.question_bank_dir) if args.question_bank_dir else _REPO_ROOT / "question_bank"
    if not qb_dir.is_absolute():
        qb_dir = _REPO_ROOT / qb_dir

    registry = Registry(qb_dir)
    registry, config_path = _apply_question_config_or_exit(registry, args.question_config)
    if config_path is not None:
        print(f'Applied question config: {config_path}', file=sys.stderr)
    if args.limit is not None and args.limit < 0:
        print('error: --limit must be non-negative', file=sys.stderr)
        sys.exit(1)
    try:
        registry = registry.filtered(
            question_ids=args.questions,
            capability=args.capability,
            task_type=args.task_type,
            domain=args.domain,
            tags=args.tags,
            exclude_question_ids=args.exclude_questions,
            exclude_capability=args.exclude_capability,
            exclude_task_type=args.exclude_task_type,
            exclude_domain=args.exclude_domain,
            exclude_tags=args.exclude_tags,
            limit=args.limit,
        )
    except KeyError as exc:
        print(f'error: {exc}', file=sys.stderr)
        sys.exit(1)
    if len(registry) == 0:
        print('error: no questions selected to host', file=sys.stderr)
        sys.exit(1)
    print(f'Loaded {len(registry)} hosted questions from {qb_dir}', file=sys.stderr)

    store_dir = Path(args.store_dir) if args.store_dir else Path.home() / ".matbench"
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_dir = store_dir / 'runs' / f'serve_{timestamp}'

    llm_cfg = None
    has_env = os.environ.get('MAT_BENCH_LLM_MODEL') and os.environ.get('MAT_BENCH_LLM_API_KEY')
    if args.llm_judge or has_env:
        try:
            llm_cfg = parse_llm_judge_config(args.llm_judge or None)
        except ValueError as exc:
            print(f'error: {exc}', file=sys.stderr)
            sys.exit(1)

    app = create_app(
        question_bank_dir=qb_dir,
        store_dir=store_dir,
        registry=registry,
        llm_cfg=llm_cfg,
        grading_workers=args.grading_workers,
        output_dir=output_dir,
        parallel_checklist_workers=args.parallel_checklist_workers,
    )

    print(f'Starting mat-bench at http://{args.host}:{args.port}', file=sys.stderr)
    print(f'  UI:       http://{args.host}:{args.port}/', file=sys.stderr)
    print(f'  Guide:    http://{args.host}:{args.port}/guide', file=sys.stderr)
    print(f'  Bench API: http://{args.host}:{args.port}/bench', file=sys.stderr)
    log_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "fmt": "%(asctime)s %(levelprefix)s %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
                "use_colors": None,
            },
            "access": {
                "()": "uvicorn.logging.AccessFormatter",
                "fmt": '%(asctime)s %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
            },
            "access": {
                "formatter": "access",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": args.log_level.upper(), "propagate": False},
            "uvicorn.error": {"handlers": ["default"], "level": args.log_level.upper(), "propagate": False},
            "uvicorn.access": {"handlers": ["access"], "level": "DEBUG", "propagate": False},
            "mat_bench": {"handlers": ["default"], "level": "INFO", "propagate": False},
        },
    }
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level, log_config=log_config)


def _add_list_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser('list', help='List questions in the question bank')
    p.add_argument(
        '--question-bank-dir',
        type=str,
        default='question_bank',
        help='Path to question_bank directory (default: question_bank)',
    )
    _add_question_config_arg(p)
    p.add_argument('--capability', type=str, default=None, help='Filter by capability')
    p.add_argument('--domain', type=str, default=None, help='Filter by domain')
    p.add_argument('--tags', type=str, nargs='*', default=None, help='Filter by tags')
    p.set_defaults(func=_cmd_list)


def _add_questions_parser(subparsers: argparse._SubParsersAction) -> None:
    from .registry.question_config import default_question_config_path

    p = subparsers.add_parser('questions', help='Manage enabled/disabled questions')
    p.add_argument(
        '--question-bank-dir',
        type=str,
        default='question_bank',
        help='Path to question_bank directory (default: question_bank)',
    )
    p.add_argument(
        '--question-config',
        type=str,
        default=str(default_question_config_path()),
        metavar='FILE',
        help='Question config file to edit (default: ~/.matbench/questions.yaml).',
    )
    question_subparsers = p.add_subparsers(dest='questions_command')

    list_p = question_subparsers.add_parser('list', help='List enabled questions')
    list_p.set_defaults(func=_cmd_questions_list)

    validate_p = question_subparsers.add_parser('validate', help='Validate question config')
    validate_p.set_defaults(func=_cmd_questions_validate)

    enable_p = question_subparsers.add_parser('enable', help='Enable question ID(s)')
    enable_p.add_argument('question_ids', nargs='+', metavar='ID')
    enable_p.set_defaults(func=_cmd_questions_enable)

    disable_p = question_subparsers.add_parser('disable', help='Disable question ID(s)')
    disable_p.add_argument('question_ids', nargs='+', metavar='ID')
    disable_p.set_defaults(func=_cmd_questions_disable)

    set_p = question_subparsers.add_parser(
        'set-enabled',
        help='Replace the enabled-question allowlist with the given ID(s)',
    )
    set_p.add_argument('question_ids', nargs='+', metavar='ID')
    set_p.set_defaults(func=_cmd_questions_set_enabled)

    clear_p = question_subparsers.add_parser(
        'clear-enabled',
        help='Remove the enabled-question allowlist so all non-disabled questions are enabled',
    )
    clear_p.set_defaults(func=_cmd_questions_clear_enabled)


def _add_grade_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser('grade', help='Grade a JSONL submission file')
    p.add_argument('raw_runs', type=str, help='Path to raw_runs.jsonl file')
    p.add_argument(
        '--output-dir',
        type=str,
        default=None,
        help='Output directory for reports (default: same as input)',
    )
    p.add_argument('--prefix', type=str, default='', help='Prefix for output files')
    p.set_defaults(func=_cmd_grade)


def _add_report_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser('report', help='Re-generate reports from existing JSONL')
    p.add_argument('raw_runs', type=str, help='Path to raw_runs.jsonl file')
    p.add_argument(
        '--output-dir',
        type=str,
        default=None,
        help='Output directory for reports (default: same as input)',
    )
    p.add_argument(
        '--prefix', type=str, default='interim_', help='Prefix for output files'
    )
    p.set_defaults(func=_cmd_report)


def _cmd_list(args: argparse.Namespace) -> None:
    from .registry import Registry

    registry = Registry(args.question_bank_dir)
    registry, config_path = _apply_question_config_or_exit(registry, args.question_config)
    questions = registry.list_questions(
        capability=args.capability,
        domain=args.domain,
        tags=args.tags,
    )

    print(f'Found {len(questions)} questions in {args.question_bank_dir}')
    if config_path is not None:
        print(f'Config: {config_path}')
    print()
    for q in questions:
        tags_str = ', '.join(q.tags[:3])
        capabilities = ','.join(q.capability)
        print(f'  {q.id:<30s} {capabilities:<25s} {q.domain:<15s} {tags_str}')


def _load_question_config_for_edit(args: argparse.Namespace):
    from pydantic import ValidationError

    from .registry.question_config import load_question_config_or_default

    try:
        return load_question_config_or_default(args.question_config)
    except (OSError, ValueError, ValidationError) as exc:
        print(f'error: failed to load question config: {exc}', file=sys.stderr)
        sys.exit(1)


def _load_registry_for_questions(args: argparse.Namespace):
    from .registry import Registry

    try:
        return Registry(args.question_bank_dir)
    except FileNotFoundError as exc:
        print(f'error: {exc}', file=sys.stderr)
        sys.exit(1)


def _validate_question_ids_or_exit(registry, question_ids: list[str]) -> None:
    try:
        for question_id in question_ids:
            registry.get_question(question_id)
    except KeyError as exc:
        print(f'error: {exc}', file=sys.stderr)
        sys.exit(1)


def _save_question_config_or_exit(path: str, config) -> None:
    from .registry.question_config import save_question_config

    try:
        save_question_config(path, config)
    except OSError as exc:
        print(f'error: failed to save question config: {exc}', file=sys.stderr)
        sys.exit(1)


def _cmd_questions_list(args: argparse.Namespace) -> None:
    from .registry.question_config import apply_question_config

    registry = _load_registry_for_questions(args)
    config = _load_question_config_for_edit(args)
    registry = apply_question_config(registry, config)
    questions = registry.list_questions()
    print(f'Enabled questions: {len(questions)}')
    print(f'Config: {args.question_config}')
    print()
    for q in questions:
        print(q.id)


def _cmd_questions_validate(args: argparse.Namespace) -> None:
    from .registry.question_config import apply_question_config

    registry = _load_registry_for_questions(args)
    config = _load_question_config_for_edit(args)
    registry = apply_question_config(registry, config)
    print(f'Config OK: {args.question_config}')
    print(f'Enabled questions: {len(registry)}')


def _cmd_questions_enable(args: argparse.Namespace) -> None:
    from .registry.question_config import enable_questions

    registry = _load_registry_for_questions(args)
    _validate_question_ids_or_exit(registry, args.question_ids)
    config = _load_question_config_for_edit(args)
    enable_questions(config, args.question_ids)
    _save_question_config_or_exit(args.question_config, config)
    print(f'Enabled {len(args.question_ids)} question(s) in {args.question_config}')


def _cmd_questions_disable(args: argparse.Namespace) -> None:
    from .registry.question_config import disable_questions

    registry = _load_registry_for_questions(args)
    _validate_question_ids_or_exit(registry, args.question_ids)
    config = _load_question_config_for_edit(args)
    disable_questions(config, args.question_ids)
    _save_question_config_or_exit(args.question_config, config)
    print(f'Disabled {len(args.question_ids)} question(s) in {args.question_config}')


def _cmd_questions_set_enabled(args: argparse.Namespace) -> None:
    from .registry.question_config import QuestionSelectionConfig

    registry = _load_registry_for_questions(args)
    _validate_question_ids_or_exit(registry, args.question_ids)
    config = _load_question_config_for_edit(args)
    config.enabled_questions = list(dict.fromkeys(args.question_ids))
    config.disabled_questions = [
        qid for qid in config.disabled_questions if qid not in set(config.enabled_questions)
    ]
    QuestionSelectionConfig.model_validate(config.model_dump())
    _save_question_config_or_exit(args.question_config, config)
    print(f'Set {len(config.enabled_questions)} enabled question(s) in {args.question_config}')


def _cmd_questions_clear_enabled(args: argparse.Namespace) -> None:
    config = _load_question_config_for_edit(args)
    config.enabled_questions = None
    _save_question_config_or_exit(args.question_config, config)
    print(f'Cleared enabled-question allowlist in {args.question_config}')


def _cmd_grade(args: argparse.Namespace) -> None:
    from .evaluation.grade import grade_run

    raw_runs_path = Path(args.raw_runs)
    output_dir = Path(args.output_dir) if args.output_dir else None

    report = grade_run(
        raw_runs_path=raw_runs_path,
        output_dir=output_dir,
        prefix=args.prefix,
    )

    print(f'Graded {report.total_questions} runs')
    print(f'  Final score: {report.total_passed} questions passed (out of {report.total_questions})')
    print(f'  Pass rate: {report.pass_rate:.1%}')
    print(f'  Weighted pass rate: {report.weighted_pass_rate:.3f}')
    print()
    print('Reports written:')
    for label, path in report.report_paths.items():
        print(f'  {label}: {path}')


def _cmd_report(args: argparse.Namespace) -> None:
    from .reporting.reporter import generate_rating_from_raw_runs

    raw_runs_path = Path(args.raw_runs)
    output_dir = Path(args.output_dir) if args.output_dir else None

    result = generate_rating_from_raw_runs(
        raw_runs_path=raw_runs_path,
        output_dir=output_dir,
        prefix=args.prefix,
    )

    print(f"Total runs: {result['total_runs']}")
    print(f"Pass rate: {result['pass_rate']:.3f}")
    print()
    print('Reports written:')
    for label, path in result.get('report_paths', {}).items():  # type: ignore[union-attr]
        print(f'  {label}: {path}')


def main() -> None:
    parser = argparse.ArgumentParser(
        prog='mat-bench',
        description='MATTER v5 benchmark evaluation toolkit',
    )
    subparsers = parser.add_subparsers(dest='command')

    _add_run_parser(subparsers)
    _add_serve_parser(subparsers)
    _add_list_parser(subparsers)
    _add_questions_parser(subparsers)
    _add_grade_parser(subparsers)
    _add_report_parser(subparsers)

    args = parser.parse_args()
    if not args.command or (args.command == 'questions' and not args.questions_command):
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == '__main__':
    main()
