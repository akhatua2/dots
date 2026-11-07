# PyInstaller spec for the dots-worker binary.
# Builds a single self-contained executable for macOS arm64.
# Run from the core/ dir: uv run --extra dev --extra mac pyinstaller packaging/worker.spec

import os

from PyInstaller.building.api import EXE, PYZ
from PyInstaller.building.build_main import Analysis
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

block_cipher = None

# Paths are resolved relative to the spec file (SPECPATH = core/packaging),
# so derive everything from it — works no matter the invocation cwd.
_PKG_DIR = SPECPATH                       # core/packaging
_CORE_DIR = os.path.dirname(_PKG_DIR)     # core
_SRC_DIR = os.path.join(_CORE_DIR, "src")  # core/src (the dots package)

def _mlx_kernels():
    """MLX's Metal kernels, at the top of the bundle: the packaged libmlx is loaded from there (through the link
    PyInstaller makes to it), and MLX looks for mlx.metallib beside the library it loaded"""
    kernels = [(src, dest) for src, dest in collect_data_files("mlx") if src.endswith("mlx.metallib")]
    return kernels + [(src, ".") for src, _ in kernels]


a = Analysis(
    [os.path.join(_PKG_DIR, "worker_entry.py")],
    # src/ holds the dots package (src layout); the package is also installed
    # editably by `uv sync`, so this is belt-and-suspenders.
    pathex=[_SRC_DIR, _CORE_DIR],
    # iroh's networking library, loaded by its bindings at run time; MLX's, kept in mlx/lib beside the Metal kernels
    # (mlx.metallib) it loads from there
    binaries=collect_dynamic_libs("iroh") + collect_dynamic_libs("mlx"),
    # How a Mac draws each kind of image model (image_tools/runners.toml), and dots' own drawing driver; MLX's
    # Metal kernels
    datas=collect_data_files("dots.image_tools", include_py_files=True) + _mlx_kernels(),
    # The engine loads a model's code by its architecture's name, so every architecture goes in
    hiddenimports=collect_submodules("mlx") + collect_submodules("mlx_lm") + collect_submodules("dots") + [
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
