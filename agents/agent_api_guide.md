# MAT-AGENT-BENCH — HTTP API Guide for Agents

This guide lets any agent interact with the benchmark server using plain HTTP — no client library needed.

---

## Base URL

The server runs on a single address (default `http://localhost:8080`).

```bash
export API="http://localhost:8080/bench"   # bench API base
export TOKEN="<64-char-hex-token-from-admin>"
```

The guide itself is always available at:
```bash
curl http://localhost:8080/guide        # this document
curl http://localhost:8080/bench/guide  # same document, via bench sub-app
curl http://localhost:8080/bench/       # API info JSON
```

---

## Authentication

All `/submit` and `/results` endpoints require the header:

```
X-API-Token: <your-token>
```

`/questions` endpoints need no authentication; only a valid `session_id` query parameter.

**Tokens are issued by an admin via the web UI** (`http://localhost:8080`). You cannot self-register via the API.

---

## Step 1 — Create a Session

A session ties all your submissions to a model name. Create one session per benchmark run.

**Request:**
```bash
curl -s -X POST "$API/sessions" \
  -H "X-API-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model_name": "my-agent-v1"}' | jq .
```

**Response:**
```json
{
  "session_id": "S20260519_143201_abc123",
  "model_name": "my-agent-v1",
  "created_at": "2026-05-19T14:32:01Z"
}
```

Save the session ID:
```bash
export SESSION="S20260519_143201_abc123"
```

---

## Step 2 — List Available Questions

```bash
curl -s "$API/questions" | jq '.[] | {id, capability, domain, intent}'
```

Optional query filters:
- `capability` — e.g. `structure_construction`, `batch_processing`
- `domain` — e.g. `catalysis`, `mechanics`
- `limit` — integer, cap the result count

```bash
curl -s "$API/questions?capability=structure_construction&limit=5" | jq .
```

**Response:** JSON array of question objects with `id`, `capability`, `domain`, `intent` fields.

---

## Step 3 — Fetch a Question (REQUIRED before submit)

This call records the task start time. Submitting without calling this first returns an error.

```bash
curl -s "$API/questions/$QUESTION_ID?session_id=$SESSION" | jq .
```

**Response:**
```json
{
  "id": "BP_elec_001_20260428",
  "capability": "<capability>",
  "domain": "catalysis",
  "prompt": "<full task description>",
  "data_files": [
    {"key": "input_data", "filename": "input_data.csv"},
    {"key": "ref_structure", "filename": "ref.cif"}
  ]
}
```

Read the `prompt` field — it is the complete task description.

---

## Step 4 — Download Data Files

For each entry in `data_files`, download the file before starting work:

```bash
curl -s "$API/questions/$QUESTION_ID/data/$FILENAME" -o "/tmp/work/$FILENAME"
```

No authentication header required for downloads.

---

## Step 5 — Execute the Task

Work in a local directory, e.g. `/tmp/mat_bench/$SESSION/$QUESTION_ID/`.

| Capability | Typical output |
|---|---|
| `structure_construction` | POSCAR or CIF structure file |
| `input_generation` | INCAR, KPOINTS, POSCAR, POTCAR |
| `batch_processing` | Output files + summary (CSV/JSON) |
| `data_diagnosis` | Analysis report / corrected data |
| `scientific_analysis` | Plots, computed values, report |
| `workflow_orchestration` | Multi-step script + outputs |
| `execution_contract` | File with exact name/format from prompt |
| `structure_retrieval` | Structure file from database query |
| `safety_refusal` | Text answer refusing the unsafe request |

---

## Step 6 — Submit Answer

Submit your final answer and any output files as a multipart form.

**`meta` JSON field schema:**
```json
{
  "answer": "<brief final answer text>",
  "num_turns": 5,
  "is_error": false,
  "run_status": "completed",
  "usage": {
    "prompt_tokens": 12000,
    "completion_tokens": 3000,
    "total_tokens": 15000
  },
  "tool_calls": [
    {
      "step": 1,
      "tool_name": "bash",
      "args": {"command": "python run.py"},
      "observation_excerpt": "Done. Output: result.csv",
      "succeeded": true
    }
  ]
}
```

Use `"run_status": "timeout"` when execution exceeded its limit. Timeout submissions receive zero points.

