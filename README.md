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
mat-bench list --capability input_generation --domain abacus
mat-bench list --tags vasp incar
```

### 2. Grade an agent run

Agent runs must be provided as a JSONL file where each line is an `EvalRunRecord` (see `mat_bench/schemas.py`).

```bash
mat-bench grade runs/my_agent_raw_runs.jsonl --output-dir runs/results/
```

This writes a graded summary and per-question report to `--output-dir`.

### 3. Re-generate reports from existing results

```bash
mat-bench report runs/results/raw_runs.jsonl --output-dir runs/results/ --prefix final_
```

---

## Project Layout

```
mat_bench/          # Core Python package
  cli.py            # mat-bench CLI entry point
  grade.py          # High-level grading API
  evaluator.py      # Per-checklist-item binary verifiers
  schemas.py        # Pydantic models (EvalRunRecord, etc.)
  registry.py       # Question bank loader
  validators/       # Optional structure/CIF validators
question_bank/      # Question definitions (YAML)
  manifest.yaml     # Tool registry and bank index
  <capability>/     # One YAML per capability category
runs/               # Suggested directory for agent run outputs
```
