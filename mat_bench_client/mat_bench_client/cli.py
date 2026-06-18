"""Standalone client CLI for mat-bench servers.

The API is always mounted at {server}/bench. Pass the bare server URL
(e.g. http://host:8080) and all requests go to /bench/... automatically.

Usage:
    mat-bench-client setup        Save token and create a session
    mat-bench-client session      Create a new session (reuse saved token)
    mat-bench-client status       Show current token, session, and server from config
    mat-bench-client questions    List available questions
    mat-bench-client question     Fetch a single question (prompt + data files)
    mat-bench-client data         Download a question data file
    mat-bench-client submit       Submit an answer for grading
    mat-bench-client result       Get grading result for one question
    mat-bench-client results      Get aggregate results summary
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import uuid
from pathlib import Path
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

import yaml


# ---------------------------------------------------------------------------
# Config  (~/.mat-bench-client/config.yaml)
# ---------------------------------------------------------------------------

_DEFAULT_SERVER = 'http://localhost:8080'


def _config_path() -> Path:
    return Path(os.environ.get('MATBENCH_HOME', Path.home() / '.mat-bench-client')) / 'config.yaml'


def _load_config() -> dict:
    p = _config_path()
    if p.is_file():
        try:
            return yaml.safe_load(p.read_text(encoding='utf-8')) or {}
        except (yaml.YAMLError, OSError):
            pass
    return {}


def _save_config(config: dict) -> None:
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.dump(config, default_flow_style=False), encoding='utf-8')


def _resolve_server(args_server: str | None) -> str:
    """Return bare server URL (without /bench suffix)."""
    if args_server:
        return args_server.rstrip('/')
    env = os.environ.get('MAT_BENCH_SERVER_URL')
    if env:
        return env.rstrip('/')
    cfg = _load_config()
    return cfg.get('server_url', _DEFAULT_SERVER).rstrip('/')


def _api(server: str) -> str:
    """Return the API base: {server}/bench."""
    return f'{server}/bench'


def _require_credentials(server: str) -> tuple[str, str]:
    """Return (token, session_id) from env vars or saved config."""
    cfg = _load_config()
    token = os.environ.get('MAT_BENCH_TOKEN') or cfg.get('token')
    session_id = os.environ.get('MAT_BENCH_SESSION_ID') or cfg.get('session_id')
    if not token or not session_id:
        print(
            'error: no token/session found. Run:\n'
            f'  mat-bench-client setup --server {server} --token <TOKEN>\n'
            'Or set env vars: MAT_BENCH_TOKEN and MAT_BENCH_SESSION_ID',
            file=sys.stderr,
        )
        sys.exit(1)
    return token, session_id


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only — no external dependencies)
# ---------------------------------------------------------------------------

def _http(
    method: str,
    url: str,
    headers: dict | None = None,
    body: bytes | None = None,
) -> tuple[int, bytes]:
    req = urllib_request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib_request.urlopen(req) as resp:
            return resp.status, resp.read()
    except HTTPError as exc:
        return exc.code, exc.read()
    except URLError as exc:
        print(f'error: cannot reach server — {exc.reason}', file=sys.stderr)
        sys.exit(1)


def _json_get(url: str, token: str, params: dict | None = None) -> tuple[int, dict | list]:
    if params:
        url = url + '?' + urlencode({k: v for k, v in params.items() if v is not None})
    status, raw = _http('GET', url, headers={'X-API-Token': token, 'Accept': 'application/json'})
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, {'_raw': raw.decode(errors='replace')}


def _json_post(url: str, token: str, data: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(data).encode() if data else None
    headers: dict[str, str] = {'X-API-Token': token, 'Accept': 'application/json'}
    if body:
        headers['Content-Type'] = 'application/json'
    status, raw = _http('POST', url, headers=headers, body=body)
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, {'_raw': raw.decode(errors='replace')}


def _multipart_encode(
    fields: dict[str, str],
    files: list[tuple[str, str, bytes]],
) -> tuple[str, bytes]:
    boundary = uuid.uuid4().hex
    body = b''
    for name, value in fields.items():
        body += f'--{boundary}\r\n'.encode()
        body += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        body += value.encode('utf-8') + b'\r\n'
    for name, filename, data in files:
        body += f'--{boundary}\r\n'.encode()
        body += (
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            'Content-Type: application/octet-stream\r\n\r\n'
        ).encode()
        body += data + b'\r\n'
    body += f'--{boundary}--\r\n'.encode()
    return f'multipart/form-data; boundary={boundary}', body


# ---------------------------------------------------------------------------
# API wrappers  (all paths relative to /bench)
# ---------------------------------------------------------------------------

def api_create_session(api_base: str, token: str, model_name: str) -> str:
    status, data = _json_post(f'{api_base}/sessions', token, data={'model_name': model_name})
    if status != 200:
        print(f'error: session creation failed ({status}): {data}', file=sys.stderr)
        sys.exit(1)
    return data['session_id']  # type: ignore[index]


def api_list_questions(
    api_base: str,
    token: str,
    capability: str | None = None,
    domain: str | None = None,
    limit: int | None = None,
) -> list:
    status, data = _json_get(
        f'{api_base}/questions',
        token,
        params={'capability': capability, 'domain': domain, 'limit': limit},
    )
    if status != 200:
        print(f'error: listing questions failed ({status}): {data}', file=sys.stderr)
        sys.exit(1)
    return data  # type: ignore[return-value]


def api_get_question(api_base: str, token: str, question_id: str, session_id: str) -> dict:
    status, data = _json_get(
        f'{api_base}/questions/{question_id}',
        token,
        params={'session_id': session_id},
    )
    if status == 404:
        print(f'error: question {question_id!r} not found', file=sys.stderr)
        sys.exit(1)
    if status != 200:
        print(f'error: fetching question failed ({status}): {data}', file=sys.stderr)
        sys.exit(1)
    return data  # type: ignore[return-value]


def api_download_data(
    api_base: str,
    token: str,
    question_id: str,
    fname: str,
    dest_path: Path,
) -> None:
    url = f'{api_base}/questions/{question_id}/data/{fname}'
    status, raw = _http('GET', url, headers={'X-API-Token': token})
    if status == 404:
        print(f'error: data file {fname!r} not found for question {question_id!r}', file=sys.stderr)
        sys.exit(1)
    if status != 200:
        print(f'error: download failed ({status}): {raw.decode(errors="replace")}', file=sys.stderr)
        sys.exit(1)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(raw)


def api_submit(
    api_base: str,
    token: str,
    session_id: str,
    question_id: str,
    answer: str,
    num_turns: int = 1,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    files: list[tuple[str, Path]] | None = None,
) -> dict:
    meta = json.dumps({
        'answer': answer,
        'num_turns': num_turns,
        'is_error': False,
        'usage': {
            'prompt_tokens': prompt_tokens,
            'completion_tokens': completion_tokens,
            'total_tokens': prompt_tokens + completion_tokens,
        },
        'tool_calls': [],
    })
    file_parts: list[tuple[str, str, bytes]] = []
    for i, (_, path) in enumerate(files or [], start=1):
        file_parts.append((f'file{i}', path.name, path.read_bytes()))

    content_type, body = _multipart_encode({'meta': meta}, file_parts)
    url = f'{api_base}/submit/{question_id}?session_id={session_id}'
    status, raw = _http('POST', url, headers={
        'X-API-Token': token,
        'Content-Type': content_type,
        'Accept': 'application/json',
    }, body=body)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {'_raw': raw.decode(errors='replace')}
    if status not in (200, 202):
        print(f'error: submission failed ({status}): {data}', file=sys.stderr)
        sys.exit(1)
    return data  # type: ignore[return-value]


def api_get_result(
    api_base: str,
    token: str,
    question_id: str,
    session_id: str,
    wait: bool = False,
    timeout: int = 120,
) -> tuple[int, dict | list]:
    url = f'{api_base}/results/{question_id}'
    deadline = time.monotonic() + timeout
    while True:
        status, data = _json_get(url, token, params={'session_id': session_id})
        if status == 202 and wait:
            if time.monotonic() >= deadline:
                print('error: timed out waiting for grading result', file=sys.stderr)
                sys.exit(1)
            time.sleep(3)
            continue
        return status, data


def api_get_results(api_base: str, token: str, session_id: str) -> dict:
    status, data = _json_get(f'{api_base}/results', token, params={'session_id': session_id})
    if status != 200:
        print(f'error: fetching results failed ({status}): {data}', file=sys.stderr)
        sys.exit(1)
    return data  # type: ignore[return-value]


def api_get_evaluated_question_ids(api_base: str, token: str, session_id: str) -> set[str]:
    summary = api_get_results(api_base, token, session_id)
    return {r['question_id'] for r in summary.get('results', []) if 'question_id' in r}


def api_list_sessions(api_base: str, token: str, limit: int = 10) -> list:
    status, data = _json_get(f'{api_base}/sessions', token, params={'limit': limit})
    if status != 200:
        print(f'error: fetching sessions failed ({status}): {data}', file=sys.stderr)
        sys.exit(1)
    return data  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _server_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        '--server',
        type=str,
        default=None,
        metavar='URL',
        help='Server base URL, e.g. http://host:5000 (default: saved config or $MAT_BENCH_SERVER_URL).',
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='mat-bench-client',
        description=(
            'CLI client for a mat-bench server. '
            'API is always at {server}/bench.'
        ),
    )
    subs = parser.add_subparsers(dest='command')

    # setup
    p = subs.add_parser('setup', help='Save token, create session, and write config')
    _server_arg(p)
    p.add_argument(
        '--token', type=str, required=True, metavar='TOKEN',
        help='Your API token (64-char hex string provided by the server admin).',
    )
    p.add_argument(
        '--model', type=str, required=True, metavar='NAME',
        help='Model/agent identifier locked for all submissions in this session.',
    )
    p.set_defaults(func=_cmd_setup)

    # session
    p = subs.add_parser('session', help='Create a new session using the saved token')
    _server_arg(p)
    p.add_argument(
        '--model', type=str, required=True, metavar='NAME',
        help='Model/agent identifier locked for all submissions in this session.',
    )
    p.set_defaults(func=_cmd_session)

    # status
    p = subs.add_parser('status', help='Show current token, session ID, and server from config')
    _server_arg(p)
    p.add_argument('-n', type=int, default=None, metavar='N',
                   help='Also list the N most recent sessions from the server')
    p.set_defaults(func=_cmd_status)

    # questions
    p = subs.add_parser('questions', help='List available questions')
    _server_arg(p)
    p.add_argument('--capability', type=str, default=None)
    p.add_argument('--domain', type=str, default=None)
    p.add_argument('--limit', type=int, default=None)
    p.add_argument('--pending', action='store_true',
                   help='Only show questions not yet evaluated in the current session')
    p.set_defaults(func=_cmd_questions)

    # question
    p = subs.add_parser('question', help='Fetch a single question (records start time)')
    p.add_argument('question_id', type=str)
    _server_arg(p)
    p.set_defaults(func=_cmd_question)

    # data
    p = subs.add_parser('data', help='Download a question data file')
    p.add_argument('question_id', type=str)
    p.add_argument('filename', type=str, help='File name or key as listed by `question`')
    p.add_argument('-o', '--output', type=str, default=None, metavar='PATH',
                   help='Output path (default: ./<filename>)')
    _server_arg(p)
    p.set_defaults(func=_cmd_data)

    # submit
    p = subs.add_parser('submit', help='Submit an answer for a question')
    p.add_argument('question_id', type=str)
    p.add_argument('--answer', type=str, required=True, help='Final answer text')
    p.add_argument('--turns', type=int, default=1, metavar='N',
                   help='Number of agent turns (default: 1)')
    p.add_argument('--prompt-tokens', type=int, default=0, metavar='N')
    p.add_argument('--completion-tokens', type=int, default=0, metavar='N')
    p.add_argument('--files', nargs='*', metavar='PATH',
                   help='Output files to attach (file1, file2, ... fields)')
    _server_arg(p)
    p.set_defaults(func=_cmd_submit)

    # result
    p = subs.add_parser('result', help='Get grading result for one question')
    p.add_argument('question_id', type=str)
    p.add_argument('--wait', action='store_true', help='Poll until grading completes')
    p.add_argument('--timeout', type=int, default=120, metavar='SECS',
                   help='Max wait seconds when --wait is used (default: 120)')
    _server_arg(p)
    p.set_defaults(func=_cmd_result)

    # results
    p = subs.add_parser('results', help='Get aggregate results summary')
    _server_arg(p)
    p.add_argument('--session', type=str, default=None, metavar='SESSION_ID',
                   help='Session ID to query (default: from config / $MAT_BENCH_SESSION_ID)')
    p.add_argument('--download', type=str, nargs='?', const='', default=None, metavar='FILE',
                   help='Save detailed CSV of results to FILE (default: results_<session_id>.csv)')
    p.set_defaults(func=_cmd_results)

    return parser


def _cmd_setup(args: argparse.Namespace) -> None:
    server = _resolve_server(args.server)
    base = _api(server)
    print(f'Connecting to {base} ...', file=sys.stderr)
    session_id = api_create_session(base, args.token, args.model)
    cfg = _load_config()
    cfg.update({'server_url': server, 'token': args.token, 'session_id': session_id, 'model_name': args.model})
    _save_config(cfg)
    print(f'Token:      {args.token}')
    print(f'Session:    {session_id}')
    print(f'Model:      {args.model}')
    print(f'Config:     {_config_path()}')


def _cmd_session(args: argparse.Namespace) -> None:
    if os.environ.get('MAT_BENCH_SESSION_ID'):
        print(
            'Do not create a new session during a benchmark run.',
            file=sys.stderr,
        )
        sys.exit(1)
    server = _resolve_server(args.server)
    base = _api(server)
    cfg = _load_config()
    token = os.environ.get('MAT_BENCH_TOKEN') or cfg.get('token')
    if not token:
        print('error: no token found. Run setup first.', file=sys.stderr)
        sys.exit(1)
    session_id = api_create_session(base, token, args.model)
    cfg.update({'server_url': server, 'token': token, 'session_id': session_id, 'model_name': args.model})
    _save_config(cfg)
    print(f'New session: {session_id}')
    print(f'Model:       {args.model}')
    print(f'Config:      {_config_path()}')


def _cmd_status(args: argparse.Namespace) -> None:
    cfg = _load_config()
    token = os.environ.get('MAT_BENCH_TOKEN') or cfg.get('token', '')
    session_id = os.environ.get('MAT_BENCH_SESSION_ID') or cfg.get('session_id', '')
    server = os.environ.get('MAT_BENCH_SERVER_URL') or cfg.get('server_url', _DEFAULT_SERVER)
    token_display = (token[:8] + '...' + token[-4:]) if len(token) > 12 else token or '(not set)'
    print(f'Server:     {server}')
    print(f'Token:      {token_display}')
    print(f'Session:    {session_id or "(not set)"}')
    print(f'Config:     {_config_path()}')
    if args.n is not None:
        if not token:
            print('error: no token found. Run setup first.', file=sys.stderr)
            sys.exit(1)
        sessions = api_list_sessions(_api(_resolve_server(args.server)), token, limit=args.n)
        print()
        print(f'Recent sessions (last {args.n}):')
        print(f"  {'Session ID':<34s} {'Model':<20s} Created")
        print('  ' + '-' * 75)
        for s in sessions:
            marker = ' *' if s['session_id'] == session_id else ''
            print(f"  {s['session_id']:<34s} {s.get('model_name', ''):<20s} {s.get('created_at', '')}{marker}")


def _cmd_questions(args: argparse.Namespace) -> None:
    server = _resolve_server(args.server)
    token, session_id = _require_credentials(server)
    questions = api_list_questions(
        _api(server), token,
        capability=args.capability, domain=args.domain, limit=args.limit,
    )
    if args.pending:
        evaluated = api_get_evaluated_question_ids(_api(server), token, session_id)
        questions = [q for q in questions if q['id'] not in evaluated]
    print(f'Found {len(questions)} question(s)' + (' not yet evaluated' if args.pending else ''))
    print()
    for q in questions:
        intent = q.get('intent', '')
        if len(intent) > 60:
            intent = intent[:57] + '...'
        cap = ', '.join(q.get('capability', []) or [])
        print(f"  {q['id']:<34s} {cap:<25s} {q.get('domain', ''):<15s} {intent}")


def _cmd_question(args: argparse.Namespace) -> None:
    server = _resolve_server(args.server)
    token, session_id = _require_credentials(server)
    q = api_get_question(_api(server), token, args.question_id, session_id)
    print(f"Question:   {q['id']}")
    cap = ', '.join(q.get('capability', []) or [])
    print(f"Capability: {cap}")
    print(f"Domain:     {q.get('domain', '')}")
    print()
    print('--- Prompt ---')
    print(q.get('prompt', q.get('human_prompt_seed', '')))
    data_files = q.get('data_files', [])
    if data_files:
        print()
        print('--- Data files ---')
        for f in data_files:
            print(f"  {f.get('key', f.get('filename', '')):<20s}  {f.get('filename', '')}")


def _cmd_data(args: argparse.Namespace) -> None:
    server = _resolve_server(args.server)
    token, _ = _require_credentials(server)
    dest = Path(args.output) if args.output else Path(Path(args.filename).name)
    api_download_data(_api(server), token, args.question_id, args.filename, dest)
    print(f'Saved to: {dest}')


def _cmd_submit(args: argparse.Namespace) -> None:
    server = _resolve_server(args.server)
    token, session_id = _require_credentials(server)
    files: list[tuple[str, Path]] = []
    for path_str in (args.files or []):
        p = Path(path_str)
        if not p.is_file():
            print(f'error: file not found: {path_str}', file=sys.stderr)
            sys.exit(1)
        files.append((p.name, p))
    result = api_submit(
        _api(server), token, session_id,
        question_id=args.question_id,
        answer=args.answer,
        num_turns=args.turns,
        prompt_tokens=args.prompt_tokens,
        completion_tokens=args.completion_tokens,
        files=files,
    )
    print(f"Status:   {result.get('status', 'unknown')}")
    print(f"Question: {result.get('question_id', args.question_id)}")
    print(f"Session:  {result.get('session_id', session_id)}")
    if result.get('status') == 'grading':
        print('(Grading in progress — use `result --wait` to poll)')


def _cmd_result(args: argparse.Namespace) -> None:
    server = _resolve_server(args.server)
    token, session_id = _require_credentials(server)
    status, data = api_get_result(
        _api(server), token,
        question_id=args.question_id,
        session_id=session_id,
        wait=args.wait,
        timeout=args.timeout,
    )
    if status == 202:
        print('Status: grading (still in progress)')
        print('Tip:    re-run with --wait to poll until complete')
        return
    if status == 404:
        print(f'No result found for question {args.question_id!r}')
        return
    if status != 200:
        print(f'error: unexpected response ({status}): {data}', file=sys.stderr)
        sys.exit(1)
    results = data if isinstance(data, list) else [data]
    for r in results:
        score = r.get('overall_weighted_score', 0.0)
        print(f"Question:     {r.get('question_id', args.question_id)}")
        print(f"Status:       {r.get('run_status', 'unknown')}")
        print(f"Criteria:     {r.get('passed_count', 0)}/{r.get('total_count', 0)} passed")
        print(f"Score:        {score:.3f}")


def _cmd_results(args: argparse.Namespace) -> None:
    server = _resolve_server(args.server)
    token, default_session = _require_credentials(server)
    session_id = args.session or default_session
    summary = api_get_results(_api(server), token, session_id)
    total = summary.get('total', 0)
    passed = summary.get('questions_passed', 0)
    rate = summary.get('pass_rate', 0.0)
    weighted = summary.get('weighted_pass_rate', 0.0)
    print(f'Session:         {session_id}')
    print(f'Total questions: {total}')
    print(f'Passed:          {passed}/{total}')
    print(f'Pass rate:       {rate:.1%}')
    print(f'Weighted:        {weighted:.3f}')
    rows = summary.get('results', [])
    if rows:
        print()
        print(f"  {'Question':<34s} {'Capability':<25s} {'Domain':<15s} {'Status':<12s} Score")
        print('  ' + '-' * 100)
        for r in rows:
            p = r.get('passed_count', 0)
            t = r.get('total_count', 0)
            w = r.get('overall_weighted_score', 0.0)
            cap = ', '.join(r.get('capability', []) or [])
            print(
                f"  {r.get('question_id', ''):<34s}"
                f"{cap:<25s}"
                f"{r.get('domain', ''):<15s}"
                f"{r.get('run_status', ''):<12s}"
                f"{p}/{t}  ({w:.2f})"
            )
    if args.download is not None:
        dest = Path(args.download) if args.download else Path(f'results_{session_id}.csv')
        with dest.open('w', newline='', encoding='utf-8') as fh:
            writer = csv.DictWriter(fh, fieldnames=[
                'question_id', 'domain', 'capability', 'run_status',
                'passed', 'passed_count', 'total_count', 'overall_weighted_score',
            ])
            writer.writeheader()
            for r in rows:
                writer.writerow({
                    'question_id': r.get('question_id', ''),
                    'domain': r.get('domain', ''),
                    'capability': ', '.join(r.get('capability', []) or []),
                    'run_status': r.get('run_status', ''),
                    'passed': r.get('passed', ''),
                    'passed_count': r.get('passed_count', 0),
                    'total_count': r.get('total_count', 0),
                    'overall_weighted_score': r.get('overall_weighted_score', 0.0),
                })
        print(f'\nCSV saved to: {dest}')


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == '__main__':
    main()
