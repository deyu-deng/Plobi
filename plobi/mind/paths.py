"""Where Mind lives, and which parts of it we may write.

Two rules from the North Star contract:

- the vault path is resolved from ``MIND_ROOT``; **no drive letters in code**
  (Docs/SPEC-FINAL.md, Mind path rule)
- writes stay inside the prefixes Mind's own verifier tolerates; creating a new
  ``Vault/projects/<x>`` or ``Loom/skills/<x>`` would require editing Mind's
  AGENTS.md declarations, which no agent may do automatically
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

# Relative to MIND_ROOT. Mirrors Docs/specs/memory-adapter.md §3.
SAFE_PREFIXES: tuple[str, ...] = (
    "Vault/projects/Plobi",
    "Vault/meta",
    "Vault/notes",
    "Vault/journal",
    "Vault/inbox",
    "Loom/wiki/concepts",
    "Loom/wiki/entities",
    "Loom/wiki/sources",
    "Loom/wiki/comparisons",
    "Loom/raw/chat-logs/exports",
    "Loom/raw/chat-logs/digested",
)

# AI-authored prose belongs in Loom; Vault forbids AI meta-commentary.
AI_WRITE_ROOT = "Loom/raw/chat-logs/digested"


class MindUnavailable(RuntimeError):
    """MIND_ROOT is unset or does not point at a vault."""


class UnsafeMindPath(ValueError):
    """A write was attempted outside the tolerated prefixes."""


def resolve_root(explicit: Path | str | None = None) -> Path | None:
    """Resolve the vault root, or ``None`` when it is not configured.

    Order: explicit argument → ``MIND_ROOT`` → sibling ``mind/`` under the
    repository root → legacy ``Mind/`` beside the repository root. A layout
    that keeps the vault in the data directory is addressed by ``MIND_ROOT``,
    never by guessing where the repo happens to sit.
    """
    if explicit:
        candidate = Path(explicit)
        return candidate if candidate.is_dir() else None

    env = os.environ.get("MIND_ROOT", "").strip()
    if env:
        candidate = Path(env)
        return candidate if candidate.is_dir() else None

    # In-repo layouts: <repo>/mind, then the pre-migration <workspace>/Mind.
    # An unset MIND_ROOT with neither present means "no brain configured here"
    # — callers get ``None``, not a guessed path.
    sibling = Path(__file__).resolve().parents[2] / "mind"
    if (sibling / "AGENTS.md").is_file():
        return sibling

    legacy = Path(__file__).resolve().parents[3] / "Mind"
    if (legacy / "AGENTS.md").is_file():
        return legacy

    return None


def require_root(explicit: Path | str | None = None) -> Path:
    root = resolve_root(explicit)
    if root is None:
        raise MindUnavailable("MIND_ROOT is not set and no Mind vault was found")
    return root


def git_toplevel(path: Path) -> Optional[str]:
    """Top-level of the git repository enclosing ``path``, or ``None``.

    ``None`` means ``path`` sits in no repository at all. Two guards need this and
    they are not the same question: the writer must not commit into a repo it does
    not own, while declaration sync must not run a vault's own gate against — or
    edit files inside — an *enclosing* checkout that merely happens to sit above
    ``MIND_ROOT`` (ruling 72 tail; REQUIREMENTS R-013 edge case ②). A directory in
    no repo at all is neither case, so callers compare against ``path`` rather than
    merely testing for ``None``.
    """
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    lines = (completed.stdout or "").strip().splitlines()
    return lines[0] if lines else None


def is_safe_relative(relative: str) -> bool:
    posix = Path(relative).as_posix().lstrip("./")
    if not posix or posix.startswith("..") or Path(posix).is_absolute():
        return False
    return any(posix == prefix or posix.startswith(prefix + "/") for prefix in SAFE_PREFIXES)


def safe_target(root: Path, relative: str) -> Path:
    """Resolve ``relative`` under ``root``, refusing anything outside the zones."""
    if not is_safe_relative(relative):
        raise UnsafeMindPath(f"{relative!r} is outside the writable Mind prefixes")

    target = (root / relative).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:  # symlink or traversal escape
        raise UnsafeMindPath(f"{relative!r} escapes the Mind root") from exc
    return target
