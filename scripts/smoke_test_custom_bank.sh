#!/usr/bin/env bash
# Exercise the custom question-bank API against a running mat-bench server.
#
# Verifies: creating a custom bank, adding a question to it (with a data
# file), and confirming the bank/question are visible via the read endpoints.
#
# Usage:
#   TOKEN=<api-token> ./scripts/smoke_test_custom_bank.sh
#
# The server must be started with --allow-bank-management (and a
# --store-dir) for the write endpoints to be enabled, e.g.:
#   mat-bench serve --question-bank-dir ./question_bank --store-dir ./store \
#       --allow-bank-management --allow-token-registration
#
# Optional environment variables:
#   API   Benchmark API base URL (default: http://127.0.0.1:8080/bench)
#   TOKEN API token issued through the benchmark UI or --allow-token-registration.
#         If unset, the script will try POST $API/token to mint one.

set -euo pipefail

API="${API:-http://127.0.0.1:8080/bench}"

for command in curl jq; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Error: $command is required." >&2
        exit 1
    }
done

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

if [ -z "${TOKEN:-}" ]; then
    echo "TOKEN not set; attempting to mint one via POST $API/token..."
    TOKEN="$(curl -fsS -X POST "$API/token" | jq -er '.token')" || {
        echo "Error: could not mint a token. Start the server with" \
             "--allow-token-registration, or set TOKEN yourself." >&2
        exit 1
    }
    echo "Minted token: $TOKEN"
fi

auth=(-H "X-API-Token: $TOKEN")
suffix="$(python -c 'import uuid; print(uuid.uuid4().hex[:8])')"
BANK_ID="smoke-test-bank-$suffix"
QUESTION_ID="smoke_q_${suffix}"

echo "Listing question banks before creation..."
curl -fsS "$API/question-banks" "${auth[@]}" | jq .

echo "Creating custom question bank '$BANK_ID'..."
BANK="$(curl -fsS -X POST "$API/question-banks" "${auth[@]}" \
    -H "Content-Type: application/json" \
    --data "$(jq -nc --arg id "$BANK_ID" --arg name "Smoke Test Bank" \
        --arg desc "Created by scripts/smoke_test_custom_bank.sh" \
        '{bank_id: $id, name: $name, description: $desc}')")"
echo "$BANK" | jq .
jq -e --arg id "$BANK_ID" '.bank_id == $id' <<<"$BANK" >/dev/null || {
    echo "Error: created bank id does not match requested id." >&2
    exit 1
}

echo "Preparing a sample question and data file..."
cat >"$WORKDIR/notes.txt" <<'EOF'
Sample data file attached to a smoke-test question.
EOF

QUESTION_JSON="$(jq -nc --arg id "$QUESTION_ID" '{
    id: $id,
    task_type: "search_and_interpretation",
    capabilities: ["scientific_reasoning"],
    domain: "agnostic",
    difficulty: "easy",
    intent: "Smoke-test question created to verify custom question-bank creation.",
    human_prompt_seed: "This is a placeholder prompt used only for API smoke testing.",
    data_files: [{key: "notes", path: "notes.txt", description: "sample attachment"}],
    scoring_checklist: [{id: "sc1", criterion: "Placeholder criterion for smoke testing.", verify: "llm_binary_judge", weight: 1.0}]
}')"

echo "Adding question '$QUESTION_ID' to bank '$BANK_ID'..."
QUESTION_RESPONSE="$(curl -fsS -X POST "$API/question-banks/$BANK_ID/questions" "${auth[@]}" \
    --form-string "question=$QUESTION_JSON" \
    -F "notes.txt=@$WORKDIR/notes.txt")"
echo "$QUESTION_RESPONSE" | jq .
jq -e --arg id "$QUESTION_ID" '.id == $id' <<<"$QUESTION_RESPONSE" >/dev/null || {
    echo "Error: added question id does not match requested id." >&2
    exit 1
}

echo "Fetching bank metadata to confirm the question is registered..."
BANK_META="$(curl -fsS "$API/question-banks/$BANK_ID" "${auth[@]}")"
echo "$BANK_META" | jq .
jq -e --arg id "$QUESTION_ID" '.question_ids | index($id) != null' <<<"$BANK_META" >/dev/null || {
    echo "Error: question $QUESTION_ID not found in bank $BANK_ID." >&2
    exit 1
}

echo "Creating a session to fetch full question detail..."
SESSION="$(curl -fsS -X POST "$API/sessions" "${auth[@]}" \
    -H "Content-Type: application/json" \
    --data '{"model_name": "smoke-test-custom-bank"}' | jq -er '.session_id')"

echo "Fetching question detail via /questions/{id}?session_id=..."
curl -fsS "$API/questions/$QUESTION_ID?session_id=$SESSION" | jq .

echo "Smoke test passed: custom bank creation and question upload both succeeded."
echo "Bank:     $BANK_ID"
echo "Question: $QUESTION_ID"
