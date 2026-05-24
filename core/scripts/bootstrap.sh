#!/usr/bin/env bash
# Bootstrap the dots engine on a fresh Mac. Safe to run from anywhere — it
# cd's into the core/ project dir (where pyproject.toml lives) on its own.
set -euo pipefail

CORE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$CORE_DIR"

if ! command -v uv >/dev/null 2>&1; then
    echo "→ installing uv (https://github.com/astral-sh/uv)"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Add uv to PATH for the rest of this shell
    export PATH="$HOME/.local/bin:$PATH"
fi

echo "→ installing dots + the Apple-silicon (mlx) compute backend"
# --extra mac pulls mlx/mlx-lm so this Mac can run model layers as a worker.
uv sync --extra mac

echo
echo "dots installed (in $CORE_DIR)."
echo
echo "Next steps — the hub address is baked in, no host:port needed:"
echo "  • Open the chat (auto-donates compute):   uv run dots chat"
echo "  • Donate compute only:                    uv run dots worker"
echo "  • Run your own hub:                        uv run dots hub --http-port 8080"
