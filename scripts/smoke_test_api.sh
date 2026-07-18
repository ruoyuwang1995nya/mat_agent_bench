#!/usr/bin/env bash
# Exercise the documented run-scoped benchmark API without an external agent.
#
# Usage:
#   TOKEN=<api-token> ./scripts/smoke_test_api.sh
#
# Optional environment variables:
#   API          Benchmark API base URL (default: http://127.0.0.1:8080/bench)
#   QUESTION_ID  Hosted question ID (default: SR_db_001_20260615v2)
#   MODEL_NAME   Model label recorded in the session (default: api-smoke-test)
#   POLL_SECONDS Delay between grading-job polls (default: 2)
#   POLL_LIMIT   Maximum grading-job polls (default: 60)

set -euo pipefail

API="${API:-http://127.0.0.1:8080/bench}"
TOKEN="${TOKEN:?Set TOKEN to an API token issued through the benchmark UI.}"
QUESTION_ID="${QUESTION_ID:-SR_db_001_20260615v2}"
MODEL_NAME="${MODEL_NAME:-api-smoke-test}"
POLL_SECONDS="${POLL_SECONDS:-2}"
POLL_LIMIT="${POLL_LIMIT:-60}"

for command in curl jq; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Error: $command is required." >&2
        exit 1
    }
done

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

request_id="$(python -c 'import uuid; print(uuid.uuid4())')"
auth=(-H "X-API-Token: $TOKEN")

echo "Checking server and question catalog..."
curl -fsS "$API/questions?limit=1" >/dev/null
curl -fsS "$API/questions" | jq -e --arg id "$QUESTION_ID" \
    'any(.[]; .id == $id)' >/dev/null || {
    echo "Error: question $QUESTION_ID is not hosted by $API." >&2
    exit 1
}

echo "Creating session..."
SESSION="$({
    curl -fsS -X POST "$API/sessions" "${auth[@]}" \
        -H "Content-Type: application/json" \
        --data "$(jq -nc --arg model "$MODEL_NAME" '{model_name: $model}')"
} | jq -er '.session_id')"

echo "Creating one-question run for $QUESTION_ID..."
RUN="$({
    curl -fsS -X POST "$API/runs?session_id=$SESSION" "${auth[@]}" \
        -H "Content-Type: application/json" \
        --data "$(jq -nc --arg id "$QUESTION_ID" '{question_ids: [$id]}')"
} | jq -er '.run_id')"

echo "Fetching activated task..."
TASK="$WORKDIR/task.json"
curl -fsS "$API/runs/$RUN/tasks/$QUESTION_ID" "${auth[@]}" >"$TASK"
jq -e --arg id "$QUESTION_ID" '.question_id == $id and (.prompt | length > 0)' "$TASK" >/dev/null

# These are deliberate mock artifacts for the default retrieval question. They
# validate multipart artifact upload and evaluator access, not scientific accuracy.
cat >"$WORKDIR/retrieval_report.md" <<'EOF'
# Mock retrieval report

This smoke test did not query a materials database. It submits deterministic
artifacts only to validate the benchmark API submission and grading lifecycle.
EOF

cat >"$WORKDIR/source_summary.json" <<'EOF'
{
  "query_formula": "RbClO4",
  "selected_formula": "RbClO4",
  "database_source": "mock smoke-test source",
  "structure_identifier": "SMOKE-TEST",
  "candidate_count": 0,
  "space_group": "unknown"
}
EOF

META="$(jq -nc \
    --arg answer "Mock API smoke test submission; no scientific retrieval was performed." \
    '{answer: $answer, num_turns: 1, is_error: false,
      usage: {prompt_tokens: 0, completion_tokens: 0, total_tokens: 0},
      tool_calls: [{step: 1, tool_name: "api-smoke-test", args: {},
                    observation_excerpt: "Created deterministic mock artifacts.", succeeded: true}]}')"

echo "Submitting mock result..."
SUBMISSION_FILE="$WORKDIR/submission.json"
if ! SUBMISSION_STATUS="$(curl -sS -o "$SUBMISSION_FILE" -w '%{http_code}' \
    -X POST "$API/submit/$QUESTION_ID?run_id=$RUN" "${auth[@]}" \
        -H "Idempotency-Key: $request_id" \
        --form-string "meta=$META" \
        -F "retrieval_report=@$WORKDIR/retrieval_report.md" \
        -F "source_summary=@$WORKDIR/source_summary.json")"; then
    echo "Error: unable to reach submission endpoint." >&2
    exit 1
fi
if [[ "$SUBMISSION_STATUS" != 2* ]]; then
    echo "Error: submission failed with HTTP $SUBMISSION_STATUS." >&2
    cat "$SUBMISSION_FILE" >&2
    exit 1
fi
SUBMISSION="$(<"$SUBMISSION_FILE")"
ATTEMPT="$(jq -er '.attempt_id' <<<"$SUBMISSION")"
JOB="$(jq -er '.job_id' <<<"$SUBMISSION")"

echo "Run:     $RUN"
echo "Attempt: $ATTEMPT"
echo "Job:     $JOB"
echo "Polling grading job..."

for _ in $(seq 1 "$POLL_LIMIT"); do
    JOB_RESPONSE="$(curl -fsS "$API/grading-jobs/$JOB" "${auth[@]}")"
    STATUS="$(jq -er '.status' <<<"$JOB_RESPONSE")"
    echo "  $STATUS"
    case "$STATUS" in
        completed)
            break
            ;;
        failed)
            jq . <<<"$JOB_RESPONSE" >&2
            echo "Error: grading job failed." >&2
            exit 1
            ;;
    esac
    sleep "$POLL_SECONDS"
done

if [ "$STATUS" != "completed" ]; then
    echo "Error: grading did not finish after $POLL_LIMIT polls." >&2
    exit 1
fi

echo "Fetching evaluation result..."
RESULT="$(curl -fsS "$API/results/$QUESTION_ID?run_id=$RUN" "${auth[@]}")"
echo "$RESULT" | jq '.[0] | {
    question_id,
    run_status,
    passed,
    passed_count,
    total_count,
    overall_weighted_score
}'

echo "Smoke test passed: API discovery, session, run, task, submission, job, and result retrieval all succeeded."