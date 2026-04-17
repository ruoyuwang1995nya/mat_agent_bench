#!/usr/bin/env bash
# Agent script for Claude Code (claude CLI).
#
# Contract: ./claude_code.sh <workspace_dir> <prompt_file> <output_file>
#
# Environment variables (optional):
#   MODEL      - Claude model name (e.g. opus, sonnet, claude-opus-4-6)
#   MAX_TURNS  - Max conversation turns (default: 50)
#
set -euo pipefail

WORKSPACE="$1"
PROMPT_FILE="$2"
OUTPUT_FILE="$3"

PROMPT=$(cat "$PROMPT_FILE")
RAW_OUTPUT="$WORKSPACE/_claude_raw.json"

# Run claude in non-interactive mode
claude -p "$PROMPT" \
    --output-format json \
    --dangerously-skip-permissions \
    --max-turns "${MAX_TURNS:-50}" \
    --bare \
    ${MODEL:+--model "$MODEL"} \
    > "$RAW_OUTPUT" 2>/dev/null || true

# Parse claude JSON output into the standard agent output format
python3 -c "
import json, sys

raw_path = '$RAW_OUTPUT'
output_path = '$OUTPUT_FILE'
model_default = '${MODEL:-claude_code}'

# Parse the last JSON object from stdout (shell noise may precede it)
raw = None
try:
    with open(raw_path) as f:
        text = f.read().strip()
    if text:
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith('{'):
                try:
                    raw = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
except FileNotFoundError:
    pass

if raw is None:
    raw = {'is_error': True, 'result': 'no JSON output from claude'}

# Extract model name from modelUsage
model_name = model_default
mu = raw.get('modelUsage')
if isinstance(mu, dict) and mu:
    name = next(iter(mu))
    if '.' in name:
        for part in name.split('.'):
            if part.startswith('claude'):
                name = part
                break
    for sep in ('-v1', '-v2', '['):
        if sep in name:
            name = name[:name.index(sep)]
            break
    model_name = name

# Build usage from raw
raw_usage = raw.get('usage') or {}
input_tokens = int(raw_usage.get('input_tokens') or 0)
cache_creation = int(raw_usage.get('cache_creation_input_tokens') or 0)
cache_read = int(raw_usage.get('cache_read_input_tokens') or 0)
output_tokens = int(raw_usage.get('output_tokens') or 0)
prompt_tokens = input_tokens + cache_creation + cache_read

result = {
    'answer': str(raw.get('result', '')),
    'model_name': model_name,
    'num_turns': raw.get('num_turns', 0),
    'is_error': raw.get('is_error', False),
    'duration_ms': raw.get('duration_ms', 0),
    'usage': {
        'prompt_tokens': prompt_tokens,
        'completion_tokens': output_tokens,
        'total_tokens': prompt_tokens + output_tokens,
        'cache_read_input_tokens': cache_read,
    },
}

with open(output_path, 'w') as f:
    json.dump(result, f, ensure_ascii=False)
"
