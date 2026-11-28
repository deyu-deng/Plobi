"""Resolve the ``plobi`` invocation as an argv list for child processes.

Shared public helper. Two callers need the exact same recipe:

* ``plobi_cli.kanban_db`` — the dispatcher spawns profile workers.
* ``plugins/plobi-core/agent_ask.py`` — the L1 secretary asks a project
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
from pathlib import Path
from typing import Optional

_IS_WINDOWS = sys.platform == "win32"


#: 一个 `sys.executable` 得长成什么样才配当 `-m` 的载体：python / python3 /
#: python3.11 / python.exe。`Plobi.exe`、`electron.exe` 一个都不匹配。
_PYTHON_EXE_RE = re.compile(r"^python(\d+(\.\d+)*)?(\.exe)?$", re.IGNORECASE)

#: 安装树里 venv 的相对位置：先找 console script（最不容易变的一条），
#: 找不到再退回解释器 + `-m`。
_VENV_CLI_RELPATHS = (
    ("plobi-agent", "venv", "Scripts", "plobi.exe"),
    ("plobi-agent", "venv", "bin", "plobi"),
)
_VENV_PY_RELPATHS = (
    ("plobi-agent", "venv", "Scripts", "python.exe"),
    ("plobi-agent", "venv", "bin", "python"),
)


def _host_is_python_interpreter() -> bool:
    """`sys.executable` 到底是不是一个 Python 解释器？

    这条判定是 11-23 那单假唤醒的根：桌面端（Electron 壳）里
    `sys.executable` 指向 GUI 本体，`[sys.executable, "-m", "plobi_cli.main"]`
    没有任何解释器去看那个 `-m`，子进程一起来就是壳自己，几秒内带着
    `Unable to move the cache: 拒绝访问` / `Gpu Cache Creation failed` 退掉。
    `wake_agent` 当时只看 `Popen` 返不返回，于是 `woke=true` 一路写进
    `agent_channel.jsonl`——话说送到了，人没醒。

    `frozen` 一定要单独挡：PyInstaller 打包后 basename 可能被改成
    `python.exe` 这种形状，靠名字判会漏。
    """
    if getattr(sys, "frozen", False):
        return False
    return bool(_PYTHON_EXE_RE.match(os.path.basename(sys.executable or "")))


def _home_from_this_module() -> Optional[Path]:
    """从本文件位置反推「我正在被哪套安装跑」。

    这条候选比什么都可信：`plobi_cli` 是 pip 装进 venv 的，所以
    `<home>\\plobi-agent\\venv\\Lib\\site-packages\\plobi_cli\\plobi_bin.py`
    往上的 `venv` 一撞，`<home>` 就出来了。11-23 现场两套门牌同时在跑
    （`Plobi` 生产 + `Plobi-Dev` 开发），光看环境变量会指到隔壁去，
    只有自身位置不会撒谎。
    """
    try:
        here = Path(__file__).resolve()
    except OSError:
        return None
    for parent in here.parents:
        if parent.name == "venv":
            return parent.parent.parent
    for parent in here.parents:
        if parent.name == "plobi-agent":
            return parent.parent
    return None


def _candidate_plobi_homes() -> list[Path]:
    """`PLOBI_HOME` 的候选值，按可信度排。

    自身位置 > `plobi_constants` 的权威值 > 环境变量 > `~/.plobi`。
    `plobi_constants` 是权威，但它不在（纯 CLI 冷启动、测试里）的时候也不能
    让这条路径直接断。
    """
    homes: list[Path] = []
    from_module = _home_from_this_module()
    if from_module is not None:
        homes.append(from_module)
    try:
        from plobi_constants import get_plobi_home

        homes.append(Path(get_plobi_home()))
    except Exception:  # noqa: BLE001 - 常量层缺席不等于没得救
        pass
    env_home = (os.environ.get("PLOBI_HOME") or "").strip()
    if env_home:
        expanded = Path(os.path.expanduser(env_home))
        if expanded not in homes:
            homes.append(expanded)
    fallback = Path.home() / ".plobi"
    if fallback not in homes:
        homes.append(fallback)
    return homes


def _venv_plobi_argv() -> Optional[list[str]]:
    """安装树 venv 里的命令行——GUI 宿主下唯一还站得住的那条。

    桌面端跑起来时，机器上这套 Plobi 是装好的（`$PLOBI_HOME/plobi-agent/venv`），
    它的 `plobi.exe` / `python.exe` 是货真价实的命令行入口。宿主 exe 不能当
    解释器，但这条能。
    """
    for home in _candidate_plobi_homes():
        for relpath in _VENV_CLI_RELPATHS:
            candidate = home.joinpath(*relpath)
            if candidate.is_file():
                return [str(candidate)]
        for relpath in _VENV_PY_RELPATHS:
            candidate = home.joinpath(*relpath)
            if candidate.is_file():
                return [str(candidate), "-m", "plobi_cli.main"]
    return None


def module_plobi_argv() -> list[str]:
    """Return the interpreter-bound Plobi CLI invocation."""
    # ``plobi_cli.main`` is the console-script target declared in
    # pyproject.toml, NOT a top-level ``plobi`` package — there is no
    # ``plobi`` package to import.
    if _host_is_python_interpreter():
        return [sys.executable, "-m", "plobi_cli.main"]
    # 宿主不是解释器（Electron / PyInstaller）：先认显式指定，再认安装树 venv，
    # 最后才退回宿主 exe——那一条注定跑不起来，但调用方至少能靠
    # `plobi_argv_runnable` 判定出来并如实报失败，而不是假成功。
    for env_name in ("PLOBI_PYTHON", "PLOBI_PY"):
        explicit = (os.environ.get(env_name) or "").strip()
        if not explicit:
            continue
        expanded = os.path.expanduser(explicit)
        if os.path.isfile(expanded):
            return [expanded, "-m", "plobi_cli.main"]
    venv_argv = _venv_plobi_argv()
    if venv_argv:
        return venv_argv
    return [sys.executable, "-m", "plobi_cli.main"]


def plobi_argv_runnable(argv: list[str]) -> bool:
    """这串 argv 现在真的能当命令行跑起来吗？

    给唤醒链（`wake.py`）用：拼得出命令 ≠ 起得来。`argv[0]` 是宿主 exe
    加 `-m` 这种形状，`CreateProcess` 会成功返回，孩子几秒后自己死——
    只看 `Popen` 就是 11-23 那次假成功的第二次机会。
    """
    if not argv:
        return False
    head = (argv[0] or "").strip()
    if not head:
        return False
    looks_like_path = (
        os.path.isabs(head)
        or bool(os.path.dirname(head))
        or "/" in head
        or os.sep in head
    )
    if looks_like_path:
        if not os.path.isfile(os.path.expanduser(head)):
            return False
    elif _safe_which_no_cwd(head) is None:
        return False
    # `-m` 前面必须是解释器本身。
    if "-m" in argv[1:2] or (len(argv) > 2 and argv[1] == "-m"):
        return bool(_PYTHON_EXE_RE.match(os.path.basename(head)))
    return True


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
