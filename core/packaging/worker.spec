# PyInstaller spec for the dots-worker binary.
# Builds a single self-contained executable for macOS arm64.
# Run from the core/ dir: uv run --extra dev --extra mac pyinstaller packaging/worker.spec

import os

from PyInstaller.building.api import EXE, PYZ
from PyInstaller.building.build_main import Analysis

block_cipher = None

# Paths are resolved relative to the spec file (SPECPATH = core/packaging),
# so derive everything from it — works no matter the invocation cwd.
_PKG_DIR = SPECPATH                       # core/packaging
_CORE_DIR = os.path.dirname(_PKG_DIR)     # core
_SRC_DIR = os.path.join(_CORE_DIR, "src")  # core/src (the dots package)

a = Analysis(
    [os.path.join(_PKG_DIR, "worker_entry.py")],
    # src/ holds the dots package (src layout); the package is also installed
    # editably by `uv sync`, so this is belt-and-suspenders.
    pathex=[_SRC_DIR, _CORE_DIR],
    binaries=[],
    datas=[],
    hiddenimports=[
        'dots.cli',
        'dots.distributed.worker',
        'dots.distributed.hub',
        'dots.system.util',
        'dots.system.geo',
        'dots.app.api',
        'iroh',
        'mlx',
        'mlx.core',
        'mlx_lm',
        'mlx_lm.models',
        'aiohttp',
        'msgpack',
        'dotenv',
        'huggingface_hub',
        'hf_xet',
        'rich',
        'prompt_toolkit',
        'numpy',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='dots-worker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # worker output is useful to see
    disable_windowed_traceback=False,
    target_arch='arm64',
    codesign_identity=None,
    entitlements_file=None,
)
