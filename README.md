# mat-agent-bench

**MAT-AGENT-BENCH** is a benchmark toolkit for evaluating AI agents on materials science tasks. It provides a question bank, a web UI, and an HTTP API for delivering tasks, receiving agent submissions, and grading results.

The benchmark covers capabilities including structure retrieval and construction, input generation, workflow orchestration, batch processing, data diagnosis, execution contracts, scientific analysis, and safety/refusal.

Each submission is graded with a multiplicative scoring model:

- **Correctness** (`S_correct`, 0-1): weighted average of content criteria such as result accuracy and trajectory correctness.
- **Grounding** (`S_ground`, binary veto): 1 only when every grounding criterion passes; a zero makes the final score zero.
- **Efficiency** (`S_efficiency`, 0-1): weighted average of budget criteria such as tokens, turns, and wall-clock time. It defaults to 1 when no efficiency criteria are configured.

**Final score = S_correct x S_ground x S_efficiency**

---

## Installation

Requires Python ≥ 3.11 and [uv](https://github.com/astral-sh/uv).

```bash
git clone <repo-url>
cd mat-agent-bench

uv venv .venv --python 3.12
uv pip install -e ".[server]"
```

Install optional structure-validation dependencies when the question bank requires them:

```bash
uv pip install -e ".[server,validators]"
```

## Start the Server

Use the `serve` command directly. It starts the web UI and the benchmark API together:

```bash
mat-bench serve
```

By default, the server listens on `0.0.0.0:8080`. From the same machine, open:

- UI: `http://127.0.0.1:8080/`
- API: `http://127.0.0.1:8080/bench`
- API reference: `http://127.0.0.1:8080/bench/docs`

Use explicit network settings when needed:

```bash
mat-bench serve --host 127.0.0.1 --port 9000
```

The server loads `.env` by default without replacing environment variables that are already set. Configure an LLM judge there when your selected questions use LLM-backed criteria:

```bash
MAT_BENCH_LLM_API_KEY="your-api-key"
MAT_BENCH_LLM_MODEL="your-model-name"
MAT_BENCH_LLM_BASE_URL="https://your-provider-base-url/v1"
MAT_BENCH_LLM_JUDGE="provider/model"
```

Pass a different environment file with `--env-file PATH`, or set `--llm-judge PROVIDER/MODEL` directly on the command line.

### Optional quick-start script

[`scripts/start_server.sh`](scripts/start_server.sh) is a convenience entrypoint for local use. It runs the same `mat-bench serve` command in the background, stops an existing listener on the chosen port, waits for the API health check, writes console output to `server.log`, and then exits.

```bash
./scripts/start_server.sh

# Optional positional arguments: PORT HOST CHECKLIST_WORKERS
./scripts/start_server.sh 9000 127.0.0.1 4
```

The script defaults to `8080`, `127.0.0.1`, and four parallel checklist workers. Use `mat-bench serve` when you need to control the full server configuration or want the server attached to your terminal.

## Authentication

The web UI issues API tokens for normal use. Create or retrieve a token in the UI, then export it for an agent or command-line client:

```bash
export TOKEN="<token-from-the-web-ui>"
```

For local development only, you can allow unauthenticated development-token registration when starting the server:

```bash
mat-bench serve --allow-token-registration

TOKEN=$(curl -sf -X POST http://127.0.0.1:8080/bench/token | jq -r .token)
export TOKEN
```

Without `--allow-token-registration`, `POST /bench/token` returns `403`. Requests that create runs, submit answers, inspect results, or access grading jobs require the `X-API-Token` header.

## Browse Questions

List questions from the CLI before or while the server is running:

```bash
mat-bench list
mat-bench list --capability input_generation
mat-bench list --tags vasp incar
```

The public API also exposes the question list:

```bash
curl -sS http://127.0.0.1:8080/bench/questions
```

## Run an Agent Through the API

The API is mounted below `/bench`. The recommended integration is run-scoped: create a run, obtain its task list, work each task, submit an answer with an idempotency key, and poll grading status.

1. Create a run for an agent model:

   ```bash
   curl -sS -X POST http://127.0.0.1:8080/bench/runs \
     -H "X-API-Token: $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"model_name":"my-agent-model"}'
   ```

2. Retrieve the run's assigned tasks. The response supplies question IDs and task details:

   ```bash
   curl -sS http://127.0.0.1:8080/bench/runs/<run_id>/tasks \
     -H "X-API-Token: $TOKEN"
   ```

3. Download a question's declared data files when needed:

   ```bash
   curl -sS -O \
     http://127.0.0.1:8080/bench/questions/<question_id>/data/<filename>
   ```

4. Submit the answer and any output files. Use the run ID in the query string and a unique `Idempotency-Key` for every logical submission:

   ```bash
   curl -sS -X POST \
     "http://127.0.0.1:8080/bench/submit/<question_id>?run_id=<run_id>" \
     -H "X-API-Token: $TOKEN" \
     -H "Idempotency-Key: <unique-submission-key>" \
     -F 'meta={"answer":"<agent answer>","model_name":"my-agent-model","num_turns":1,"usage":{},"tool_calls":[],"is_error":false}' \
     -F "files=@<output-file>"
   ```

   The response includes a grading-job identifier. Grading is asynchronous, so a results request can return `202` until it finishes.

5. Poll the grading job and retrieve run results:

   ```bash
   curl -sS http://127.0.0.1:8080/bench/grading-jobs/<grading_job_id> \
     -H "X-API-Token: $TOKEN"

   curl -sS "http://127.0.0.1:8080/bench/results?run_id=<run_id>" \
     -H "X-API-Token: $TOKEN"
   ```

See the interactive API reference at `/bench/docs` and [docs/api.md](docs/api.md) for complete request and response schemas, run configuration, task filtering, attachments, and grading states. Legacy session endpoints remain available for compatibility, but new integrations should use run-scoped routes.

## Run a Single Question

The included [`scripts/run_question.sh`](scripts/run_question.sh) wrapper invokes the configured agent for one question. Start the server first and export a valid token before using it:

```bash
export TOKEN="<token-from-the-web-ui>"
./scripts/run_question.sh SR_db_001_20260411v2
```

Specify a different server URL as the second argument, or override the agent settings with environment variables:

```bash
./scripts/run_question.sh SR_db_001_20260411v2 http://127.0.0.1:9000
MODEL=claude-opus-4-6 MAX_TURNS=30 ./scripts/run_question.sh SR_db_001_20260411v2
```

Use the results endpoint or the web UI to inspect the resulting grade:

```bash
curl -sS "http://127.0.0.1:8080/bench/results?session_id=<session_id>" \
  -H "X-API-Token: $TOKEN"
```

## Server Options

Run `mat-bench serve --help` for the authoritative option list. The primary options are grouped below.

### Network and sources

```text
--host HOST                    Bind address (default: 0.0.0.0)
--port PORT                    Listening port (default: 8080)
--question-bank-dir DIR        Question-bank directory
--question-config FILE         Question configuration file
```

When `--question-config` is omitted, the server uses `~/.matbench/questions.yaml` when it exists. Otherwise, it resolves the repository's bundled `question_bank` directory.

### Question selection

```text
--questions ID [ID ...]        Specific question IDs to host
--capability VALUE             Include a capability
--task-type VALUE              Include a task type
--domain VALUE                 Include a domain
--tags TAG [TAG ...]           Include questions with all selected tags
--exclude-questions ID [...]   Exclude question IDs
--exclude-capability VALUE     Exclude a capability
--exclude-task-type VALUE      Exclude a task type
--exclude-domain VALUE         Exclude a domain
--exclude-tags TAG [TAG ...]   Exclude questions with any selected tag
--limit N                      Maximum number of selected questions
```

### Storage, logging, and grading

```text
--store-dir DIR                Persistent server state (default: ~/.matbench)
--env-file FILE                Environment file (default: .env if it exists)
--log-level LEVEL              Logging level
--llm-judge PROVIDER/MODEL     LLM judge override
--grading-workers N            Parallel grading workers (default: 4)
--parallel-checklist-workers N Parallel checklist evaluators (default: 1)
--allow-token-registration     Enable development token registration
```

Each server invocation creates its run output beneath `~/.matbench/runs/serve_<timestamp>`. Logging-level precedence is `--log-level`, `MAT_BENCH_LOG_LEVEL`, `~/.matbench/config.yaml`, then `info`.

## Project Layout

```text
mat_bench/          Core package
  cli.py            Command-line interface, including `mat-bench serve`
  server/           HTTP API, authentication, submissions, and grading jobs
  ui/               Web UI mounted at the server root
  registry/         Question-bank loading and selection
  evaluation/       Grading and checklist evaluation
  validators/       Optional structure and CIF validation
agents/             Agent implementations and API guidance
scripts/            Convenience launchers, runners, and smoke tests
question_bank/      Question definitions and task assets
docs/               Published usage and API documentation
runs/               Suggested local agent-run outputs
```

## Further Reading

- [API documentation](docs/api.md)
- [Documentation home](docs/index.md)
- [Agent API guide](agents/agent_api_guide.md)
- [Quick server launcher](scripts/start_server.sh)
