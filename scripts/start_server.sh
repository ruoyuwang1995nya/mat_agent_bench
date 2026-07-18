#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

PORT="${1:-8080}"
HOST="${2:-127.0.0.1}"
CHECKLIST_WORKERS="${3:-4}"

cleanup() {
    echo "Shutting down server..."
    kill "${SERVER_PID:-}" 2>/dev/null || true
    wait "${SERVER_PID:-}" 2>/dev/null || true
}
trap cleanup INT TERM

kill_port() {
    local port="$1"
    local pid
    pid=$(netstat -tlnp 2>/dev/null | awk -v port=":$port" '$4 ~ port"$" {split($7,a,"/"); print a[1]}' || true)
    if [ -n "$pid" ]; then
        echo "Killing process $pid on port $port..."
        kill "$pid" 2>/dev/null || true
        sleep 1
    fi
}

cd "$ROOT_DIR"

kill_port "$PORT"

echo "Starting mat-bench on port $PORT..."
nohup mat-bench serve --host "$HOST" --port "$PORT" --parallel-checklist-workers "$CHECKLIST_WORKERS" >"$ROOT_DIR/server.log" 2>&1 &
SERVER_PID=$!

echo "Waiting for server to be ready..."
for i in $(seq 1 20); do
    if curl -sf "http://${HOST}:${PORT}/bench/questions" >/dev/null 2>&1; then
        echo "Server is ready."
        break
    fi
    sleep 1
    if [ "$i" -eq 20 ]; then
        echo "Warning: server did not respond after 20s."
    fi
done

echo "Server is running."
echo "  UI:      http://${HOST}:${PORT}/"
echo "  API:     http://${HOST}:${PORT}/bench"
echo "  API docs: http://${HOST}:${PORT}/bench/docs"
echo "  API log: ~/.matbench/logs/api-server.log"
echo "  Console capture: server.log"
echo "Startup complete. This script will now exit."
