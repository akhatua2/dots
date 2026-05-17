#!/usr/bin/env bash
# Bootstrap dots on a fresh Mac. Run this in the dots/ project directory.
set -euo pipefail

if ! command -v uv >/dev/null 2>&1; then
    echo "→ installing uv (https://github.com/astral-sh/uv)"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Add uv to PATH for the rest of this shell
    export PATH="$HOME/.local/bin:$PATH"
fi

echo "→ creating venv"
uv venv

echo "→ installing dots and dependencies"
uv pip install -e .

echo
echo "✓ dots installed."
echo
echo "Next steps:"
echo "  • Run the hub on this Mac:        .venv/bin/dots hub"
echo "  • Donate compute to a hub:        .venv/bin/dots worker HUB_HOST:5555"
echo "  • Open the chat:                  .venv/bin/dots chat HUB_HOST:5555"
echo
echo "If you and a friend are on Tailscale, HUB_HOST is the hub Mac's"
echo "tailnet IP (find it with:  tailscale ip -4 )."
