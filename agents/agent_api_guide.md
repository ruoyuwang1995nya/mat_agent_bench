# MAT-AGENT-BENCH — HTTP API Guide for Agents

This guide lets any agent interact with the benchmark server using plain HTTP — no client library needed.

---

## Base URL

| Mode | API base URL |
|------|-------------|
| Standalone (`mat-bench serve`) | `http://<host>:<port>` |
| Combined (`mat-bench serve-all`) | `http://<host>:<port>/bench` |

All examples below use `$API` for the base URL. Set it once:

```bash
export API="http://localhost:8765/bench"   # serve-all mode
# or
export API="http://localhost:8765"         # standalone mode
```

You will also need your token:
```bash
export TOKEN="<64-char-hex-token-from-admin>"
```

---

## Authentication

All `/submit` and `/results` endpoints require the header:

```
X-API-Token: <your-token>
```

`/questions` endpoints need no authentication; only a valid `session_id` query parameter.

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
  "capability": "batch_processing",
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
    "run_status": "completed",
    "correctness_passed": 7,
    "correctness_total": 10,
    "correctness_weighted_score": 0.7,
    "grounding_passed": 3,
    "grounding_total": 3,
    "grounding_weighted_score": 1.0,
    "grounding_veto": false,
    "efficiency_passed": 1,
    "efficiency_total": 1,
    "efficiency_weighted_score": 1.0,
    "passed_count": 11,
    "total_count": 14
  }
]
```

**202 response** means grading is still in progress — retry after a few seconds.

**Scoring formula:** `final = correctness_score × grounding_factor × efficiency_score`
- `grounding_factor` = 0.0 if `grounding_veto` is true, else 1.0

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

---

## Full Python Workflow

```python
import json, time, requests

API     = "http://localhost:8765/bench"  # adjust for your server
TOKEN   = "<your-token>"
MODEL   = "my-agent-v1"
WORKDIR = "/tmp/mat_bench_work"

headers = {"X-API-Token": TOKEN}

# 1. Create session
r = requests.post(f"{API}/sessions", headers=headers,
                  json={"model_name": MODEL})
SESSION = r.json()["session_id"]

# 2. List questions
questions = requests.get(f"{API}/questions", headers=headers).json()

for q in questions:
    qid = q["id"]

    # 3. Fetch question (starts timer)
    details = requests.get(f"{API}/questions/{qid}",
                           headers=headers,
                           params={"session_id": SESSION}).json()
    prompt = details["prompt"]
    data_files = details.get("data_files", [])

    # 4. Download data files
    import os; os.makedirs(f"{WORKDIR}/{qid}", exist_ok=True)
    for f in data_files:
        fname = f["filename"]
        raw = requests.get(f"{API}/questions/{qid}/data/{fname}").content
        with open(f"{WORKDIR}/{qid}/{fname}", "wb") as fh:
            fh.write(raw)

    # 5. Execute task — your agent logic here
    answer = "..."          # your computed answer
    output_files = []       # paths to generated files

    # 6. Submit
    meta = json.dumps({
        "answer": answer,
        "num_turns": 1,
        "is_error": False,
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "tool_calls": [],
    })
    files_payload = [("meta", (None, meta))]
    for i, path in enumerate(output_files, start=1):
        with open(path, "rb") as fh:
            files_payload.append((f"file{i}", (os.path.basename(path), fh.read())))
    requests.post(f"{API}/submit/{qid}?session_id={SESSION}",
                  headers=headers, files=files_payload)

    # 7. Poll for result
    for _ in range(40):
        res = requests.get(f"{API}/results/{qid}",
                           headers=headers,
                           params={"session_id": SESSION})
        if res.status_code == 200:
            data = res.json()
            if data and data[0].get("run_status") == "completed":
                print(f"{qid}: score={data[0].get('correctness_weighted_score')}")
                break
        time.sleep(3)

# 8. Summary
summary = requests.get(f"{API}/results", headers=headers,
                       params={"session_id": SESSION}).json()
print(f"Pass rate: {summary['pass_rate']:.1%}  Weighted: {summary['weighted_pass_rate']:.3f}")
```

---

## Common Errors

| HTTP Status | Error | Cause | Fix |
|---|---|---|---|
| 401 | `X-API-Token header is required` | Missing token header | Add `-H "X-API-Token: $TOKEN"` |
| 401 | `Invalid or unknown API token` | Bad token | Check token value; re-register via web UI |
| 404 | `question not found` | Wrong question ID | Check `GET /questions` for valid IDs |
| 422 | `Field required` on session creation | Missing `model_name` in body | Send `{"model_name": "..."}` in JSON body |
| 400 | `No task start time recorded` | Submit called before fetch | Call `GET /questions/{id}?session_id=...` first |
| 409 | Already submitted | Question submitted in current session | Create a new session first |
| 202 | *(empty result)* | Grading still in progress | Retry `GET /results/{id}` after a few seconds |
