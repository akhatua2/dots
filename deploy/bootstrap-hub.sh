#!/usr/bin/env bash
#
# One-time setup of the dots hub on a fresh Debian VM (e.g. a GCP e2-micro).
# Installs uv + git, clones the repo, syncs the slim (mlx-free) hub, and
# installs a systemd service that runs it forever (Restart=always).
#
# Usage (on the VM):
#   curl -fsSL https://raw.githubusercontent.com/akhatua2/dots/main/deploy/bootstrap-hub.sh | bash
# or, from a checkout:
#   bash deploy/bootstrap-hub.sh
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
uv sync

echo "==> installing systemd service"
sudo cp deploy/dots-hub.service /etc/systemd/system/dots-hub.service
sudo sed -i \
  -e "s|__USER__|$USER|g" \
  -e "s|__APP_DIR__|$APP_DIR|g" \
  -e "s|__HOME__|$HOME|g" \
  /etc/systemd/system/dots-hub.service
sudo systemctl daemon-reload
sudo systemctl enable --now dots-hub

echo "==> done. The hub's join ticket (share this with workers/clients):"
sleep 4
sudo journalctl -u dots-hub -n 40 --no-pager | grep -A1 "share this ticket" || \
  echo "(check 'sudo journalctl -u dots-hub -f' once it finishes starting)"
