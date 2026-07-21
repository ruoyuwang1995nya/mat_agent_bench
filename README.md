# mat-agent-bench

**MAT-AGENT-BENCH** — a benchmark toolkit for evaluating AI agents on materials science tasks.

The benchmark covers 10 capability categories (structure retrieval, structure construction, input generation, workflow orchestration, batch processing, data diagnosis, execution contract, scientific analysis, safety/refusal, and more) and grades agent runs against a structured question bank using a multiplicative scoring model:

- **Correctness** (`S_correct`, 0–1): weighted average of content criteria (result accuracy, trajectory correctness, etc.)
- **Grounding** (`S_ground`, binary veto): 1 if all grounding criteria pass, 0 if any fail — a zero here zeros the entire score
- **Efficiency** (`S_efficiency`, 0–1 coefficient): weighted average of budget criteria (tokens, turns, wall-clock); defaults to 1 when no efficiency criteria are defined

**Final score = S_correct × S_ground × S_efficiency**

---

## Installation

Requires Python ≥ 3.11 and [uv](https://github.com/astral-sh/uv).

```bash
# Clone the repo
git clone <repo-url>
cd mat-agent-bench

# Install core package
uv venv .venv --python 3.12
uv pip install -e .

# Install with structure-validation extras (requires pymatgen)
uv pip install -e ".[validators]"
```

---

## Quick Start

### 1. Configure your LLM

Create a `.env` file in the repo root with your LLM provider settings:

```bash
MAT_BENCH_LLM_API_KEY="your-api-key"
MAT_BENCH_LLM_MODEL="your-model-name"
MAT_BENCH_LLM_BASE_URL="https://your-provider-base-url/v1"
```

The server loads this file automatically (override with `--env-file`).

### 2. Start the benchmark server

```bash
# Install server dependencies
uv pip install -e ".[server]"

# Start locally and allow external tools to register development tokens
mat-bench serve --allow-token-registration
```

### 3. Register an API token

Before running any agent, register a token with the server and export it:

```bash
TOKEN=$(curl -sf -X POST http://127.0.0.1:8080/bench/token | jq -r .token)
export TOKEN
```

> **Note:** You must register a token manually. The `run_question.sh` script requires `TOKEN` to be set and will not register one automatically.

### 4. List available questions

```bash
mat-bench list
```

Filter by capability or domain:

```bash
mat-bench list --capability input_generation
mat-bench list --tags vasp incar
```

### 5. Run an agent against the server

`scripts/run_question.sh` requires `TOKEN` to be set, creates a session, and invokes the agent:

```bash
# Run one question (server must already be running)
./scripts/run_question.sh SR_db_001_20260411v2

# Override model and turn limit
MODEL=claude-opus-4-6 MAX_TURNS=30 ./scripts/run_question.sh SR_db_001_20260411v2
```
The agent would show the test result.

### 6. Check results

```bash
curl -H "X-API-Token: <token>" "http://127.0.0.1:8765/results?session_id=<session>"
```

---

## Running Agents

The recommended way to run agents is via the [Harness-less Server Mode](#harness-less-server-mode). Start the server, then point your agent at the REST API.

For a quick smoke test using the built-in Claude Code agent:

```bash
mat-bench serve &
./scripts/run_question.sh SR_db_001_20260411v2
```

See [Harness-less Server Mode → Implementing your own agent](#implementing-your-own-agent-against-the-server) for how to build a custom agent against the API.

---

## Harness-less Server Mode

Instead of running questions through a local harness script, you can start an HTTP server that exposes the question bank over a REST API. Any agent with HTTP access — including a remote agent or one that cannot execute shell scripts — can fetch questions, download data files, and submit results directly.

### Start the server

```bash
# Install server dependencies first
uv pip install -e ".[server]"

# Local development: permit external tools to register tokens
mat-bench serve --allow-token-registration

# Custom host/port with an LLM judge
mat-bench serve --host 0.0.0.0 --port 9000 \
    --llm-judge anthropic/claude-sonnet-4-20250514 \
    --output-dir runs/my_server_run
```

All `mat-bench serve` options:

```
--host HOST             Host to bind to (default: 127.0.0.1)
--port PORT             Port to listen on (default: 8765)
--question-bank-dir DIR Path to question_bank directory (default: question_bank)
--output-dir DIR        Directory for workspaces and raw_runs.jsonl (default: runs/serve_<timestamp>)
--llm-judge PROVIDER/MODEL  LLM judge for llm_binary_judge criteria
--env-file FILE         Path to .env file for LLM config (default: .env)
--grading-workers N     Parallel grading threads (default: 4)
--allow-token-registration  Allow unauthenticated development token registration
```

### API overview

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/token` | Register a development token when explicitly enabled |
| `POST` | `/sessions` | Create a new session (requires `X-API-Token` header) |
| `GET`  | `/questions` | List questions (filter: `capability`, `domain`, `limit`) |
| `GET`  | `/questions/{id}` | Full question details + data file list |
| `GET`  | `/questions/{id}/data/{fname}` | Download a data file |
| `POST` | `/submit/{id}?session_id=S0001` | Submit result files + metadata (multipart) |
| `GET`  | `/results/{id}` | Grading result(s) for one question |
| `GET`  | `/results` | Summary of all submitted results |

Authentication is required for `/submit` and `/results`. Tokens are issued in
the web UI by default. On a local development server started with
`--allow-token-registration`, external tools can use `POST /bench/token` and
then create a session via `POST /bench/sessions`.

### Run a single question via the server (Claude Code)

`scripts/run_question.sh` requires a pre-registered `TOKEN` environment variable, creates a session, and invokes the agent against the server:

```bash
# Register a token first (if not already done)
TOKEN=$(curl -sf -X POST http://127.0.0.1:8080/bench/token | jq -r .token)
export TOKEN

# Run one question (server must already be running)
./scripts/run_question.sh SR_db_001_20260411v2

# Custom server URL
./scripts/run_question.sh SR_db_001_20260411v2 http://127.0.0.1:9000

# Override model and turn limit
MODEL=claude-opus-4-6 MAX_TURNS=30 ./scripts/run_question.sh SR_db_001_20260411v2
```

The script requires `TOKEN` to be exported beforehand, creates a session, fills in the prompt template at `agents/run_question.md`, and invokes `claude` with `--dangerously-skip-permissions`. After the run, check scores with:

```bash
curl -H "X-API-Token: <token>" "http://127.0.0.1:8765/results?session_id=<session>"
```

### Implementing your own agent against the server

Your agent should follow these steps for each question:

1. **Fetch question details** — `GET /questions/{id}` returns the prompt and a `data_files` list.
2. **Download data files** — `GET /questions/{id}/data/{fname}` for each file.
3. **Solve the task** and collect any output files.
4. **Submit** via `POST /submit/{id}?session_id=<session>` as a multipart form:
   - `meta` field: JSON string with `answer`, `model_name`, `num_turns`, `usage`, `tool_calls`, `is_error`.
   - Additional file fields: attach any output files produced during the run.
5. **Check your score** — `GET /results/{id}?session_id=<session>`.

See `agents/run_question.md` for the full prompt template used by the built-in Claude Code agent.

---

## Project Layout

```
mat_bench/          # Core Python package
  cli.py            # mat-bench CLI (run, list, grade, report)
  harness.py        # Benchmark orchestration engine
  grade.py          # High-level grading API
  evaluator.py      # Per-checklist-item binary verifiers
  schemas.py        # Pydantic models (EvalRunRecord, etc.)
  registry.py       # Question bank loader
  validators/       # Optional structure/CIF validators
agents/             # Agent scripts
  claude_code.sh    # Claude Code agent
  example.sh        # Minimal template to copy
scripts/            # Convenience wrappers
  run_benchmark.py  # Generic benchmark runner (same as mat-bench run)
  run_claude_code_baseline.py  # Claude Code runner with --model/--max-turns flags
question_bank/      # Question definitions (YAML)
  manifest.yaml     # Tool registry and bank index
  <capability>/     # One YAML per capability category
runs/               # Suggested directory for agent run outputs
```