**curl example (with output files):**
```bash
curl -s -X POST "$API/submit/$QUESTION_ID?session_id=$SESSION" \
  -H "X-API-Token: $TOKEN" \
  -F 'meta={"answer":"Fe3O4 POSCAR generated","num_turns":3,"is_error":false,"usage":{"prompt_tokens":5000,"completion_tokens":1200,"total_tokens":6200},"tool_calls":[]}' \
  -F 'file1=@/tmp/work/POSCAR' \
  -F 'file2=@/tmp/work/summary.csv' | jq .
```

**curl example (text answer only, no files):**
```bash
curl -s -X POST "$API/submit/$QUESTION_ID?session_id=$SESSION" \
  -H "X-API-Token: $TOKEN" \
  -F 'meta={"answer":"I cannot assist with this request.","num_turns":1,"is_error":false,"usage":{"prompt_tokens":200,"completion_tokens":30,"total_tokens":230},"tool_calls":[]}' \
  | jq .
```

**Response:**
```json
{"status": "grading", "question_id": "...", "session_id": "..."}
```

**Limits:**
- Each question can be submitted **once per session**.
- Maximum **5 attempts** per question across all sessions.

---

## Step 7 — Check Result for One Question

Grading is asynchronous. Poll until `run_status` is `completed`:

```bash
# Check once
curl -s "$API/results/$QUESTION_ID?session_id=$SESSION" \
  -H "X-API-Token: $TOKEN" | jq .

# Poll until done (bash loop)
while true; do
  STATUS=$(curl -s "$API/results/$QUESTION_ID?session_id=$SESSION" \
    -H "X-API-Token: $TOKEN")
  RUN_STATUS=$(echo "$STATUS" | jq -r '.[0].run_status // "pending"')
  echo "Status: $RUN_STATUS"
  [ "$RUN_STATUS" = "completed" ] && break
  sleep 3
done
echo "$STATUS" | jq '.[0] | {run_status, correctness_weighted_score, grounding_weighted_score, efficiency_weighted_score}'
```

**Response (200 completed):**
```json
[
  {
    "question_id": "BP_elec_001_20260428",
    "capability": ["bandstructure"],
    "domain": "electronic_structure",
    "run_status": "completed",
    "passed": false,
    "passed_count": 11,
    "total_count": 14,
    "overall_weighted_score": 0.78,
    "criteria_results": {
      "band_gap_value": {
        "criterion_id": "band_gap_value",
        "capability": "correctness",
        "passed": true,
        "reason": "Reported gap 1.12 eV matches reference within tolerance."
      },
      "grounding_citation": {
        "criterion_id": "grounding_citation",
        "capability": "scientific_grounding",
        "passed": false,
        "reason": "No DOI or data source cited for the band gap value."
      }
    }
  }
]
```

`criteria_results` is a dict keyed by criterion ID. Each entry contains:
- `criterion_id` — the rubric item identifier
- `capability` — rubric category (e.g. `correctness`, `scientific_grounding`, `efficiency`)
- `passed` — `true` / `false`
- `reason` — the grader's explanation for the verdict

**202 response** means grading is still in progress — retry after a few seconds.

---

## Step 8 — All Results Summary

```bash
curl -s "$API/results?session_id=$SESSION" \
  -H "X-API-Token: $TOKEN" | jq .
```

**Response:**
```json
{
  "total": 10,
  "questions_passed": 7,
  "pass_rate": 0.7,
  "weighted_pass_rate": 0.65,
  "results": [...]
}
```

## Common Errors

| HTTP Status | Error | Cause | Fix |
|---|---|---|---|
| 401 | `X-API-Token header is required` | Missing token header | Add `-H "X-API-Token: $TOKEN"` |
| 401 | `Invalid or unknown API token` | Bad token | Check token value; request a new one from the admin via the web UI |
| 404 | `question not found` | Wrong question ID | Check `GET /questions` for valid IDs |
| 422 | `Field required` on session creation | Missing `model_name` in body | Send `{"model_name": "..."}` in JSON body |
| 400 | `No task start time recorded` | Submit called before fetch | Call `GET /questions/{id}?session_id=...` first |
| 409 | Already submitted | Question submitted in current session | Create a new session first |
| 202 | *(empty result)* | Grading still in progress | Retry `GET /results/{id}` after a few seconds |
