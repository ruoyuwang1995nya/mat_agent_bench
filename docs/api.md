# HTTP API Reference

All endpoints below are relative to `$API`, for example `http://127.0.0.1:8080/bench`.

## Status values

| Resource | Values |
| --- | --- |
| Run | `active` |
| Task | `activated`, `unavailable` |
| Attempt | `queued`, `completed`, `failed`, `grading_interrupted` |
| Grading job | `queued`, `running`, `completed`, `failed` |

An interrupted process cannot resume an in-flight grading call because the current server does not persist the complete evaluator payload. On restart, queued and running jobs become `failed` with an interruption message. Submit a new attempt with a new idempotency key.

## Service information

### `GET /`

Returns API identity and the link to the plain-text agent guide. No authentication is required.

### `GET /guide`

Returns the legacy plain-text HTTP guide. No authentication is required.

## Question catalog

### `GET /questions`

Lists hosted questions. No authentication is required.

| Query parameter | Type | Meaning |
| --- | --- | --- |
| `capability` | string | Match a question capability. |
| `task_type` | string | Match the task type. |
| `domain` | string | Match the domain. |
| `tags` | repeated string | Require all supplied tags. |
| `q` | string | Case-insensitive search across ID, intent, domain, capability, and tags. |
| `offset` | integer | Zero-based page offset; default `0`. |
| `limit` | integer | Maximum results; from `1` through `500`. |

```bash
curl -s "$API/questions?q=ZnO&domain=catalysis&limit=10" | jq .
```

Each item includes `id`, `capability`, `domain`, `intent`, and `tags`.

### `GET /questions/{question_id}/data/{fname}`

Downloads an input file by its data-file key, declared path, or basename. No authentication is required.

```bash
curl -s "$API/questions/$QUESTION_ID/data/$FILENAME" -o "$FILENAME"
```

## Sessions

### `POST /sessions`

Creates a token-owned session. Create one session for each evaluation campaign or agent configuration.

```bash
curl -s -X POST "$API/sessions" \
  -H "X-API-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model_name":"my-agent-v1"}' | jq .
```

The JSON body requires `model_name`. The response contains `session_id`, `model_name`, and `created_at`.

### `GET /sessions`

Lists sessions owned by the token. The optional `limit` parameter is from `1` through `500` and defaults to `10`.

## Runs and task activation

Runs define a token-owned, session-owned selection of questions. A run records the selected order and a SHA-256 `catalog_hash` calculated from the selected question payloads.

### `POST /runs?session_id={session_id}`

Creates an evaluation run and activates its question set. Authentication is required.

Supply either an explicit `question_ids` list or filters. When `question_ids` is supplied, filters further restrict that list. A selection that produces no questions returns `422`.

```bash
curl -s -X POST "$API/runs?session_id=$SESSION" \
  -H "X-API-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question_ids":["BP_elec_001_20260615v2"]}' | jq .
```

Supported JSON fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `model_name` | string | Optional model label for the run; defaults to the session model. |
| `question_ids` | string array | Explicit question IDs. |
| `capability` | string | Capability filter. |
| `task_type` | string | Task-type filter. |
| `domain` | string | Domain filter. |
| `tags` | string array | Require all supplied tags. |
| `limit` | integer | Cap the resulting selection. |

### `GET /runs/{run_id}`

Returns run metadata including `question_ids`, `catalog_hash`, `status`, and `created_at`. Authentication and token ownership are required.

### `GET /runs/{run_id}/tasks`

Lists activated tasks in the frozen run order. Authentication and token ownership are required.

### `GET /runs/{run_id}/tasks/{question_id}`

Returns the full prompt, data-file descriptors, and `started_at`. Authentication and token ownership are required.

The first request records the authoritative server-side task start time. Repeated fetches preserve that timestamp. Fetch this endpoint before submitting work so duration metrics begin at task delivery.

## Submission, attempts, and grading jobs

### `POST /submit/{question_id}?run_id={run_id}`

Submits an answer and optional artifact files for an activated run task. Authentication is required.

Required header:

