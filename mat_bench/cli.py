"""CLI entry point for mat-bench.

Subcommands:
    mat-bench run           Run benchmark with an agent script
    mat-bench list          List questions in the question bank
    mat-bench grade         Grade a JSONL submission file
    mat-bench report        Re-generate reports from existing JSONL
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


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


def _add_list_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser('list', help='List questions in the question bank')
    p.add_argument(
        '--question-bank-dir',
        type=str,
        default='question_bank',
        help='Path to question_bank directory (default: question_bank)',
    )
    p.add_argument('--capability', type=str, default=None, help='Filter by capability')
    p.add_argument('--domain', type=str, default=None, help='Filter by domain')
    p.add_argument('--tags', type=str, nargs='*', default=None, help='Filter by tags')
    p.set_defaults(func=_cmd_list)


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
    questions = registry.list_questions(
        capability=args.capability,
        domain=args.domain,
        tags=args.tags,
    )

    print(f'Found {len(questions)} questions in {args.question_bank_dir}')
    print()
    for q in questions:
        tags_str = ', '.join(q.tags[:3])
        print(f'  {q.id:<30s} {q.capability:<25s} {q.domain:<15s} {tags_str}')


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
    print(f'  Passed: {report.total_passed}/{report.total_questions}')
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
    _add_list_parser(subparsers)
    _add_grade_parser(subparsers)
    _add_report_parser(subparsers)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == '__main__':
    main()
