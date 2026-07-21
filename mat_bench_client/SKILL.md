---
name: mat-bench-client
description: Run the complete mat-agent-bench evaluation workflow using the mat-bench-client CLI. Covers: one-time setup with a provided API token, session management, fetching questions, downloading data files, executing material-science tasks, submitting results, and checking scores. Supports all nine capability domains: batch_processing / structure_construction / workflow_orchestration / data_diagnosis / scientific_analysis / execution_contract / input_generation / safety_refusal / structure_retrieval.
---

# MAT-BENCH-CLIENT — Complete Evaluation Workflow

## Installation

```bash
pip install ./mat_bench_client   # from the mat_agent_bench repo root
```

Requires Python ≥ 3.11 and `pyyaml`. No other dependencies.

## Architecture

| Component | URL |
|-----------|-----|
| Web UI / management | `http://<host>:<port>` |
| Evaluation API | `http://<host>:<port>/bench` ← CLI handles this automatically |

The CLI always appends `/bench` to the server URL internally. Always pass the **bare** server URL (no `/bench` suffix).

Config is persisted at `~/.mat-bench-client/config.yaml` (token, session_id, server_url).

---

## Step 1 — One-time Setup

Use the token provided by the server admin. Do not self-register on normal,
shared, or internet-accessible deployments. For a local development server
explicitly started with `--allow-token-registration`, `mat-bench-client setup`
may register a development token without `--token`.

```bash
mat-bench-client setup \
  --server http://<host>:<port> \
  --token <64-char-hex-token>
```

Output:
```
Token:    <token>
Session:  S20260519_143201_abc123
Config:   /home/user/.mat-bench-client/config.yaml
```

After setup, the server URL, token, and session ID are saved. All subsequent commands read them automatically — no `--server` or `--token` flags needed.

---

## Step 2 — List Questions

```bash
mat-bench-client questions
```

Optional filters:
```bash
mat-bench-client questions --capability structure_construction
mat-bench-client questions --domain catalysis --limit 10
```

Output: `id`, `capability`, `domain`, `intent` columns.

---

## Step 3 — Fetch a Question (REQUIRED before submit)

```bash
mat-bench-client question <question_id>
```

This call **records the task start time** on the server. Submitting without calling this first returns `"No task start time recorded"`.

Output:
```
Question:   BP_elec_001_20260428
Capability: batch_processing
Domain:     catalysis

--- Prompt ---
<full task description>

--- Data files ---
input_data       input_data.csv
ref_structure    ref.cif
```

---

## Step 4 — Download Data Files

```bash
mat-bench-client data <question_id> <filename>
# saves to ./<filename> by default

mat-bench-client data <question_id> input_data.csv -o /work/input_data.csv
```

Download all data files listed in Step 3 before executing the task.

---

## Step 5 — Execute the Task

Work in a local directory. Typical task types:

| Capability | What to produce |
|---|---|
| `structure_construction` | POSCAR or CIF structure file |
| `input_generation` | INCAR, KPOINTS, POSCAR, POTCAR |
| `batch_processing` | Output files + summary (CSV/JSON) |
| `data_diagnosis` | Analysis report / corrected data |
| `scientific_analysis` | Plots, computed values, report |
| `workflow_orchestration` | Multi-step workflow script + outputs |
| `execution_contract` | File with exact name/format specified in prompt |
| `structure_retrieval` | Structure file from database query |
| `safety_refusal` | Text answer refusing the unsafe request |

Keep all generated files in a dedicated work directory — you will upload them in Step 6.

---

## Step 6 — Submit Answer

```bash
mat-bench-client submit <question_id> \
  --answer "<brief final answer>" \
  --model "<your-model-name>" \
  --turns <number-of-agent-turns> \
  --files output1.cif output2.csv report.txt
```

Full options:
```
--answer TEXT            Final answer text (required)
--model NAME             Agent/model identifier (default: unknown)
--turns N                Number of agent turns (default: 1)
--prompt-tokens N        Input token count (default: 0)
--completion-tokens N    Output token count (default: 0)
--files PATH ...         Output files to attach (space-separated)
```

Files are sent as `file1`, `file2`, … form fields. Keep original filenames — the grader matches by name.

Output:
```
Status:   grading
Question: BP_elec_001_20260428
Session:  S20260519_143201_abc123
(Grading in progress — use `result --wait` to poll)
```

**Submission limits:**
- Each question can be submitted **once per session**.
- Maximum **5 attempts** per question across all sessions.
- To retry, create a new session first (see Session Management below).

---

## Step 7 — Check Score

Single question (poll until grading completes):
```bash
mat-bench-client result <question_id> --wait
```

Without `--wait`, returns immediately (may show `grading` status):
```bash
mat-bench-client result <question_id>
```

Output:
```
Question: BP_elec_001_20260428
Status:   completed
Score:    8/10 checkpoints passed
Weighted: 0.800
```

Aggregate summary for all questions in the current session:
```bash
mat-bench-client results
```

---

## Session Management

A single session can be used for all questions. Create a **new session** when:
- You need to retry a question that was already submitted in the current session
- The current session is corrupted

```bash
mat-bench-client session
```

This creates a new session using the saved token and updates `config.yaml`. The new session ID is used for all subsequent commands automatically.

---

## Recommended Workflow for All Questions

```bash
# 1. One-time setup
mat-bench-client setup --server http://<host>:<port> --token <token>

# 2. Get full question list
mat-bench-client questions

# 3. For each question:
mat-bench-client question <id>                   # MUST do before submit
mat-bench-client data <id> <file1>               # download each data file
# ... execute the task ...
mat-bench-client submit <id> \
  --answer "..." --model "my-agent" --turns 5 \
  --files output.cif result.csv
mat-bench-client result <id> --wait              # poll for score

# 4. Final summary
mat-bench-client results
```

Recommended capability order (easiest → hardest):
1. `execution_contract` — format/filename checks
2. `structure_retrieval` — database lookups
3. `safety_refusal` — refuse unsafe requests
4. `batch_processing` — bulk computation
5. `structure_construction` — structure file generation
6. `data_diagnosis` — data analysis
7. `input_generation` — DFT input files
8. `scientific_analysis` — scientific computation
9. `workflow_orchestration` — multi-step workflows

---

## Common Errors

| Error | Cause | Fix |
|---|---|---|
| `session creation failed (404)` | Wrong server URL or server not running | Check `--server` points to the bare host:port, not host:port/bench |
| `Not authenticated` | Invalid or missing token | Re-run `setup --token <token>` |
| `"No task start time recorded"` | Skipped `question` fetch before submit | Run `mat-bench-client question <id>` first |
| `"Field required"` on question fetch | Missing session_id | Re-run `setup` or `session` to create a valid session |
| `submission failed (409)` / limit reached | Already submitted in this session | Run `mat-bench-client session` to get a new session |
| `error: file not found` | Wrong path passed to `--files` | Use absolute or correct relative paths |
| Score not updating | Grading is async | Use `result --wait` or wait 3–5 seconds then re-check |

---

## Environment Variables (optional)

| Variable | Effect |
|---|---|
| `MAT_BENCH_SERVER_URL` | Override server URL without `--server` flag |
| `MAT_BENCH_TOKEN` | Override token (takes precedence over config) |
| `MAT_BENCH_SESSION_ID` | Override session ID (takes precedence over config) |
| `MATBENCH_HOME` | Override config directory (default: `~/.mat-bench-client`) |
