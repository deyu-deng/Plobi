"""Bring this machine's existing agent configuration into Plobi's own copy.

Plobi runs its *own* magpie: its own port, its own data directory under
``$PLOBI_HOME/magpie`` (see the desktop's ``magpie-sidecar.ts``). A user who
already runs magpie by hand — or who is already signed in to Claude Code, Codex
or Gemini CLI through it — should not have to sign in again on first launch.

So the onboarding picker offers one click that imports what is already here.
Three rules this module keeps:

1. **Read the machine, write only our own directory.** Sources are inspected
   read-only; the only writes land in ``$PLOBI_HOME/magpie``.
2. **Never sign anybody out.** We copy configuration files. We never call
   ``magpie accounts import``, which (by its own help text) signs the source
   tool out of the account it just took. A copy cannot do that.
3. **Never invent a provider.** An import that copies files but leaves magpie
   with nothing usable reports exactly that, so the UI can say "nothing found"
   instead of pretending the user is configured.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

#: Files worth carrying over. These are the ones that describe *what this
#: machine can already reach*: signed-in subscriptions (``logins.json``), the
#: providers magpie knows (``plugin-providers.json``), the plugins that serve
#: them, and the icons those plugins show.
IMPORT_FILES = (
    "logins.json",
    "plugin-auth.json",
    "plugin-icons.json",
    "plugin-providers.json",
    "plugins.json",
    "plugins.yml",
    "affinity.json",
)

#: Directories carried over as a whole. ``plugins`` holds the plugin code
#: itself (``node_modules``): without it the provider rows the other files
#: describe cannot answer a single request.
IMPORT_DIRS = ("icons", "plugins")

#: This copy's own runtime state — never copied in, on either side.
#: ``usage.jsonl``/``routing`` are this installation's history, ``*.lock`` is a
#: live lock file, ``*.bak`` is magpie's own recovery copy of a file we are
#: already carrying the current version of.
SKIP_NAMES = {"usage.jsonl", "served.json", "stash.json", "migrations.json", "cli-identity.json"}
SKIP_SUFFIXES = (".lock", ".bak")

#: Where the import came from, so Settings can say "imported from …" and so a
#: second scan can tell "already here" from "not here yet".
MARKER_FILE = "import-source.json"

#: ``magpie accounts`` talks to a running gateway, which is a network round trip
#: on a cold start. Bounded, like every other sidecar probe we make.
_ACCOUNTS_TIMEOUT_SECONDS = 12.0
_ACCOUNTS_CACHE_SECONDS = 30.0
_accounts_cache: Optional[tuple] = None


def magpie_home() -> Path:
    """The data directory of the magpie copy we run: ``$PLOBI_HOME/magpie``."""
    from plobi_cli.config import get_plobi_home

    return Path(get_plobi_home()) / "magpie"


def _home_candidates() -> List[Path]:
    """Where another magpie *could* keep its data on this machine.

    Ordered by how likely it is to be the one the user actually uses: the XDG
    variable when they set one, then the plain per-user location, then the
    platform's own application-data folder (~magpie's desktop build uses it).
    """
    out: List[Path] = []
    xdg = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
    if xdg:
        out.append(Path(xdg) / "magpie")
    out.append(Path.home() / ".config" / "magpie")
    if sys.platform == "win32":
        appdata = (os.environ.get("APPDATA") or "").strip()
        if appdata:
            out.append(Path(appdata) / "magpie")
    elif sys.platform == "darwin":
        out.append(Path.home() / "Library" / "Application Support" / "magpie")

    ours = _resolve(magpie_home())
    seen = set()
    unique: List[Path] = []
    for path in out:
        try:
            key = _resolve(path)
        except OSError:
            key = str(path)
        if key in seen or key == ours:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _resolve(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def _skippable(name: str) -> bool:
    return name in SKIP_NAMES or name.endswith(SKIP_SUFFIXES)


def _describe_file(path: Path) -> Dict[str, Any]:
    try:
        stat = path.stat()
        size = stat.st_size
        mtime = stat.st_mtime
    except OSError:
        size = 0
        mtime = 0.0
    return {"name": path.name, "bytes": size, "mtime": mtime}


def _dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total


def scan() -> Dict[str, Any]:
    """What this machine could hand us. Read-only; never raises on a bad path.

    Returns one row per candidate source that actually exists, with the files
    it would contribute. ``importable`` is False when a source exists but holds
    nothing we recognise (an empty ``~/.config/magpie`` left behind by an
    uninstall), which the UI shows differently from "no source at all".
    """
    target = magpie_home()
    sources: List[Dict[str, Any]] = []
    for home in _home_candidates():
        if not home.is_dir():
            continue
        files = []
        for name in IMPORT_FILES:
            candidate = home / name
            if candidate.is_file() and not _skippable(name):
                files.append(_describe_file(candidate))
        dirs = []
        for name in IMPORT_DIRS:
            candidate = home / name
            if candidate.is_dir():
                dirs.append({"name": name, "bytes": _dir_size(candidate)})
        sources.append(
            {
                "path": str(home),
                "files": files,
                "dirs": dirs,
                "importable": bool(files or dirs),
            }
        )

    return {
        "target": str(target),
        "target_exists": target.is_dir(),
        "imported_from": _read_marker(),
        "sources": sources,
    }


def _read_marker() -> Optional[str]:
    try:
        data = json.loads((magpie_home() / MARKER_FILE).read_text(encoding="utf-8"))
    except Exception:
        return None
    source = data.get("from") if isinstance(data, dict) else None
    return source if isinstance(source, str) and source else None


def _write_marker(source: Path, copied: int) -> None:
    payload = {
        "from": str(source),
        "at": time.time(),
        "files": copied,
    }
    try:
        target = magpie_home()
        target.mkdir(parents=True, exist_ok=True)
        (target / MARKER_FILE).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass


def apply(source: Optional[str] = None) -> Dict[str, Any]:
    """Copy one source's configuration into our own magpie directory.

    ``source`` is a path from :func:`scan`; omitted means "the first source that
    has something in it". Existing files in the target are backed up next to
    themselves (``<name>.pre-import``) before being replaced — an import must
    never be the reason a user loses a provider they added in Plobi.
    """
    candidates = [Path(s["path"]) for s in scan()["sources"] if s["importable"]]
    if not candidates:
        return {"ok": False, "error": "nothing-to-import", "copied": 0, "files": [], "dirs": []}

    chosen: Optional[Path] = None
    if source:
        wanted = _resolve(Path(source))
        for candidate in candidates:
            if _resolve(candidate) == wanted:
                chosen = candidate
                break
        if chosen is None:
            return {"ok": False, "error": "unknown-source", "copied": 0, "files": [], "dirs": []}
    else:
        chosen = candidates[0]

    target = magpie_home()
    target.mkdir(parents=True, exist_ok=True)

    copied_files: List[str] = []
    for name in IMPORT_FILES:
        src = chosen / name
        if not src.is_file() or _skippable(name):
            continue
        dst = target / name
        try:
            if dst.is_file() and dst.stat().st_size and not dst.read_bytes() == src.read_bytes():
                shutil.copy2(dst, dst.with_name(dst.name + ".pre-import"))
            shutil.copy2(src, dst)
            copied_files.append(name)
        except OSError:
            continue

    copied_dirs: List[str] = []
    for name in IMPORT_DIRS:
        src_dir = chosen / name
        if not src_dir.is_dir():
            continue
        dst_dir = target / name
        try:
            dst_dir.mkdir(parents=True, exist_ok=True)
            _copy_tree(src_dir, dst_dir)
            copied_dirs.append(name)
        except OSError:
            continue

    _write_marker(chosen, len(copied_files))
    _invalidate_accounts_cache()

    return {
        "ok": bool(copied_files or copied_dirs),
        "from": str(chosen),
        "copied": len(copied_files),
        "files": copied_files,
        "dirs": copied_dirs,
        "error": None if (copied_files or copied_dirs) else "nothing-to-import",
    }


def _copy_tree(src: Path, dst: Path) -> None:
    """Copy a plugin tree without the lock files a running magpie holds."""
    for root, dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        out_dir = dst / rel
        out_dir.mkdir(parents=True, exist_ok=True)
        dirs[:] = [d for d in dirs if d not in {"node_modules.bak", ".cache"}]
        for name in files:
            if _skippable(name):
                continue
            try:
                shutil.copy2(Path(root) / name, out_dir / name)
            except OSError:
                continue


def magpie_binary() -> Optional[str]:
    """The binary we shipped. Never a PATH lookup — borrowing the user's own
    ``magpie-cli`` would run *their* instance against *our* data."""
    env_path = (os.environ.get("PLOBI_MAGPIE_BINARY") or "").strip()
    if env_path and Path(env_path).is_file():
        return env_path
    return None


def _run(args: List[str], timeout: float) -> Optional[str]:
    binary = magpie_binary()
    if not binary:
        return None
    env = dict(os.environ)
    # Same directory the sidecar writes to, or this call reads a different
    # magpie than the one we started.
    env["XDG_CONFIG_HOME"] = str(Path(magpie_home()).parent)
    env["DO_NOT_TRACK"] = "1"
    try:
        proc = subprocess.run(
            [binary] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout or ""


def _invalidate_accounts_cache() -> None:
    global _accounts_cache
    _accounts_cache = None


def accounts(force: bool = False) -> List[Dict[str, Any]]:
    """Every subscription this copy of magpie knows, with its allowance.

    Cached for ``_ACCOUNTS_CACHE_SECONDS`` because ``setup.status`` — which the
    desktop polls — must not spawn a process per call.
    """
    global _accounts_cache
    now = time.monotonic()
    if not force and _accounts_cache is not None:
        at, value = _accounts_cache
        if now - at < _ACCOUNTS_CACHE_SECONDS:
            return value

    raw = _run(["accounts", "--json"], _ACCOUNTS_TIMEOUT_SECONDS)
    rows: List[Dict[str, Any]] = []
    if raw:
        start = raw.find("[")
        if start >= 0:
            try:
                parsed = json.loads(raw[start:])
                if isinstance(parsed, list):
                    rows = [r for r in parsed if isinstance(r, dict)]
            except json.JSONDecodeError:
                rows = []

    _accounts_cache = (now, rows)
    return rows


def has_usable_subscription() -> bool:
    """Whether a signed-in subscription is a real way to answer a message.

    This is what makes "I have a Claude/ChatGPT subscription, why does Plobi
    still ask me to add a provider?" stop happening: a subscription quota is
    one of the ways this app is configured.
    """
    try:
        for row in accounts():
            if not isinstance(row, dict):
                continue
            if row.get("on") is False or row.get("active") is False:
                continue
            if str(row.get("user") or "").strip() or str(row.get("agent") or "").strip():
                return True
    except Exception:
        return False
    return False
