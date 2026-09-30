"""Resolve the ``plobi`` invocation as an argv list for child processes.

Shared public helper. Two callers need the exact same recipe:

* ``plobi_cli.kanban_db`` — the dispatcher spawns profile workers.
* ``plugins/plobi-north-star/agent_ask.py`` — the L1 secretary asks a project
  分身 by running it as a subprocess (裁定 50.4).

Both used to want one implementation; ``kanban_db`` carried it privately.
It lives here instead so a second caller doesn't have to reach into a private
function — and so it can never drift into a third copy.

Resolution order (unchanged from the kanban_db original):

1. ``$PLOBI_BIN`` — explicit operator override.
2. ``plobi`` on ``PATH``.
3. ``sys.executable -m plobi_cli.main`` — interpreter-bound fallback that works
   when no shim is on ``PATH`` (cron, systemd, launchd, detached processes).

Mirrors ``gateway.run._resolve_plobi_bin`` for the same reason the original
stayed local: ``plobi_cli`` sits below ``gateway`` in the dependency order, so
``gateway`` cannot import from here but the reverse is fine.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Optional

_IS_WINDOWS = sys.platform == "win32"


def module_plobi_argv() -> list[str]:
    """Return the interpreter-bound Plobi CLI invocation."""
    # ``plobi_cli.main`` is the console-script target declared in
    # pyproject.toml, NOT a top-level ``plobi`` package — there is no
    # ``plobi`` package to import.
    return [sys.executable, "-m", "plobi_cli.main"]


def _absolute_plobi_path(path: str) -> str:
    """Return an absolute filesystem path for a resolved Plobi shim."""
    expanded = os.path.expanduser(path)
    return expanded if os.path.isabs(expanded) else os.path.abspath(expanded)


def _looks_like_path(value: str) -> bool:
    """Return true when a command override is an explicit path, not a name."""
    expanded = os.path.expanduser(value)
    return (
        expanded.startswith("~")
        or os.path.isabs(expanded)
        or bool(os.path.dirname(expanded))
        or "\\" in expanded
        or bool(re.match(r"^[A-Za-z]:", expanded))
    )


def _is_windows_batch_shim(path: str) -> bool:
    """Return true for Windows shell/batch shims that should not be argv[0]."""
    return path.lower().endswith((".cmd", ".bat"))


def _path_search_names(command: str) -> list[str]:
    """Return executable names to try for an unqualified command."""
    if not _IS_WINDOWS or os.path.splitext(command)[1]:
        return [command]
    raw = os.environ.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD"
    exts = [ext for ext in raw.split(";") if ext]
    return [command + ext for ext in exts]


def _safe_which_no_cwd(command: str) -> Optional[str]:
    """Resolve a bare command from PATH without implicit current-dir search.

    ``shutil.which`` follows platform search behavior. On Windows that can
    include the current directory before PATH for bare names, which is not a
    safe dispatcher primitive. This resolver only considers explicit PATH
    entries and skips empty / ``.`` entries.
    """
    path_env = os.environ.get("PATH", "")
    for raw_dir in path_env.split(os.pathsep):
        if not raw_dir or raw_dir == ".":
            continue
        directory = os.path.expanduser(raw_dir)
        for name in _path_search_names(command):
            candidate = os.path.join(directory, name)
            if not os.path.isfile(candidate):
                continue
            if _IS_WINDOWS or os.access(candidate, os.X_OK):
                return candidate
    return None


def _plobi_path_argv(path: str) -> list[str]:
    """Return argv for a resolved Plobi executable path.

    Windows batch shims (`.cmd` / `.bat`) are not safe as argv[0] for
    worker launches because the argument vector includes task-derived
    values. Prefer the interpreter-bound module form whenever the resolved
    executable is only a shell shim.
    """
    if _IS_WINDOWS and _is_windows_batch_shim(path):
        return module_plobi_argv()
    return [_absolute_plobi_path(path)]


def resolve_plobi_argv() -> list[str]:
    """Resolve the ``plobi`` invocation as argv parts for ``Popen``.

    Tries in order:

    1. ``$PLOBI_BIN`` — explicit operator override. Path-like values are
       normalized to absolute paths; bare command names keep normal PATH
       semantics and never prefer a same-directory file before ``PATH``.
    2. ``shutil.which("plobi")`` — the console-script shim, normalized to
       an absolute path. On Windows, ``which`` can return a relative
       ``.\\plobi.CMD`` when the current directory is on ``PATH``; directly
       launching batch shims is also unsafe with task-derived argv. The
       caller therefore falls back to the interpreter-bound module form
       for implicit ``.cmd`` / ``.bat`` shims.
    3. ``sys.executable -m plobi_cli.main`` — fallback for setups where
       Plobi is launched from a venv and the ``plobi`` shim is not on
       the caller's ``$PATH`` (cron, systemd ``User=`` services,
       launchd jobs, detached processes, etc.). Goes through the running
       interpreter so the result is independent of ``$PATH``.
    """
    import shutil

    env_bin = os.environ.get("PLOBI_BIN", "").strip()
    if env_bin:
        if _looks_like_path(env_bin):
            return _plobi_path_argv(env_bin)
        resolved_env_bin = _safe_which_no_cwd(env_bin)
        if resolved_env_bin:
            return _plobi_path_argv(resolved_env_bin)
        return module_plobi_argv()

    plobi_bin = _safe_which_no_cwd("plobi") if _IS_WINDOWS else shutil.which("plobi")
    if plobi_bin:
        return _plobi_path_argv(plobi_bin)
    return module_plobi_argv()
