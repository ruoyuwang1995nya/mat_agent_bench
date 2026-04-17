# mat-agent-bench

**MATTER v5** — a benchmark toolkit for evaluating AI agents on materials science tasks.

The benchmark covers 10 capability categories (structure retrieval, structure construction, input generation, workflow orchestration, batch processing, data diagnosis, execution contract, scientific analysis, safety/refusal, and more) and grades agent runs against a structured question bank using a binary pass/fail checklist system with per-item weights.

---

## Installation

Requires Python ≥ 3.11 and [uv](https://github.com/astral-sh/uv).

```bash
# Clone the repo
git clone <repo-url>
cd mat_agent_bench

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
