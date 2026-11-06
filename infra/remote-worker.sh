#!/usr/bin/env bash
# Start a dots worker on another Mac over SSH, joined to the local hub (infra/dev-local.sh).
#
#   infra/remote-worker.sh user@other-mac.local [GB]    start it, lending GB (default 1)
#   infra/remote-worker.sh user@other-mac.local stop    stop it
#
# Needs Remote Login on the other Mac and this Mac's key there (ssh-copy-id). It copies the engine as a
# wheel, gets uv there if it's missing, and runs the worker in ~/.dots-test, logging to worker.log there.
# Nothing is installed system-wide; `stop` ends the worker, and removing ~/.dots-test cleans up.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOME_DIR="${DOTS_LOCAL_HOME:-$HOME/.dots-local}"
HOST="${1:?usage: $0 user@host [GB|stop]}"
ARG="${2:-1}"
REMOTE_HOME='~/.dots-test'

if [ "$ARG" = stop ]; then
  ssh "$HOST" "pkill -f 'dots worker' && echo stopped || echo 'no worker running'"
  exit 0
fi

dist="$(mktemp -d)"
(cd "$ROOT/core" && uv build --wheel -q -o "$dist")
wheel="$(basename "$dist"/dots-*.whl)"
ticket="$(tr -d ' \n' <"$HOME_DIR/hub_ticket.txt")"

ssh "$HOST" "mkdir -p $REMOTE_HOME"
scp -q "$dist/$wheel" "$HOST:$REMOTE_HOME/"
ssh "$HOST" bash -s -- "$wheel" "$ticket" "$ARG" "${DOTS_DRAFT_MODEL:-}" <<'REMOTE'
set -e
wheel="$1" ticket="$2" gb="$3" draft="$4"
export PATH="$HOME/.local/bin:$PATH"
command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
cd ~/.dots-test
pkill -f 'dots worker' || true
DOTS_HOME="$HOME/.dots-test" DOTS_DRAFT_MODEL="$draft" nohup uvx --python 3.11 \
  --from "dots[mac] @ file://$HOME/.dots-test/$wheel" dots worker "$ticket" --max-ram "$gb" \
  >worker.log 2>&1 </dev/null &
echo "worker started on $(scutil --get ComputerName), lending $gb GB (log: ~/.dots-test/worker.log)"
REMOTE
rm -rf "$dist"
