"""Where Mind lives, and which parts of it we may write.

Two rules from the North Star contract:

- the vault path is resolved from ``MIND_ROOT``; **no drive letters in code**
  (docs/plobi/north_star/GRILL_FREEZE.md, Mind path rule)
- writes stay inside the prefixes Mind's own verifier tolerates; creating a new
  ``Vault/projects/<x>`` or ``Loom/skills/<x>`` would require editing Mind's
  AGENTS.md declarations, which no agent may do automatically
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# Relative to MIND_ROOT. Mirrors docs/specs/MIND_ADAPTER_PLAN.md §3.
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
    repository root (the official brain now lives at ``<repo>/mind``).
    Never guesses a drive letter.
    """
    if explicit:
        candidate = Path(explicit)
        return candidate if candidate.is_dir() else None

    env = os.environ.get("MIND_ROOT", "").strip()
    if env:
        candidate = Path(env)
        return candidate if candidate.is_dir() else None

    # <workspace>/Code/plobi/mind/paths.py -> <workspace>/Code/mind
    # (the official brain moved under the repo; legacy <workspace>/Mind kept
    #  as a fallback so pre-migration layouts still resolve)
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
