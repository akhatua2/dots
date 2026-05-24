#!/usr/bin/env bash
#
# One-time setup of the dots hub on a fresh Debian VM (e.g. a GCP e2-micro).
# Installs uv + git, clones the repo, syncs the slim (mlx-free) hub, and
# installs a systemd service that runs it forever (Restart=always).
#
# Usage (on the VM):
#   curl -fsSL https://raw.githubusercontent.com/akhatua2/dots/main/infra/bootstrap-hub.sh | bash
# or, from a checkout:
#   bash infra/bootstrap-hub.sh
#
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/akhatua2/dots.git}"
APP_DIR="${APP_DIR:-$HOME/dots}"
BRANCH="${BRANCH:-main}"

echo "==> installing system deps (git, curl)"
sudo apt-get update -y
sudo apt-get install -y git curl

echo "==> installing uv"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

echo "==> cloning / updating $REPO_URL at $APP_DIR"
if [ ! -d "$APP_DIR/.git" ]; then
  git clone "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"
git fetch origin
git checkout "$BRANCH"
git reset --hard "origin/$BRANCH"

echo "==> uv sync (hub only — no 'mac' extra, so no mlx)"
# pyproject.toml lives in core/ — sync and run the hub from there.
(cd "$APP_DIR/core" && uv sync)

echo "==> installing systemd service"
sudo cp "$APP_DIR/infra/dots-hub.service" /etc/systemd/system/dots-hub.service
sudo sed -i \
  -e "s|__USER__|$USER|g" \
  -e "s|__APP_DIR__|$APP_DIR/core|g" \
  -e "s|__HOME__|$HOME|g" \
  /etc/systemd/system/dots-hub.service
sudo systemctl daemon-reload
sudo systemctl enable --now dots-hub

echo "==> waiting for the hub to come up and publish its ticket…"
TICKET_FILE="$HOME/.dots/hub_ticket.txt"
for _ in $(seq 1 40); do
  [ -s "$TICKET_FILE" ] && break
  sleep 1
done
if [ -s "$TICKET_FILE" ]; then
  echo "==> hub is up. Join ticket (share with workers/clients):"
  echo
  cat "$TICKET_FILE"
  echo
  echo "Workers join with:  dots worker <TICKET>"
  echo "Chat with:          dots chat <TICKET>"
else
  echo "==> ticket not found yet; check: sudo journalctl -u dots-hub -f"
fi
