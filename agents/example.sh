#!/usr/bin/env bash
# ============================================================
# Example agent template for mat-agent-bench.
#
# Copy this file and replace the agent invocation section with
# your own agent. The harness will call this script as:
#
#   ./my_agent.sh <workspace_dir> <prompt_file> <output_file>
#
# Your script must write a JSON result to <output_file> with
# at least an "answer" field. All other fields are optional.
#
# Output JSON format:
# {
#   "answer":     "the agent's final answer text",  (required)
#   "model_name": "my-model-v1",                    (optional, defaults to script name)
#   "num_turns":  3,                                 (optional, default 0)
#   "is_error":   false,                             (optional, default false)
#   "duration_ms": 12345,                            (optional)
#   "usage": {                                       (optional)
#     "prompt_tokens": 1200,
#     "completion_tokens": 450,
#     "total_tokens": 1650
#   }
# }
#
# Environment variables can be passed via --agent-env:
#   python scripts/run_benchmark.py --agent agents/my_agent.sh \
#       --agent-env API_KEY=sk-xxx MODEL=gpt-4o
#
# ============================================================
set -euo pipefail

WORKSPACE="$1"
PROMPT_FILE="$2"
OUTPUT_FILE="$3"

PROMPT=$(cat "$PROMPT_FILE")

# ---- Replace this section with your agent invocation ----

# Example: echo a placeholder answer
ANSWER="This is a placeholder answer from the example agent."

# ---------------------------------------------------------

# Write the result JSON
cat > "$OUTPUT_FILE" <<EOF
{
  "answer": "$ANSWER",
  "model_name": "example_agent"
}
EOF
