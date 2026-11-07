#!/usr/bin/env bash
# Builds stable-diffusion.cpp's sd-cli into ~/.dots/tools, for Macs that MLX doesn't run on (macOS 13): there a
# worker draws FLUX.2 klein with it instead of mflux (core/src/dots/image_tools/runners.toml).
#
#   infra/sdcpp.sh            build the pinned release, with Metal
#
# It needs Apple's command-line tools (xcode-select --install) and uv, which brings cmake. The source is pinned to
# a release; the model files come from Hugging Face the first time the Mac draws (about 5.3 GB).
set -euo pipefail

REF="${DOTS_SDCPP_REF:-master-1053-5a1ba5d}"
HOME_DIR="${DOTS_HOME:-$HOME/.dots}"
SRC="$HOME_DIR/tools/src/stable-diffusion.cpp"

xcode-select -p >/dev/null 2>&1 || { echo "Install Apple's command-line tools first: xcode-select --install" >&2; exit 1; }
command -v uvx >/dev/null || { echo "Install uv first: https://docs.astral.sh/uv/" >&2; exit 1; }

mkdir -p "$HOME_DIR/tools/src"
if [ ! -d "$SRC/.git" ]; then
  git clone --depth 1 --branch "$REF" --recurse-submodules --shallow-submodules \
    https://github.com/leejet/stable-diffusion.cpp "$SRC"
fi
cd "$SRC"
uvx cmake -B build -DSD_METAL=ON -DCMAKE_BUILD_TYPE=Release
uvx cmake --build build --config Release -j "$(sysctl -n hw.ncpu)" --target sd-cli
cp build/bin/sd-cli "$HOME_DIR/tools/sd-cli"
echo "built $HOME_DIR/tools/sd-cli ($REF)"
