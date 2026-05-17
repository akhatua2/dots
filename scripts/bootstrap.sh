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

ip=$(ipconfig getifaddr en0 2>/dev/null || echo "<your-wifi-ip>")
echo
echo "✓ dots installed. To start the worker:"
echo
echo "    .venv/bin/dots serve --port 5555"
echo
echo "Then tell the coordinator your address:  ${ip}:5555"
