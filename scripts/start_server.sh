#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

BACKEND_PORT="${1:-8765}"
FRONTEND_PORT="${2:-8080}"
HOST="${3:-0.0.0.0}"
SERVER_URL="${4:-http://${HOST}:${BACKEND_PORT}}"

cleanup() {
    echo "Shutting down servers..."
    kill "${BACKEND_PID:-}" "${FRONTEND_PID:-}" 2>/dev/null || true
    wait "${BACKEND_PID:-}" "${FRONTEND_PID:-}" 2>/dev/null || true
}
trap cleanup INT TERM

cd "$ROOT_DIR"

echo "Starting backend server on port $BACKEND_PORT..."
nohup mat-bench serve --host "$HOST" --port "$BACKEND_PORT" >"$ROOT_DIR/backend.log" 2>&1 &
BACKEND_PID=$!

echo "Waiting for backend to be ready..."
for i in $(seq 1 20); do
    if curl -sf "http://${HOST}:${BACKEND_PORT}/questions" >/dev/null 2>&1; then
        echo "Backend is ready."
        break
    fi
    sleep 1
    if [ "$i" -eq 20 ]; then
        echo "Warning: backend did not respond after 20s, starting frontend anyway."
    fi
done

echo "Starting frontend server on port $FRONTEND_PORT..."
nohup mat-bench serve-ui --host "$HOST" --port "$FRONTEND_PORT" --backend-url "$SERVER_URL" >"$ROOT_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!

echo "Both servers are running."
echo "  Backend:  http://${HOST}:${BACKEND_PORT}  (log: backend.log)"
echo "  Frontend: http://${HOST}:${FRONTEND_PORT}  (log: frontend.log)"
echo "Startup complete. This script will now exit."
