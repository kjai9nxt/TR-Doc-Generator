#!/usr/bin/env bash
# One command to run the whole app: backend (FastAPI) + frontend (Vite).
# Usage:  ./start.sh        (from the project root)
# Stop:   press Ctrl+C  (both servers shut down together)

set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# Kill any stale backend from a previous run so the new code takes over port 8000.
echo "▶ Clearing any old backend on port 8000 ..."
pkill -f "python3 server.py" 2>/dev/null || true
pkill -f "server.py" 2>/dev/null || true
# also free the port directly if something else grabbed it
fuser -k 8000/tcp 2>/dev/null || true
sleep 1

echo "▶ Starting backend API (http://127.0.0.1:8000) ..."
python3 server.py > logs/backend.log 2>&1 &
BACKEND_PID=$!

# stop the backend whenever this script exits (Ctrl+C, etc.)
cleanup() {
  echo ""
  echo "■ Shutting down ..."
  kill "$BACKEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# wait for the backend to answer before starting the UI
echo "  waiting for the API to be ready ..."
for i in $(seq 1 20); do
  if curl -s -o /dev/null http://127.0.0.1:8000/api/status; then
    echo "  ✓ backend is up."
    break
  fi
  sleep 0.5
done

# install frontend deps on first run
if [ ! -d "frontend/node_modules" ]; then
  echo "▶ Installing frontend dependencies (first run only) ..."
  (cd frontend && npm install)
fi

echo "▶ Starting frontend (Vite). Open the URL it prints below (usually http://localhost:5173)."
echo "-------------------------------------------------------------------"
cd frontend
npm run dev
