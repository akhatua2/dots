#!/usr/bin/env bash
# Run a private dots hub, one worker and the cloud on this Mac, for trying the apps end to end.
#
# It uses its own home directory (~/.dots-local), so it has its own identity and ledger and never
# touches your real ~/.dots. It starts with no join token, so the HTTP API is open on localhost.
#
#   infra/dev-local.sh start    start the hub and the cloud
#   infra/dev-local.sh app      open the Mac app so its lending switch runs a real worker against them
#   infra/dev-local.sh worker   or start a worker by hand, without the app
#   infra/dev-local.sh stop     stop everything
#   infra/dev-local.sh status   show what is running
#   infra/dev-local.sh split    serve a model that has to split across Macs, with a draft model
#
# In the Mac app's Settings, turn off "Use the mock cloud" and connect to http://127.0.0.1:8800. Then lending
# in the app starts this Mac's worker; the small model loads on the first answer.
# The hub's own OpenAI API is http://127.0.0.1:8080/v1, serving mlx-community/Qwen2.5-0.5B-Instruct-4bit.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/core/.venv/bin/python"
HOME_DIR="${DOTS_LOCAL_HOME:-$HOME/.dots-local}"
LOGS="$HOME_DIR/logs"
HTTP_PORT="${DOTS_LOCAL_HTTP_PORT:-8080}"
CLOUD_PORT="${DOTS_LOCAL_CLOUD_PORT:-8800}"
MAX_RAM_GB="${DOTS_LOCAL_MAX_RAM_GB:-6}"
# The split setup: a model bigger than one Mac's share, and a small draft with the same tokenizer
SPLIT_MODEL="${DOTS_LOCAL_SPLIT_MODEL:-mlx-community/Qwen2.5-1.5B-Instruct-4bit}"
DRAFT_MODEL="${DOTS_LOCAL_DRAFT_MODEL:-mlx-community/Qwen2.5-0.5B-Instruct-4bit}"

# Run from the home directory with the token unset: python-dotenv would otherwise find a .env
# above the working directory and bring a real join token in with it.
run() { (cd "$HOME_DIR" && env -u DOTS_JOIN_TOKEN DOTS_HOME="$HOME_DIR" "$@"); }

start() {
  [ -x "$PY" ] || { echo "No Python env yet. Run: cd core && uv sync --extra mac" >&2; exit 1; }
  mkdir -p "$LOGS"
  if pgrep -f "dots.cli hub" >/dev/null; then echo "hub already running"; else
    # The ticket carries the hub's current address; wait for this run's, not the last one's
    rm -f "$HOME_DIR/hub_ticket.txt"
    run nohup "$PY" -m dots.cli hub --http-port "$HTTP_PORT" >"$LOGS/hub.log" 2>&1 &
    for _ in $(seq 1 30); do [ -s "$HOME_DIR/hub_ticket.txt" ] && break; sleep 1; done
  fi
  if pgrep -f "dots.cli cloud" >/dev/null; then echo "cloud already running"; else
    run nohup "$PY" -m dots.cli cloud --port "$CLOUD_PORT" --hub-url "http://127.0.0.1:$HTTP_PORT" \
      >"$LOGS/cloud.log" 2>&1 &
  fi
  echo "hub:    http://127.0.0.1:$HTTP_PORT/v1   (logs in $LOGS)"
  echo "cloud:  http://127.0.0.1:$CLOUD_PORT"
  echo "model:  ${DOTS_POOL_MODEL:-mlx-community/Qwen2.5-0.5B-Instruct-4bit}"
}

worker() {
  if pgrep -f "dots.cli worker" >/dev/null; then echo "worker already running"; return; fi
  run nohup "$PY" -m dots.cli worker "$(tr -d ' \n' <"$HOME_DIR/hub_ticket.txt")" --max-ram "$MAX_RAM_GB" \
    >"$LOGS/worker.log" 2>&1 &
  echo "worker started (logs in $LOGS/worker.log)"
}

# Opens the Mac app with what it needs to run a real worker: this home, and the engine's worker command.
app() {
  local bundle="$ROOT/apps/macos/build/Build/Products/Debug/dots.app"
  [ -d "$bundle" ] || { echo "Build the app first (see docs/development.md)" >&2; exit 1; }
  # Quit a running copy first, politely so it stops its worker, and wait for it to go.
  osascript -e 'tell application "dots" to quit' >/dev/null 2>&1 || true
  for _ in $(seq 1 50); do pgrep -x dots >/dev/null || break; sleep 0.2; done
  pkill -x dots || true
  open --env "DOTS_HOME=$HOME_DIR" --env "DOTS_WORKER=$PY -m dots.cli worker" \
    --env "DOTS_DRAFT_MODEL=${DOTS_DRAFT_MODEL:-}" "$bundle"
}

# A model too big for one Mac's share, with speculative decoding on, to see a pool split across Macs.
# The hub keeps less headroom on the Mac running the head than in production, so Macs lending about
# 1 GB each can hold their parts.
split() {
  stop
  export DOTS_POOL_MODEL="$SPLIT_MODEL" DOTS_DRAFT_MODEL="$DRAFT_MODEL"
  export DOTS_PLACEMENT_RAM_MARGIN_GB=0.1 DOTS_HEAD_WORKSPACE_RAM_GB=0.2
  start
  app
  echo "Lend about 1 GB on each Mac so $SPLIT_MODEL has to split, then ask something."
}

stop() {
  pkill -f "dots.cli cloud" || true
  pkill -f "dots.cli worker" || true
  pkill -f "dots.cli hub" || true
  echo "stopped"
}

status() {
  pgrep -fl "dots.cli (hub|worker|cloud)" || echo "not running"
  curl -s -m 3 "http://127.0.0.1:$HTTP_PORT/healthz" && echo || true
  curl -s -m 3 "http://127.0.0.1:$CLOUD_PORT/healthz" && echo || true
}

case "${1:-}" in
  start) start ;;
  app) app ;;
  worker) worker ;;
  stop) stop ;;
  status) status ;;
  split) split ;;
  *) echo "usage: $0 start|app|worker|stop|status|split" >&2; exit 2 ;;
esac
