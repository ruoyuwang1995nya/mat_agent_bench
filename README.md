# mat-agent-bench

**MAT-AGENT-BENCH** — a benchmark toolkit for evaluating AI agents on materials science tasks.

The benchmark covers 10 capability categories (structure retrieval, structure construction, input generation, workflow orchestration, batch processing, data diagnosis, execution contract, scientific analysis, safety/refusal, and more) and grades agent runs against a structured question bank using a binary pass/fail checklist system with per-item weights.

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

### 1. List available questions

```bash
mat-bench list
```

Filter by capability or domain:

```bash
mat-bench list --capability input_generation
mat-bench list --tags vasp incar
```

### 2. Run an agent

```bash
mat-bench run --agent agents/claude_code.sh --limit 3 --skip-grading
```

See the [Running Agents](#running-agents) section for full options and instructions on implementing your own agent.

### 3. Grade results

Agent runs must be provided as a JSONL file where each line is an `EvalRunRecord` (see `mat_bench/schemas.py`).

```bash
mat-bench grade runs/my_agent_raw_runs.jsonl --output-dir runs/results/
```

### 4. Re-generate reports from existing results

```bash
mat-bench report runs/results/raw_runs.jsonl --output-dir runs/results/ --prefix final_
```

---

## Running Agents

Use `mat-bench run` to run any agent against the question bank. Provide an agent shell script via `--agent` — the harness handles workspace setup, grading, and output.

### Built-in: Claude Code

```bash
# Quick smoke test (2 questions, no LLM grading)
mat-bench run --agent agents/claude_code.sh --limit 2 --skip-grading

# Run in parallel with LLM judge and generate full reports
mat-bench run --agent agents/claude_code.sh \
    --limit 5 -j 2 \
    --llm-judge anthropic/claude-sonnet-4-20250514 \
    --report

# Override Claude model / turn limit via env vars
mat-bench run --agent agents/claude_code.sh \
    --agent-env MODEL=opus MAX_TURNS=20 \
    --limit 3
```

### Implementing Your Own Agent

An agent is a shell script that receives three arguments and writes a JSON result file:

```
./my_agent.sh <workspace_dir> <prompt_file> <output_file>
```

| Argument | Description |
|---|---|
| `workspace_dir` | Working directory. Data files for the question are already copied here. Run your agent with this as cwd. |
| `prompt_file` | Path to a file containing the question prompt text. |
| `output_file` | Your script must write a JSON result here before exiting. |

**Output JSON format** (only `answer` is required):

```json
{
  "answer":      "the agent's final answer text",
  "model_name":  "gpt-4o",
  "num_turns":   5,
  "is_error":    false,
  "duration_ms": 12345,
  "usage": {
    "prompt_tokens":     1200,
    "completion_tokens":  450,
    "total_tokens":      1650
  }
}
```

Exit code 0 = success. Non-zero = error (recorded but run continues).

**Minimal template** (copy from `agents/example.sh`):

```bash
#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="$1"
PROMPT_FILE="$2"
OUTPUT_FILE="$3"

PROMPT=$(cat "$PROMPT_FILE")

# TODO: invoke your agent here, cwd is $WORKSPACE
ANSWER="my agent's answer"

cat > "$OUTPUT_FILE" <<EOF
{
  "answer": "$ANSWER",
  "model_name": "my-agent"
}
EOF
```

**OpenAI API example:**

```bash
#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="$1"
PROMPT_FILE="$2"
OUTPUT_FILE="$3"

PROMPT=$(cat "$PROMPT_FILE")

python3 - <<PYEOF
import json, os
from openai import OpenAI

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
resp = client.chat.completions.create(
    model=os.environ.get("MODEL", "gpt-4o"),
    messages=[{"role": "user", "content": open("$PROMPT_FILE").read()}],
)
msg = resp.choices[0].message
json.dump({
    "answer":     msg.content or "",
    "model_name": resp.model,
    "num_turns":  1,
    "usage": {
        "prompt_tokens":     resp.usage.prompt_tokens,
        "completion_tokens": resp.usage.completion_tokens,
        "total_tokens":      resp.usage.total_tokens,
    },
}, open("$OUTPUT_FILE", "w"))
PYEOF
```

Once written, run it with:

```bash
mat-bench run --agent agents/my_agent.sh --limit 5 \
    --agent-env OPENAI_API_KEY=sk-xxx MODEL=gpt-4o \
    --report
```

### All `mat-bench run` Options

```
--agent AGENT             Path to agent shell script (required)
--agent-env KEY=VALUE     Environment variables passed to the agent
--questions ID [ID ...]   Run specific question IDs
--capability CAPABILITY   Filter by capability
--domain DOMAIN           Filter by domain
--limit N                 Run only the first N questions
--output-dir DIR          Output directory (default: runs/<agent>_<timestamp>)
--timeout SECONDS         Per-question timeout (default: 600)
--mode {direct,planner}   Evaluation mode (default: direct)
--llm-judge PROVIDER/MODEL  LLM for llm_binary_judge criteria
--skip-grading            Skip grading, output pre-grading JSONL only
--report                  Generate full reports after grading
-j N, --jobs N            Parallel tasks (default: 1)
```

### Grading Later

If you used `--skip-grading`, grade the output separately:

```bash
mat-bench grade runs/<output>/raw_runs.jsonl --output-dir runs/<output>/
```

---

## Harness-less Server Mode

Instead of running questions through a local harness script, you can start an HTTP server that exposes the question bank over a REST API. Any agent with HTTP access — including a remote agent or one that cannot execute shell scripts — can fetch questions, download data files, and submit results directly.

### Start the server

```bash
# Install server dependencies first
uv pip install -e ".[server]"

# Start on default host/port (127.0.0.1:8765)
mat-bench serve

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
```

### API overview

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/token` | Register a new persistent API token |
| `POST` | `/sessions` | Create a new session (requires `X-API-Token` header) |
| `GET`  | `/questions` | List questions (filter: `capability`, `domain`, `limit`) |
| `GET`  | `/questions/{id}` | Full question details + data file list |
| `GET`  | `/questions/{id}/data/{fname}` | Download a data file |
| `POST` | `/submit/{id}?session_id=S0001` | Submit result files + metadata (multipart) |
| `GET`  | `/results/{id}` | Grading result(s) for one question |
| `GET`  | `/results` | Summary of all submitted results |

Authentication is required for `/submit` and `/results` — obtain a token via `POST /token` then create a session via `POST /sessions`.

### Run a single question via the server (Claude Code)

`scripts/run_question.sh` automates token registration, session creation, and agent invocation against the server:

```bash
# Run one question (server must already be running)
./scripts/run_question.sh SR_db_001_20260411v2

# Custom server URL
./scripts/run_question.sh SR_db_001_20260411v2 http://127.0.0.1:9000

# Override model and turn limit
MODEL=claude-opus-4-6 MAX_TURNS=30 ./scripts/run_question.sh SR_db_001_20260411v2
```

The script registers a token, creates a session, fills in the prompt template at `agents/run_question.md`, and invokes `claude` with `--dangerously-skip-permissions`. After the run, check scores with:

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