```http
Idempotency-Key: <unique-client-key-for-this-attempt>
```

Use a stable key when retrying the same network request. Replaying a request with the same run, question, and key returns the same `attempt_id` and `job_id`; it does not enqueue grading again. Use a new key to create a new attempt.

The body is `multipart/form-data`:

| Part | Required | Meaning |
| --- | --- | --- |
| `meta` | Yes | JSON string containing answer and execution metadata. |
| Any file parts | No | Output artifacts. The multipart filename is used as the artifact path. |

Example:

```bash
curl -s -X POST "$API/submit/$QUESTION_ID?run_id=$RUN" \
  -H "X-API-Token: $TOKEN" \
  -H "Idempotency-Key: $(uuidgen)" \
  -F 'meta={"answer":"Generated the requested output.","num_turns":3,"is_error":false,"usage":{"prompt_tokens":5000,"completion_tokens":1200,"total_tokens":6200},"tool_calls":[]}' \
  -F 'output=@./result.csv' | jq .
```

`meta` fields currently consumed by the evaluator:

| Field | Type | Meaning |
| --- | --- | --- |
| `answer` | string | Final textual answer. |
| `num_turns` | integer | Agent turn count. |
| `is_error` | boolean | Marks the agent execution as an error. |
| `usage` | object | Token usage, typically `prompt_tokens`, `completion_tokens`, `total_tokens`. |
| `tool_calls` | array | Self-reported tool evidence with `step`, `tool_name`, `args`, `observation_excerpt`, and `succeeded`. |

Artifact safety rules:

- Artifact paths must be relative and may not contain `..`, backslashes, or an absolute path.
- A request accepts at most 100 files.
- Each file is limited to 25 MiB.

The response contains `attempt_id`, `job_id`, `question_id`, `session_id`, `run_id`, and `status: "grading"`.

### `GET /runs/{run_id}/attempts/{attempt_id}`

Returns durable attempt metadata for the token-owned run. It includes `question_id`, `idempotency_key`, `status`, and `created_at`.

### `GET /grading-jobs/{job_id}`

Returns durable asynchronous grading state. Authentication and job ownership are required.

```bash
while true; do
  JOB=$(curl -s "$API/grading-jobs/$JOB_ID" -H "X-API-Token: $TOKEN")
  STATUS=$(echo "$JOB" | jq -r .status)
  echo "$STATUS"
  [ "$STATUS" = "completed" ] || [ "$STATUS" = "failed" ] && break
  sleep 2
done
```

Responses include `job_id`, `attempt_id`, `run_id`, `question_id`, `status`, `error`, `created_at`, and `updated_at`.

## Results

### `GET /results/{question_id}`

Returns grading result records. Authentication is required. Pass `session_id` to restrict the result to a single session:

```bash
curl -s "$API/results/$QUESTION_ID?session_id=$SESSION" \
  -H "X-API-Token: $TOKEN" | jq .
```

While a legacy or run submission is still being graded, the endpoint returns HTTP `202`. A completed result includes pass counts, weighted score, and criterion-level reasons.

### `GET /results`

Returns the authenticated token's aggregate result summary. Optionally pass `session_id`.

## Legacy endpoints

The following session-oriented routes remain available for older clients:

- `GET /questions/{question_id}?session_id={session_id}` fetches a task and resets the legacy task timer.
- `POST /submit/{question_id}?session_id={session_id}` submits without run-level idempotency or durable job identity.

New integrations should use the run-scoped routes described above.

## Errors

Errors use FastAPI's JSON form:

```json
{"detail":"human-readable error"}
```

| Status | Typical cause |
| --- | --- |
| `400` | Missing run submission idempotency key, missing required context, malformed multipart body, or unsafe artifact path. |
| `401` | Missing or unknown `X-API-Token`. |
| `403` | Resource belongs to another token. |
| `404` | Unknown resource or question not activated in the run. |
| `410` | A question in an existing run is no longer available in the hosted catalog. |
| `413` | Too many artifacts or an artifact exceeds its file-size limit. |
| `422` | Invalid request shape or a selection that contains no questions. |