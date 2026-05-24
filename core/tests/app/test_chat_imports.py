"""Tests for the relative imports in `dots.app.chat`.

The chat package is nested under `dots.app`, so references outside the
package need the correct relative import depth. A wrong-level import is
easy to miss — it parses fine,
the test suite happily collects, and the bug only fires when the
chat_processor task hits the lazy import on the first prompt.

When that happened, the chat_processor coroutine raised
`ImportError`, the task died silently, and the chat UI sat in
"ctrl-c to interrupt" state forever — prompts queued in
`prompt_queue` but no one consuming them, no error visible to the
user, no log on the hub.

Walk every relative import in `dots/app/chat/__init__.py` and verify
each one actually resolves at runtime. Catches both runtime imports
and TYPE_CHECKING imports identically.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest


def _iter_relative_imports(path: Path):
    """Yield (full_module_name, [imported_names]) for each
    `from .X import Y, Z` style statement in `path`. Top-level
    package is hard-coded as `dots.app.chat` since that's what's
    being tested."""
    src = path.read_text()
    tree = ast.parse(src)
    package_prefix = "dots.app.chat"
    package_parts = package_prefix.split(".")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level > 0:
            # Each `.` in `from ...X` corresponds to one level up.
            # level=1 means "from .X" (same package).
            # level=2 means "from ..X" (parent package).
            base_parts = package_parts[: len(package_parts) - node.level + 1]
            if node.module:
                target = ".".join(base_parts + node.module.split("."))
            else:
                target = ".".join(base_parts)
            names = [a.name for a in node.names]
            yield target, names


def test_chat_init_relative_imports_resolve():
    """The bug this catches:

        from .worker import PoolNotReadyError

    silently parses and only crashes at runtime when the chat
    processor first dereferences PoolNotReadyError. Walk every
    `from .` / `from ..` import in dots/app/chat/__init__.py and verify
    both the module exists AND every imported name is actually
    present on it.
    """
    # Locate the file via the installed package so this is layout-agnostic
    # (src/ layout, flat, whatever) rather than guessing a path from the repo.
    import dots.app.chat as chat_pkg

    chat_init = Path(chat_pkg.__file__)
    failures = []
    for module_name, names in _iter_relative_imports(chat_init):
        try:
            mod = importlib.import_module(module_name)
        except ImportError as e:
            failures.append(f"module {module_name!r} not importable: {e}")
            continue
        for name in names:
            if not hasattr(mod, name):
                failures.append(
                    f"{module_name}.{name} doesn't exist — chat_processor "
                    f"would crash on first prompt"
                )
    if failures:
        pytest.fail(
            "broken relative imports in dots/chat/__init__.py:\n  "
            + "\n  ".join(failures)
        )
