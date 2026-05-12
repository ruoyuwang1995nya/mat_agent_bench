Run the mat-agent-bench benchmark against a local server.

## Overview

You will fetch questions from a benchmark server, execute each task, and submit your results. The server handles grading automatically.

## Workflow

**Step 1 — Get the question list**

```
GET $SERVER_URL/questions
```

Optional query params: `capability=`, `domain=`, `limit=`

Returns a JSON list of `{id, capability, domain, intent, tags}`.

**Step 2 — For each question**

a. Fetch full details (prompt + data file list):
```
GET $SERVER_URL/questions/{id}
```
Response: `{id, prompt, data_files: [{key, path, filename}], ...}`

b. Create a local workspace directory for this question:
```
mkdir -p /tmp/mat_bench/{id}
cd /tmp/mat_bench/{id}
```

c. Download each data file listed in `data_files`:
```
curl -L -o {filename} "$SERVER_URL/questions/{id}/data/{filename}"
```

d. **Execute the task** — read the `prompt` and complete the task inside the workspace directory. Create all output files in that directory.

e. Submit results:
```
curl -X POST "$SERVER_URL/submit/{id}" \
  -F 'meta={"answer": "<your final answer>", "model_name": "<model>", "num_turns": <N>, "duration_ms": <ms>, "usage": {"prompt_tokens": <N>, "completion_tokens": <N>, "total_tokens": <N>}, "tool_calls": [<see below>]}' \
  -F 'file1=@/tmp/mat_bench/{id}/output_file.ext' \
  -F 'file2=@/tmp/mat_bench/{id}/another_file.ext'
```

Upload **all files you created** in the workspace directory, one `-F` field per file.

**Step 3 — Check scores**

```
GET $SERVER_URL/results
```

## Tool call self-reporting (for grounding axis scoring)

Include a `tool_calls` list in the `meta` JSON to enable grounding axis scoring:

```json
"tool_calls": [
  {
    "step": 1,
    "tool_name": "bash",
    "args": {"command": "python compute.py"},
    "observation_excerpt": "energy = -12.3 eV",
    "succeeded": true
  },
  {
    "step": 2,
    "tool_name": "python",
    "args": {"code": "import pymatgen..."},
    "observation_excerpt": "Structure(Fe2O3)",
    "succeeded": true
  }
]
```

Each entry: `step` (1-based), `tool_name`, `args` (dict), `observation_excerpt` (first ~500 chars of output), `succeeded` (bool).

## Notes

- The server URL is provided when this skill is invoked (e.g., `http://127.0.0.1:8765`)
- Work through questions sequentially unless told otherwise
- Create a fresh workspace directory per question to avoid cross-contamination
- Upload every file you created — the server evaluates based on file content
- The `POST /submit/{id}` response shows immediate grading: `{passed_count, total_count, overall_weighted_score}`
- If a question has no data files, skip step 2c

## Starting

The user will tell you the server URL. Begin with:
```
GET {SERVER_URL}/questions
```
Then work through each question in order.
