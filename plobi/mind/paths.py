"""Where Mind lives, and which parts of it we may write.

Three rules from the North Star contract:

- the vault path is resolved from ``MIND_ROOT``; **no drive letters in code**
  (Docs/SPEC-FINAL.md, Mind path rule)
- writes stay inside the prefixes Mind's own verifier tolerates; creating a new
  ``Vault/projects/<x>`` or ``Loom/skills/<x>`` would require editing Mind's
  AGENTS.md declarations, which no agent may do automatically
- ``Vault/…`` / ``Loom/…`` are **logical** paths (ruling 2026-10-14, decisions.md
  in the brain). The human-facing surface may sit in a sibling repository, so a
  logical path is only meaningful together with the ``Roots`` pair below — never
  concatenate one onto a root by hand.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

# Logical prefixes, stable across both layouts. ``Loom/`` names the brain's own
# subtree even after the flatten, because the string is what ledgers, prompts and
# the desktop speak; only ``real_target`` knows where it physically lives.
SAFE_PREFIXES: tuple[str, ...] = (
    "Vault/projects/Plobi",
    "Vault/meta",
    "Vault/notes",
    "Vault/diary",
    "Vault/inbox",
    "Loom/wiki/concepts",
    "Loom/wiki/entities",
    "Loom/wiki/sources",
    "Loom/wiki/comparisons",
    "Loom/raw/chat-logs/exports",
    "Loom/raw/chat-logs/digested",
)

# AI-authored prose belongs in Loom; the human surface forbids AI meta-commentary.
AI_WRITE_ROOT = "Loom/raw/chat-logs/digested"

VAULT_LOGICAL = "Vault"
MIND_LOGICAL = "Loom"
VAULT_ROOT_ENV = "VAULT_ROOT"

# Ruling 2026-10-14: the brain loses its ``Loom/`` level (flatten), and the human
# surface leaves for ``VAULT_ROOT``. The move commit flips this one line; until it
# does, every logical ``Loom/…`` still resolves under ``<mind>/Loom``.
#
# Deliberately a declared flag, not "does ``<mind>/Loom`` exist": inferring it from
# the disk answers "flattened" for any empty directory — every test fake vault and
# every fresh clone — and would silently relocate AI writes to the brain root.
# The vault half does not need a flag because ``VAULT_ROOT`` is a positive pointer.
BRAIN_IS_FLATTENED = True


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


@dataclass(frozen=True)
class Roots:
    """The two roots a logical ``Vault/…`` or ``Loom/…`` path has to be read against.

    ``vault`` is ``None`` while the human surface still lives inside the brain at
    ``<mind>/Vault`` (the pre-2026-10-14 layout). Callers must not branch on it —
    go through :func:`real_target`, which answers the same question once.
    """

    mind: Path
    vault: Optional[Path] = None

    def base_for(self, logical: str) -> Path:
        """The root a logical path is anchored to, without touching the disk."""
        head, _, _ = logical.strip("/").partition("/")
        if head == VAULT_LOGICAL:
            return self.vault if self.vault is not None else self.mind / VAULT_LOGICAL
        return self.mind


def resolve_roots(explicit_mind: Path | str | None = None) -> Optional[Roots]:
    mind = resolve_root(explicit_mind)
    if mind is None:
        return None
    env = os.environ.get(VAULT_ROOT_ENV, "").strip()
    # A pinned pointer that does not exist yet is still the pointer: the surface
    # may be legitimately empty on a fresh setup, and silently falling back to
    # ``<mind>/Vault`` would hand writers two different homes.
    return Roots(mind=mind, vault=Path(env) if env else None)


def real_target(relative: str, root: Union[Path, Roots, None] = None) -> Path:
    """Map a logical Mind path onto wherever it physically lives right now.

    Three layouts are live at once during the move, and all three must keep
    working without a flag:

    1. legacy — ``<mind>/Vault/notes``, ``<mind>/Loom/wiki``
    2. vault split — ``<vault>/notes``, ``<mind>/Loom/wiki``
    3. both done — ``<vault>/notes``, ``<mind>/wiki``

    The vault half is decided by ``VAULT_ROOT`` (an env pointer, never a guess),
    so a write can only ever have one home. The brain half is decided by
    :data:`BRAIN_IS_FLATTENED`, flipped by the move commit itself.
    """
    roots = _roots_or(root)
    cleaned = Path(relative).as_posix().strip("/")
    if not cleaned:
        return roots.mind
    head, _, tail = cleaned.partition("/")
    if head == VAULT_LOGICAL:
        base = roots.base_for(cleaned)
        return base / tail if tail else base
    if head == MIND_LOGICAL and BRAIN_IS_FLATTENED:
        return roots.mind / tail if tail else roots.mind
    return roots.mind / cleaned


def logical_target(target: Path, root: Union[Path, Roots, None] = None) -> Optional[str]:
    """Inverse of :func:`real_target` for a path inside a safe zone.

    Returns ``None`` when ``target`` is outside every prefix in
    :data:`SAFE_PREFIXES`, so this doubles as the write guard used by the memory
    provider — which holds an absolute path and must produce the logical string it
    shows the model.
    """
    roots = _roots_or(root)
    try:
        resolved = target.resolve()
    except OSError:
        return None
    for prefix in SAFE_PREFIXES:
        base = real_target(prefix, roots)
        # No existence check here on purpose: the zones are defined by the path,
        # and a writer creating the first file in ``Loom/raw/chat-logs/exports``
        # must not read as "outside the prefixes" just because the directory is
        # not there yet.
        try:
            inner = resolved.relative_to(base.resolve())
        except ValueError:
            continue
        inner = inner.as_posix()
        return prefix if not inner else f"{prefix}/{inner}"
    return None


def _roots_or(root: Union[Path, Roots, None]) -> Roots:
    if isinstance(root, Roots):
        return root
    if root is not None:
        return Roots(mind=Path(root),
                     vault=Path(os.environ[VAULT_ROOT_ENV]) if os.environ.get(VAULT_ROOT_ENV, "").strip()
                     else None)
    roots = resolve_roots()
    if roots is None:
        raise MindUnavailable("MIND_ROOT is not set and no Mind vault was found")
    return roots


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


def safe_target(
    root: Path, relative: str, *, creating: bool = False
) -> Path:
    """Resolve a logical ``relative`` to the real file, refusing outside the zones.

    The escape check runs against whichever root the zone actually lives under, so
    a split human surface does not read as an escape. ``creating=True`` is the
    writer's guard against resurrecting the old layout: when ``Vault/…`` resolves
    inside the brain but that directory is gone, the surface moved to its own root
    and ``VAULT_ROOT`` was never pinned — creating the directory here would leave
    a second, unbacked-up copy of the user's own notes inside the AI repo.
    """
    if not is_safe_relative(relative):
        raise UnsafeMindPath(f"{relative!r} is outside the writable Mind prefixes")

    roots = _roots_or(root)
    target = real_target(relative, roots)
    base = roots.base_for(Path(relative).as_posix().strip("/"))
    try:
        target.resolve().relative_to(base.resolve())
    except ValueError as exc:  # symlink or traversal escape
        raise UnsafeMindPath(f"{relative!r} escapes its root") from exc
    if creating and roots.vault is None and relative.strip("/").startswith(VAULT_LOGICAL):
        if not (roots.mind / VAULT_LOGICAL).is_dir():
            raise UnsafeMindPath(
                f"{relative!r} would recreate {roots.mind / VAULT_LOGICAL} inside the brain repo; "
                f"the human surface has moved — point {VAULT_ROOT_ENV} at it"
            )
    return target
