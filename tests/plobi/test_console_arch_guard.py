"""Architecture hard gates for plobi/console (ARCH-UI-MASTER §3.5).

If an Agent recreates a parallel service layer under ``plobi/console/``,
these tests fail. Documentation alone did not stop WP-BE-1's first attempt
(``service.py`` / ``usage.py``); this does.

Run::

    .venv\\Scripts\\python.exe -m pytest tests/plobi/test_console_arch_guard.py --basetemp=.pytest-run -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CONSOLE = REPO / "plobi" / "console"

# Only these .py files may live under plobi/console/ (package + thin router).
ALLOWED_CONSOLE_PY = frozenset(
    {
        "__init__.py",
        "router.py",
    }
)

# Explicitly banned names (Agents have already tried these).
BANNED_CONSOLE_PY = frozenset(
    {
        "service.py",
        "usage.py",
        "agents_api_service.py",
        "console_service.py",
        "chat_api.py",
        "credential_bridge.py",
        "registry_v2.py",
    }
)


def test_console_package_only_allows_init_and_router() -> None:
    assert CONSOLE.is_dir(), f"missing {CONSOLE}"
    py_files = {p.name for p in CONSOLE.glob("*.py")}
    unexpected = py_files - ALLOWED_CONSOLE_PY
    assert not unexpected, (
        f"plobi/console/ may only contain {sorted(ALLOWED_CONSOLE_PY)}; "
        f"found extras: {sorted(unexpected)}. "
        "Merge logic into router.py or an existing plobi/ module (ARCH-UI-MASTER §3.5)."
    )


@pytest.mark.parametrize("banned", sorted(BANNED_CONSOLE_PY))
def test_banned_parallel_modules_do_not_exist(banned: str) -> None:
    path = CONSOLE / banned
    assert not path.exists(), (
        f"Banned parallel layer resurrected: {path.relative_to(REPO)}. "
        "Delete it and put helpers in router.py or plobi/quota|agents|delegation."
    )


def test_no_console_api_v2_or_parallel_packages() -> None:
    banned_dirs = [
        REPO / "plobi" / "console_v2",
        REPO / "plobi" / "api",
        REPO / "plobi" / "agents_api",
    ]
    present = [str(p.relative_to(REPO)) for p in banned_dirs if p.exists()]
    assert not present, f"Banned parallel packages present: {present}"
