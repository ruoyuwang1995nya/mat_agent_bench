#!/usr/bin/env bash
# Run a single mat-bench question against the harness-less server.
#
# Usage:
#   ./scripts/run_question.sh <question_id> [server_url]
#
# Examples:
#   ./scripts/run_question.sh SR_db_001_20260411v2
#   ./scripts/run_question.sh SR_db_001_20260411v2 http://127.0.0.1:8765
#
# Environment variables (optional):
#   MODEL         Claude model (e.g. claude-opus-4-6, claude-sonnet-4-6)
#   MAX_TURNS     Max agent turns (default: 50)
#   SERVER_URL    Overrides the server_url argument
#   TOKEN         API token (auto-registered if not set)
#   SESSION       Session ID (auto-created if not set)

set -euo pipefail

QUESTION_ID="${1:?Usage: $0 <question_id> [server_url]}"
SERVER_URL="${2:-${SERVER_URL:-http://127.0.0.1:8765}}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
TEMPLATE="$REPO_ROOT/agents/run_question.md"

# Require a token — registration is not allowed here
if [ -z "${TOKEN:-}" ]; then
  echo "Error: TOKEN environment variable is not set. Export a valid API token first." >&2
  exit 1
fi
echo "Using token:      $TOKEN" >&2

# Create a session if not provided
if [ -z "${SESSION:-}" ]; then
  SESSION=$(curl -sf -X POST "$SERVER_URL/sessions" -H "X-API-Token: $TOKEN" | jq -r .session_id)
  echo "Created session:  $SESSION" >&2
else
  echo "Using session:    $SESSION" >&2
fi

# Fill in the template
PROMPT=$(sed \
  -e "s|{QUESTION_ID}|$QUESTION_ID|g" \
  -e "s|{SERVER_URL}|$SERVER_URL|g" \
  -e "s|{TOKEN}|$TOKEN|g" \
  -e "s|{SESSION}|$SESSION|g" \
  "$TEMPLATE")

echo "Running question: $QUESTION_ID" >&2
echo "Server:           $SERVER_URL" >&2
echo "Model:            ${MODEL:-default}" >&2
echo "" >&2

claude \
  -p "$PROMPT" \
  --dangerously-skip-permissions \
  --verbose \
  --max-turns "${MAX_TURNS:-50}" \
  ${MODEL:+--model "$MODEL"}

echo "" >&2
echo "Done. Check scores: curl -H \"X-API-Token: $TOKEN\" \"$SERVER_URL/results?session_id=$SESSION\"" >&2
