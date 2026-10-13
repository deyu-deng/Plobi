#!/usr/bin/env python3
"""
Grep-based checker for Windows cross-platform footguns.

Flags common patterns that break silently on Windows. Run before PRs —
cheap, fast, catches regressions in a codebase that runs on three OSes.

Usage:
    # Scan staged changes (default when run from a git checkout)
    python scripts/check-windows-footguns.py

    # Scan the full tree (full-repo audit)
    python scripts/check-windows-footguns.py --all

    # Scan a specific file or directory
    python scripts/check-windows-footguns.py path/to/file.py path/to/dir/

    # Scan only modified files vs. main
    python scripts/check-windows-footguns.py --diff main

Exit status:
    0 — no Windows footguns found (or all matches suppressed)
    1 — at least one unsuppressed match

Suppress an intentional use (e.g. tests or platform-gated code) with:
    os.kill(pid, 0)  # windows-footgun: ok — only called on POSIX
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
import io
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent

# Where a build product can hide its own source. The desktop workspace resolves
# extension-less imports against real files on disk first, so an emitted `.js`
# beside a `.ts`/`.tsx` wins — and `.gitignore` ignores `apps/desktop/src/**/*.js`
# wholesale, so `git status` stays clean while the renderer white-screens (10-13
# 09:3x, 766 of them) and vitest reports a just-exported symbol as missing (10-13
# 10:0x). Two different shapes of "my change didn't take", both容器 problems.
DESKTOP_TS_DIRS = ("apps/desktop/src", "apps/desktop/electron")

# Where a build product can hide its own source. The desktop workspace resolves
# extension-less imports against real files on disk, so an emitted `.js` next to
# a `.ts`/`.tsx` wins — and `.gitignore:166` (`apps/desktop/src/**/*.js`) hides it
# from `git status`, so the only visible symptoms are a white desktop and a test
# run claiming a just-exported symbol does not exist (2026-10-13, twice).
DESKTOP_TS_DIRS = ("apps/desktop/src", "apps/desktop/electron")

SUPPRESS_MARKER = re.compile(r"#\s*windows-footgun\s*:\s*ok\b", re.IGNORECASE)

# Line-level guard hints. If a line contains any of these tokens, we assume
# the programmer wrote the line in full awareness of the Windows pitfall —
# e.g. `if hasattr(os, 'setsid'): ... os.setsid()`, or the classic
# `getattr(signal, 'SIGKILL', signal.SIGTERM)`, or `shutil.which("wmic")`.
# False negatives are fine here — the inline `# windows-footgun: ok` marker
# is still the authoritative suppression. This is just to reduce the noise
# floor on obviously-guarded lines so the signal-to-noise stays useful.
GUARD_HINTS = (
    "hasattr(os,",
    "hasattr(signal,",
    "getattr(os,",
    "getattr(signal,",
    "shutil.which(",
    "if platform.system() != \"Windows\"",
    "if platform.system() != 'Windows'",
    "if sys.platform == \"win32\"",
    "if sys.platform != \"win32\"",
    "if sys.platform == 'win32'",
    "if sys.platform != 'win32'",
    "IS_WINDOWS",
    "is_windows",
)

# Dirs we never scan.
EXCLUDED_DIRS = {
    ".git",
    "node_modules",
    "venv",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "site-packages",
    "website/build",
    "optional-skills",  # external skills
}

# File globs we never scan (beyond the dirs above).
EXCLUDED_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".so",
    ".dll",
    ".exe",
    ".png",
    ".jpg",
    ".gif",
    ".ico",
    ".svg",
    ".mp4",
    ".mp3",
    ".wav",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".whl",
    ".lock",
    ".min.js",
    ".min.css",
}

# Files we never scan (self-referential — this script mentions the
# patterns it detects — and the CONTRIBUTING docs that list them).
EXCLUDED_FILES = {
    "scripts/check-windows-footguns.py",
    "CONTRIBUTING.md",
}


@dataclass
class Footgun:
    """A Windows cross-platform footgun pattern."""

    name: str
    pattern: re.Pattern
    message: str
    fix: str
    # If set, matches in files/paths containing any of these substrings are
    # silently ignored (e.g. tests that legitimately exercise the footgun
    # behind a platform guard). Prefer `# windows-footgun: ok` inline
    # suppression over this list; only use path_allowlist for whole files
    # that are inherently tests of the footgun itself.
    path_allowlist: tuple[str, ...] = ()
    # Optional post-match predicate. Takes the re.Match and returns True
    # if the match is a REAL footgun (not a false positive). Use this when
    # the regex can't fully distinguish (e.g. open() where mode may contain
    # "b" for binary, or the line may have `encoding=` elsewhere).
    post_filter: "callable | None" = None


FOOTGUNS: list[Footgun] = [
    Footgun(
        name="open() without encoding= on text mode",
        # Match builtins.open() specifically — NOT os.open(), .open()
        # method calls (Path.open, tarfile.open, zf.open, webbrowser.open,
        # Image.open, wave.open, etc), or `async def open()` method
        # definitions.  The pattern requires a start-of-identifier boundary
        # before `open(` so `os.open`, `.open`, `def open` are all skipped.
        # Note: Path.open() is ALSO affected by the encoding default, but
        # rather than flagging all `.open(` (huge noise), we require an
        # explicit builtins-style open() call.  Path.open() is rare in the
        # codebase compared to open() and can be audited separately.
        pattern=re.compile(
            r"""(?:^|[\s\(,;=])(?<![.\w])open\s*\(\s*[^,)]+\s*(?:,\s*['"](?P<mode>[^'"]*)['"])?"""
        ),
        message=(
            "open() without an explicit encoding= uses the platform default "
            "(UTF-8 on POSIX, cp1252/mbcs on Windows) — files round-tripped "
            "between hosts get mojibake. Always pass encoding='utf-8' for "
            "text files, or use open(path, 'rb')/'wb' for binary."
        ),
        fix=(
            "open(path, 'r', encoding='utf-8')  # or 'utf-8-sig' if the "
            "file may have a BOM"
        ),
        # Filter: only flag if mode is missing-or-text AND the line doesn't
        # already pass encoding=. Skip binary mode (contains "b").
        post_filter=lambda m, line: (
            "b" not in (m.group("mode") or "")
            and "encoding=" not in line
            and "encoding =" not in line
            # Skip `def open(` and `async def open(` (method definitions)
            and not line.lstrip().startswith("def ")
            and not line.lstrip().startswith("async def ")
            # Skip open(path, **kwargs) patterns — encoding may be in the dict.
            # Too expensive to trace; require the author to set encoding in
            # the dict and trust them (or they can add a # windows-footgun: ok).
            and "**" not in line
        ),
    ),
    Footgun(
        name="os.kill(pid, 0)",
        pattern=re.compile(r"\bos\.kill\s*\(\s*[^,]+,\s*0\s*\)"),
        message=(
            "os.kill(pid, 0) is NOT a no-op on Windows — it sends "
            "CTRL_C_EVENT to the target's console process group, "
            "hard-killing the target and potentially unrelated siblings. "
            "See bpo-14484."
        ),
        fix=(
            "Use psutil.pid_exists(pid) (psutil is a core dependency). "
            "Or gateway.status._pid_exists(pid) for the plobi wrapper "
            "with a stdlib fallback."
        ),
    ),
    Footgun(
        name="bare os.setsid",
        pattern=re.compile(r"(?<!hasattr\()\bos\.setsid\b"),
        message=(
            "os.setsid does not exist on Windows and raises "
            "AttributeError. Subprocesses that need detachment on "
            "Windows use creationflags instead."
        ),
        fix=(
            "if platform.system() != 'Windows':\n"
            "    kwargs['preexec_fn'] = os.setsid\n"
            "else:\n"
            "    kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP"
        ),
    ),
    Footgun(
        name="bare os.killpg",
        pattern=re.compile(r"\bos\.killpg\b"),
        message="os.killpg does not exist on Windows.",
        fix=(
            "Use psutil for cross-platform process-tree kill:\n"
            "  p = psutil.Process(pid)\n"
            "  for c in p.children(recursive=True): c.kill()\n"
            "  p.kill()"
        ),
    ),
    Footgun(
        name="bare os.getuid / os.geteuid / os.getgid",
        pattern=re.compile(r"\bos\.(?:getuid|geteuid|getgid|getegid)\b"),
        message=(
            "os.getuid / os.geteuid / os.getgid do not exist on Windows "
            "and raise AttributeError at import time if referenced."
        ),
        fix=(
            "Use getpass.getuser() for the username, or gate with "
            "hasattr(os, 'getuid')."
        ),
    ),
    Footgun(
        name="bare os.fork",
        pattern=re.compile(r"(?<!hasattr\()\bos\.fork\s*\("),
        message="os.fork does not exist on Windows.",
        fix=(
            "Use subprocess.Popen for daemonization, or guard with "
            "hasattr(os, 'fork') and a Windows fallback path."
        ),
    ),
    Footgun(
        name="bare signal.SIGKILL",
        pattern=re.compile(r"\bsignal\.SIGKILL\b"),
        message=(
            "signal.SIGKILL does not exist on Windows and raises "
            "AttributeError at import time."
        ),
        fix="Use getattr(signal, 'SIGKILL', signal.SIGTERM).",
    ),
    Footgun(
        name="bare signal.SIGHUP / SIGUSR1 / SIGUSR2 / SIGALRM / SIGCHLD / SIGPIPE / SIGQUIT",
        pattern=re.compile(
            r"\bsignal\.(?:SIGHUP|SIGUSR1|SIGUSR2|SIGALRM|SIGCHLD|SIGPIPE|SIGQUIT)\b"
        ),
        message=(
            "These POSIX signals don't exist on Windows; referencing "
            "them raises AttributeError at import time."
        ),
        fix=(
            "Use getattr(signal, 'SIGXXX', None) and check for None "
            "before using, or gate the whole block behind a platform check."
        ),
    ),
    Footgun(
        name="subprocess shebang script invocation",
        pattern=re.compile(
            r"subprocess\.(?:run|Popen|call|check_output|check_call)\s*\(\s*\[\s*['\"]\./"
        ),
        message=(
            "Running a script via './scriptname' doesn't work on Windows — "
            "shebang lines aren't honored. CreateProcessW can't execute "
            "bash/python scripts without an explicit interpreter."
        ),
        fix="Use [sys.executable, 'scriptname.py', ...] explicitly.",
    ),
    Footgun(
        name="wmic invocation without shutil.which guard",
        # Match wmic appearing as a subprocess argument — NOT the
        # shutil.which("wmic") guard pattern itself. Looks for wmic in a
        # list or as first arg of subprocess.run/Popen.
        pattern=re.compile(
            r"""(?:subprocess\.\w+\s*\(\s*\[\s*['"]wmic['"]|['"]wmic\.exe['"])"""
        ),
        message=(
            "wmic was removed in Windows 10 21H1 and later. Always "
            "gate with shutil.which('wmic') and fall back to "
            "PowerShell (Get-CimInstance Win32_Process)."
        ),
        fix=(
            "if shutil.which('wmic'):\n"
            "    ... wmic path ...\n"
            "else:\n"
            "    subprocess.run(['powershell', '-NoProfile', '-Command',\n"
            "                    'Get-CimInstance Win32_Process | ...'])"
        ),
    ),
    Footgun(
        name="hardcoded ~/Desktop (OneDrive trap)",
        pattern=re.compile(
            r"""['"](?:~|~/|[A-Z]:[/\\]Users[/\\][^/\\'"]+[/\\])Desktop\b"""
        ),
        message=(
            "When OneDrive Backup is enabled on Windows, the real Desktop "
            "is at %USERPROFILE%\\OneDrive\\Desktop, not %USERPROFILE%\\"
            "Desktop (which exists as an empty husk)."
        ),
        fix=(
            "On Windows, resolve via ctypes + SHGetKnownFolderPath, or "
            "read the Shell Folders registry key, or run PowerShell "
            "[Environment]::GetFolderPath('Desktop')."
        ),
    ),
    Footgun(
        name="asyncio add_signal_handler without try/except",
        pattern=re.compile(r"\.add_signal_handler\s*\("),
        message=(
            "loop.add_signal_handler raises NotImplementedError on "
            "Windows — always wrap in try/except or gate with a "
            "platform check."
        ),
        fix=(
            "try:\n"
            "    loop.add_signal_handler(sig, handler, sig)\n"
            "except NotImplementedError:\n"
            "    pass  # Windows asyncio doesn't support signal handlers"
        ),
    ),
    Footgun(
        name="POSIX-literal temp path as a fallback/default",
        # Narrow on purpose. The broad shape (any "/tmp", "/proc", "/opt" literal)
        # matches ~2,120 legitimate lines in this tree — reading /proc/version to
        # detect a container, Homebrew cert paths on macOS, docker mount examples —
        # and a permanently red rule is a dead rule (this repo's own doctrine).
        # These two shapes are the ones that actually sprayed D:\tmp on a volume
        # root: 9 in-tree `return "/tmp"` fallbacks, zero env-default ones.
        pattern=re.compile(
            r'^\s*return\s+["\'](?:/tmp|/var/tmp|/proc)["\']\s*$'
            r'|\b(?:getenv|environ\.get)\([^)]{0,40},\s*["\'](?:/tmp|/var/tmp)["\']\s*\)'
        ),
        message=(
            "On Windows a bare POSIX literal is not an absolute path: Python "
            "resolves it against the *current drive*, so `return \"/tmp\"` hands "
            "the caller D:\\tmp (or C:\\tmp) — a directory on a volume root — and "
            "`os.environ.get(X, \"/tmp\")` buries the same mistake in a default. "
            "Live on 2026-10-08: test and product code both wrote into D:\\tmp and "
            "D:\\proc this way (裁定 79)."
        ),
        fix=(
            "Derive the location instead of naming it: tempfile.gettempdir(), "
            "plobi_constants.get_temp_root(), or tmp_path in tests. If a POSIX "
            "default is genuinely only reachable on POSIX, gate the branch and "
            'mark the line: return "/tmp"  # windows-footgun: ok — POSIX-only branch'
        ),
    ),
]


def should_scan_file(path: Path) -> bool:
    """Return True if this file is in scope for the checker."""
    # Skip the excluded dirs
    parts = set(path.parts)
    if parts & EXCLUDED_DIRS:
        return False
    # Skip excluded suffixes
    for suffix in EXCLUDED_SUFFIXES:
        if str(path).endswith(suffix):
            return False
    # Skip self and docs that intentionally mention the patterns
    rel = path.relative_to(REPO_ROOT).as_posix()
    if rel in EXCLUDED_FILES:
        return False
    # Only scan text files (rough heuristic — .py, .md, .sh, .ps1, .yaml, etc.)
    if path.suffix in {".py", ".pyw", ".pyi"}:
        return True
    # Other file types are read but only Python-specific patterns would match;
    # that's fine and cheap to skip.
    return False


def iter_files(paths: Iterable[Path]) -> Iterable[Path]:
    for p in paths:
        if p.is_file():
            if should_scan_file(p):
                yield p
        elif p.is_dir():
            for root, dirs, files in os.walk(p):
                # prune excluded dirs in-place for speed
                dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
                for fname in files:
                    fpath = Path(root) / fname
                    if should_scan_file(fpath):
                        yield fpath


def _strip_code(line: str) -> str:
    """Return just the code portion of a line — strip trailing comments and
    skip lines that are entirely inside a string literal or comment.

    Heuristic only (we don't parse Python); good enough to avoid flagging
    our own `# ``os.kill(pid, 0)`` is NOT a no-op` docstring-style comments.
    """
    stripped = line.lstrip()
    # Line starts with # — entirely a comment.
    if stripped.startswith("#"):
        return ""
    # Remove trailing "# ..." inline comment. Naive — doesn't handle `#`
    # inside strings — but on balance reduces noise far more than it adds.
    hash_idx = _find_unquoted_hash(line)
    if hash_idx is not None:
        return line[:hash_idx]
    return line


def _find_unquoted_hash(line: str) -> int | None:
    """Index of the first `#` not inside a single/double/triple-quoted string.

    Simple state machine — good enough for the 99% case of "code, then
    optional trailing comment."
    """
    i = 0
    n = len(line)
    in_s = False  # single-quote string
    in_d = False  # double-quote string
    while i < n:
        c = line[i]
        if c == "\\" and (in_s or in_d) and i + 1 < n:
            i += 2
            continue
        if not in_d and c == "'":
            in_s = not in_s
        elif not in_s and c == '"':
            in_d = not in_d
        elif c == "#" and not in_s and not in_d:
            return i
        i += 1
    return None


def scan_file(path: Path, footguns: list[Footgun]) -> list[tuple[int, str, Footgun]]:
    """Return a list of (line_number, line, footgun) for unsuppressed matches."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    matches: list[tuple[int, str, Footgun]] = []

    # Track whether we're inside a triple-quoted string (docstring/raw block).
    # Simple state machine — handles both ''' and """, toggled by the FIRST
    # triple-quote we see; we don't try to handle nested or f-string cases.
    in_triple: str | None = None  # None, "'''", or '"""'

    for i, line in enumerate(text.splitlines(), start=1):
        # Update triple-quote state based on this line's occurrences.
        code_for_scan = line
        if in_triple:
            # We're inside a docstring — skip the whole line's scan.
            # Check if it closes here.
            if in_triple in line:
                # Find the closing delimiter; anything after it is real code.
                after = line.split(in_triple, 1)[1]
                in_triple = None
                code_for_scan = after
            else:
                continue
        # Now check for docstring-open in the (possibly after-triple) portion.
        # Scan for the first unescaped '''/""" in the current code_for_scan.
        stripped = code_for_scan.strip()
        for delim in ('"""', "'''"):
            if delim in code_for_scan:
                # Count occurrences — even count means single-line docstring,
                # odd means we've entered a multi-line one.
                count = code_for_scan.count(delim)
                if count % 2 == 1:
                    # Odd — we're now inside the triple-quoted block.
                    # Scan only the part BEFORE the opening delimiter.
                    before = code_for_scan.split(delim, 1)[0]
                    code_for_scan = before
                    in_triple = delim
                    break
                else:
                    # Even — entire docstring fits on one line. Strip it
                    # from the scan text to avoid matching on prose.
                    parts = code_for_scan.split(delim)
                    # Keep the "outside" parts (every other chunk, starting
                    # with index 0) as code, drop the "inside" parts.
                    code_for_scan = "".join(parts[::2])
                    break

        if SUPPRESS_MARKER.search(line):
            continue
        # Skip if the line has an obvious guard — e.g. hasattr/getattr/
        # shutil.which or a platform check. False negatives are acceptable;
        # the inline suppression marker is the authoritative override.
        if any(hint in line for hint in GUARD_HINTS):
            continue
        code = _strip_code(code_for_scan)
        if not code.strip():
            continue
        for fg in footguns:
            if fg.path_allowlist and any(s in str(path) for s in fg.path_allowlist):
                continue
            match = fg.pattern.search(code)
            if not match:
                continue
            if fg.post_filter is not None:
                try:
                    if not fg.post_filter(match, line):
                        continue
                except (IndexError, AttributeError):
                    # Post-filter assumed a named group that isn't there — skip.
                    continue
            matches.append((i, line.rstrip(), fg))
    return matches


def get_staged_files() -> list[Path]:
    """Return paths staged in the current git index. Empty on non-git trees."""
    try:
        out = subprocess.check_output(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            cwd=REPO_ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [REPO_ROOT / f for f in out.splitlines() if f.strip()]


def get_diff_files(ref: str) -> list[Path]:
    """Return paths modified vs. the given git ref."""
    try:
        out = subprocess.check_output(
            ["git", "diff", f"{ref}...HEAD", "--name-only", "--diff-filter=ACMR"],
            cwd=REPO_ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [REPO_ROOT / f for f in out.splitlines() if f.strip()]


# ── drive-root residue: a state check, not a regex ───────────────────────
#
# A Footgun pattern can name a suspicious literal; it cannot prove that
# nothing got created. This bug class resolves a path drive-relative —
# "/tmp" on Windows becomes <cwd drive>:\tmp, and a "c:\Users\…" literal
# typed in Git Bash is *relative*, so it lands at <current drive>:\c\Users\…
# (rulings 79 / 48.1). The invariant lives on disk, so this check reads the
# disk. It keeps no baseline snapshot of names: a first-level directory is a
# violation by its SHAPE, or — on the governed drive — by not being a
# category the policy declares. The category list is parsed from the policy
# file at run time; if it cannot be read, that half says so out loud instead
# of reporting a pass (a dead gate and a clean gate look identical, which is
# how this repo's guards used to stay dead for weeks).

_POSIX_ROOT_NAMES_LOWER = frozenset(
    "bin boot dev etc home lib lib64 media mnt opt proc root run srv sys tmp usr var".split()
)

_POLICY_PATH_CANDIDATES = (r"D:\_POLICY\directory-policy.md",)
_WINDOWS_VOLUME_DIR_LOWER = frozenset(("$recycle.bin", "system volume information", "_policy"))


def _drive_roots():
    """Every present volume root, as 'D:\\' style strings (POSIX: just '/')."""
    if os.name != "nt":
        return [os.sep]
    import string

    return [letter + ":" + os.sep for letter in string.ascii_uppercase
            if os.path.isdir(letter + ":" + os.sep)]


def _system_drive_lower():
    raw_drive = (os.environ.get("SystemDrive") or "").strip().rstrip(":" + os.sep)
    return raw_drive.lower()


def _policy_categories():
    """(lowercased declared category names or None, a one-line note)."""
    for path in _POLICY_PATH_CANDIDATES:
        try:
            with io.open(path, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            continue
        cats = set()
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("|"):
                continue
            first = stripped.strip("|").split("|")[0].strip()
            if not first or set(first) <= set("-: ") or first.lower() in ("目录", "dir", "directory"):
                continue
            cats.add(first.lower())
        if cats:
            return cats, "categories parsed from %s (%d declared)" % (path, len(cats))
        return None, "%s has no parseable category table — category check did NOT run" % path
    return None, "no policy file readable — category check did NOT run"


def classify_drive_root_child(drive_root, name, categories, system_drive_lower=""):
    """Why this first-level directory is drive-root residue, or '' if fine."""
    low = name.lower()
    if low in _POSIX_ROOT_NAMES_LOWER:
        return "POSIX filesystem root created drive-relative (ruling 79)"
    if len(low) == 1 and low.isalpha():
        return "single letter — a drive letter that became a directory (ruling 48.1)"
    is_system_volume = bool(system_drive_lower) and drive_root.lower().startswith(system_drive_lower + ":")
    if low in ("users", "windows") and not is_system_volume:
        return "Windows profile path on a non-system volume (drive-relative literal)"
    if low in _WINDOWS_VOLUME_DIR_LOWER:
        return ""
    if drive_root.lower().startswith("d:") and categories is not None and low not in categories:
        return "not a category declared by the drive policy"
    return ""


def find_drive_root_violations(roots=None, categories=None, system_drive_lower=""):
    """[(path, reason)] for first-level directories that are residue."""
    out = []
    for root in _drive_roots() if roots is None else roots:
        try:
            entries = sorted(os.listdir(root))
        except OSError as exc:
            out.append((root + "<unreadable>", "could not list volume root: %s" % exc))
            continue
        for name in entries:
            full = os.path.join(root, name)
            if not os.path.isdir(full):
                continue
            reason = classify_drive_root_child(root, name, categories, system_drive_lower)
            if reason:
                out.append((full, reason))
    return out


def check_drive_root_writes():
    """--assert-no-drive-root-writes: non-zero if a volume root holds residue."""
    roots = _drive_roots()
    categories, note = _policy_categories()
    violations = find_drive_root_violations(roots, categories, _system_drive_lower())

    scanned = 0
    for root in roots:
        try:
            scanned += len(os.listdir(root))
        except OSError:
            pass
    # "0 findings" has to prove the scan ran, or a check that never looked is
    # indistinguishable from a clean machine.
    print("drive-root check: %d volume root(s), %d first-level entries scanned; %s"
          % (len(roots), scanned, note))
    if categories is None:
        print("  WARNING: the category half could not run, so a non-category "
              "directory would NOT be flagged this time.", file=sys.stderr)
    if not violations:
        print("✓ no drive-root residue (shape violations: 0, non-category dirs: 0)")
        return 0
    for path, reason in violations:
        try:
            import datetime

            stamp = datetime.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
        except OSError:
            stamp = "?"
        print("  %s  [mtime %s]  — %s" % (path, stamp, reason))
    print("\n✗ %d drive-root residue item(s). Move each into the policy category it belongs "
          "to; if it holds data, collect evidence on who created it before deleting "
          "anything." % len(violations), file=sys.stderr)
    return 1


def find_js_shadows(repo_root: Path | None = None) -> list[tuple[str, str]]:
    """Return ``(emitted_js, its_source)`` pairs for compiled ``.js`` sitting beside a
    ``.ts``/``.tsx`` of the same stem inside the desktop workspace.

    Why this is a footgun and not just litter (2026-10-13, twice in one day): Vite and
    Vitest resolve an extension-less import to a **real file on disk first**, so a stale
    ``src/app/foo.js`` hides ``src/app/foo.ts``. Symptoms are the worst possible kind —
    the desktop renders a white page, or a test run says a function that was just
    exported does not exist — while ``git status`` stays silent because
    ``.gitignore:166`` ignores ``apps/desktop/src/**/*.js`` wholesale.
    """
    root = (repo_root or REPO_ROOT)
    found: list[tuple[str, str]] = []

    for rel_dir in DESKTOP_TS_DIRS:
        base = root / rel_dir
        if not base.is_dir():
            continue
        for js in sorted(base.rglob("*.js")):
            if "node_modules" in js.parts:
                continue
            for ext in (".ts", ".tsx"):
                source = js.with_suffix(ext)
                if source.is_file():
                    found.append(
                        (js.relative_to(root).as_posix(), source.relative_to(root).as_posix())
                    )
                    break
    return found


def check_js_shadows(repo_root: Path | None = None) -> int:
    """--assert-no-js-shadows: non-zero when emitted JS can shadow its own source."""
    root = repo_root or REPO_ROOT
    shadows = find_js_shadows(root)
    tracked = _tracked_js_count(root)

    # "0 findings" must prove the scan ran, and that nothing tracked is at risk if
    # the operator follows the cleanup command — same discipline as the drive-root check.
    seen = 0
    for rel_dir in DESKTOP_TS_DIRS:
        base = root / rel_dir
        if base.is_dir():
            seen += sum(1 for p in base.rglob("*.js") if "node_modules" not in p.parts)
    print(
        "js-shadow check: %d dir(s) scanned (%s), %d .js file(s) seen, "
        "%s tracked by git, shadows: %d"
        % (
            len(DESKTOP_TS_DIRS),
            ", ".join(DESKTOP_TS_DIRS),
            seen,
            "未知（不在 git 仓里）" if tracked < 0 else "%d" % tracked,
            len(shadows),
        )
    )

    if not shadows:
        print("✓ no emitted .js shadowing a .ts/.tsx source")
        return 0

    for js, source in shadows:
        print(f"  {js}  ← 遮蔽  {source}")

    print(
        "\n✗ %d .js shadow(s): Vite/Vitest take the stale build product instead of the "
        "source, which is how a white desktop and 'exported symbol does not exist' appear "
        "with a clean `git status`. Clean them with:\n"
        "    cd apps/desktop && npx tsc -b . --clean\n"
        "Nothing tracked is at risk while `git ls-files` reports 0 such files (this run: "
        "%d) — if that number is not 0, stop and look before cleaning."
        % (len(shadows), tracked),
        file=sys.stderr,
    )
    return 1


def _tracked_js_count(repo_root: Path) -> int:
    try:
        out = subprocess.run(
            ["git", "ls-files", "--", "apps/desktop/src/**/*.js", "apps/desktop/electron/**/*.js"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
        )
    except OSError:
        return -1
    if out.returncode != 0:
        return -1
    return len([line for line in out.stdout.splitlines() if line.strip()])


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Flag Windows cross-platform footguns in Python code."
    )
    p.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Specific files/dirs to scan (default: staged changes).",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="Scan the full repository (plobi_cli/, gateway/, tools/, cron/, etc.).",
    )
    p.add_argument(
        "--diff",
        metavar="REF",
        help="Scan files changed vs. the given git ref (e.g. --diff main).",
    )
    p.add_argument(
        "--assert-no-drive-root-writes",
        action="store_true",
        help=(
            "State check, not a source scan: fail if any volume root holds a "
            "first-level directory that is drive-relative residue (rulings 79 "
            "/ 48.1) or, on the governed drive, not a declared policy category."
        ),
    )
    p.add_argument(
        "--assert-no-js-shadows",
        action="store_true",
        help=(
            "State check, not a source scan: fail when a compiled .js sits beside the "
            ".ts/.tsx it shadows under apps/desktop/src or apps/desktop/electron. Those "
            "files are gitignored wholesale, so a white desktop or a 'just-exported symbol "
            "does not exist' test failure shows up with a clean `git status`."
        ),
    )
    p.add_argument(
        "--list",
        action="store_true",
        help="List all known footgun rules and exit.",
    )
    return p.parse_args(argv)


def print_rules() -> None:
    print("Known Windows footguns checked by this script:\n")
    for i, fg in enumerate(FOOTGUNS, start=1):
        print(f"{i:2}. {fg.name}")
        print(f"    {fg.message}")
        print(f"    Fix: {fg.fix}")
        print()


def main(argv: list[str]) -> int:
    # Windows terminals default to cp1252, which can't encode the ✓/✗
    # characters used in the output. Reconfigure streams to UTF-8 so the
    # script works correctly on the very platform it is designed to help.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args(argv)

    if args.list:
        print_rules()
        return 0

    if args.assert_no_drive_root_writes:
        return check_drive_root_writes()

    if args.assert_no_js_shadows:
        return check_js_shadows()

    if args.all:
        # Scan main Python packages + scripts
        roots = [
            REPO_ROOT / "plobi_cli",
            REPO_ROOT / "gateway",
            REPO_ROOT / "tools",
            REPO_ROOT / "cron",
            REPO_ROOT / "agent",
            REPO_ROOT / "plugins",
            REPO_ROOT / "scripts",
            REPO_ROOT / "acp_adapter",
            REPO_ROOT / "acp_registry",
        ]
        roots = [r for r in roots if r.exists()]
    elif args.diff:
        roots = get_diff_files(args.diff)
    elif args.paths:
        roots = [p.resolve() for p in args.paths]
    else:
        # Default: staged changes
        roots = get_staged_files()
        if not roots:
            print(
                "No staged files to scan. Pass --all for a full-repo scan, "
                "--diff <ref> for a range diff, or paths explicitly.",
                file=sys.stderr,
            )
            return 0

    total_matches = 0
    files_scanned = 0
    for path in iter_files(roots):
        files_scanned += 1
        matches = scan_file(path, FOOTGUNS)
        for lineno, line, fg in matches:
            rel = path.relative_to(REPO_ROOT).as_posix()
            print(f"{rel}:{lineno}: [{fg.name}]")
            print(f"    {line.strip()}")
            print(f"    — {fg.message}")
            print(f"    Fix: {fg.fix.splitlines()[0]}")
            print()
            total_matches += 1

    if total_matches:
        print(
            f"\n✗ {total_matches} Windows footgun(s) found across "
            f"{files_scanned} file(s) scanned.",
            file=sys.stderr,
        )
        print(
            "  If an individual match is a false positive or intentionally "
            "platform-gated, suppress it with `# windows-footgun: ok` on "
            "the same line.\n  Run with --list to see all rules.",
            file=sys.stderr,
        )
        return 1

    print(
        f"✓ No Windows footguns found ({files_scanned} file(s) scanned)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
