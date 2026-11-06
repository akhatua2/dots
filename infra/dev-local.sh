#!/usr/bin/env bash
# Run a private dots hub and one worker on this Mac, for trying the apps end to end.
#
# It uses its own home directory (~/.dots-local), so it has its own identity and ledger and never
# touches your real ~/.dots. It starts with no join token, so the HTTP API is open on localhost.
#
#   infra/dev-local.sh start    start the hub and a worker (the small Qwen model loads on the first request)
#   infra/dev-local.sh stop     stop both
#   infra/dev-local.sh status   show what is running
#
# Point an app at http://127.0.0.1:8080/v1 and the model mlx-community/Qwen2.5-0.5B-Instruct-4bit.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/core/.venv/bin/python"
HOME_DIR="${DOTS_LOCAL_HOME:-$HOME/.dots-local}"
LOGS="$HOME_DIR/logs"
HTTP_PORT="${DOTS_LOCAL_HTTP_PORT:-8080}"
MAX_RAM_GB="${DOTS_LOCAL_MAX_RAM_GB:-6}"

# Run from the home directory with the token unset: python-dotenv would otherwise find a .env
# above the working directory and bring a real join token in with it.
run() { (cd "$HOME_DIR" && env -u DOTS_JOIN_TOKEN DOTS_HOME="$HOME_DIR" "$@"); }

start() {
  [ -x "$PY" ] || { echo "No Python env yet. Run: cd core && uv sync --extra mac" >&2; exit 1; }
  mkdir -p "$LOGS"
  if pgrep -f "dots.cli hub" >/dev/null; then echo "hub already running"; else
    run nohup "$PY" -m dots.cli hub --http-port "$HTTP_PORT" >"$LOGS/hub.log" 2>&1 &
    for _ in $(seq 1 30); do [ -s "$HOME_DIR/hub_ticket.txt" ] && break; sleep 1; done
  fi
  if pgrep -f "dots.cli worker" >/dev/null; then echo "worker already running"; else
    run nohup "$PY" -m dots.cli worker "$(tr -d ' \n' <"$HOME_DIR/hub_ticket.txt")" --max-ram "$MAX_RAM_GB" \
      >"$LOGS/worker.log" 2>&1 &
  fi
  echo "hub:    http://127.0.0.1:$HTTP_PORT/v1   (logs in $LOGS)"
  echo "model:  mlx-community/Qwen2.5-0.5B-Instruct-4bit"
}

stop() { pkill -f "dots.cli worker" || true; pkill -f "dots.cli hub" || true; echo "stopped"; }

status() {
  pgrep -fl "dots.cli (hub|worker)" || echo "not running"
  curl -s -m 3 "http://127.0.0.1:$HTTP_PORT/healthz" && echo || true
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  *) echo "usage: $0 start|stop|status" >&2; exit 2 ;;
esac
