"""Notes index for the L2 sidebar (WP-MIND-NOTES-VIEW).

A note lives where the *human* put it and belongs to a project or course only by
label, so this module never moves or copies anything: it reads frontmatter and
answers "which notes declare they belong to this agent". The label is
``belongs: [<registered name>, ...]`` — one list rather than one key per relation,
so a new kind of owner does not need a new schema field.

Two rules shape the code:

- narrow reads only. The sidebar gets a bounded list of titles and paths, never
  note bodies (``reader.py`` holds the same discipline for prompts).
- nothing unreadable counts as absent. Every file is tallied into one of
  scanned / parsed / skipped, because a silently-empty list is indistinguishable
  from "the brain has no notes" — and a broken path is not the same answer as an
  empty one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from .paths import real_target
from .project_tree import TREE_ROOT_RELATIVES

logger = logging.getLogger(__name__)

BELONGS_KEY = "belongs"

# Where notes live, as a LOGICAL path: ``Vault/notes`` resolves through
# :func:`plobi.mind.paths.real_target`, so the 2026-10-14 two-root move needs no
# edit here — the human surface answers against ``VAULT_ROOT`` once that pointer is
# pinned, and against ``<MIND_ROOT>/Vault`` until then. Exactly one entry is listed
# because keeping both would be the "new and old layout coexisting" the ruling forbids.
NOTE_ROOTS: tuple[str, ...] = ("Vault/notes",)

MAX_ENTRIES = 200
MAX_PAYLOAD_BYTES = 64 * 1024
MAX_UNKNOWN_NAMES = 50


class BrainNotConfigured(RuntimeError):
    """MIND_ROOT is unset or does not point at a vault."""


@dataclass
class NoteEntry:
    path: str
    title: str
    chars: int
    belongs: tuple[str, ...] = ()
    # Absolute on-disk path. `path` is vault-relative (what a human reads); the
    # desktop needs the real location to hand to its file-preview normaliser, and
    # it cannot reconstruct it without knowing MIND_ROOT.
    fs_path: str = ""


@dataclass
class Scan:
    notes: list[NoteEntry] = field(default_factory=list)
    scanned: int = 0
    skipped: list[str] = field(default_factory=list)

    @property
    def parsed(self) -> int:
        return len(self.notes)


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold()


def _belongs_of(frontmatter: dict[str, Any]) -> tuple[str, ...]:
    raw = frontmatter.get(BELONGS_KEY)
    if raw is None:
        return ()
    if isinstance(raw, (str, int, float)):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(str(item).strip() for item in raw if str(item).strip())


def _read_note(path: Path) -> tuple[Optional[dict[str, Any]], int]:
    """Return ``(frontmatter or None, character count)`` from one read.

    ``utf-8-sig`` matters: this vault has a note whose file starts with a BOM, so a
    plain utf-8 read would leave ``\ufeff`` in front of the fence and the block would
    silently count as absent.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None, 0
    chars = len(text)
    if not text.startswith("---"):
        return None, chars
    try:
        end = text.index("\n---", 3)
    except ValueError:
        return None, chars
    block = text[3:end].strip()
    if not block:
        return None, chars
    try:
        import yaml

        parsed = yaml.safe_load(block)
    except Exception:  # noqa: BLE001 - a malformed note must not break the index
        return None, chars
    return (parsed if isinstance(parsed, dict) else None), chars


def scan_notes(root: Path) -> Scan:
    """Walk the note roots once. Missing roots are skipped, not errors."""
    scan = Scan()
    for relative in NOTE_ROOTS:
        base = real_target(relative, root)
        if not base.is_dir():
            scan.skipped.append(f"{relative} (目录不存在)")
            continue
        for path in sorted(base.rglob("*.md")):
            if not path.is_file():
                continue
            scan.scanned += 1
            front, chars = _read_note(path)
            if front is None:
                scan.skipped.append(path.name)
                continue
            title = str(front.get("title") or path.stem).strip()
            scan.notes.append(
                NoteEntry(
                    path=f"{relative}/{path.relative_to(base).as_posix()}",
                    title=title or path.stem,
                    chars=chars,
                    belongs=_belongs_of(front),
                    fs_path=str(path),
                )
            )
    return scan


def agent_aliases(entry: Any) -> tuple[str, ...]:
    """Names a note may use to say it belongs to this agent.

    The agent's own name plus the last segment of its Mind subtree (that is the
    project's registered name), so ``belongs: SRTP`` matches an agent named
    ``srtp`` hanging ``Vault/projects/SRTP``.
    """
    candidates = [getattr(entry, "name", "") or ""]
    subtree = str(getattr(entry, "mind_subtree", "") or "").strip("/")
    if subtree:
        candidates.append(subtree.rsplit("/", 1)[-1])
    return tuple(dict.fromkeys(c for c in candidates if c))


# Every L2 category folder a tree may live in, as logical paths. Listing only
# ``Vault/projects`` would silently make ``research/SRTP`` — and after the move any
# course, butler or event tree — an "owner nobody registered", which the sidebar
# reports as a dangling ``belongs:`` tag instead of a note list. Same list the
# writer uses (:data:`plobi.mind.project_tree.TREE_ROOT_RELATIVES`), not a copy of it.
OWNER_TREE_ROOTS = TREE_ROOT_RELATIVES


def known_names(root: Path, entries: Iterable[Any]) -> tuple[str, ...]:
    """Registered owners: every live agent plus every project tree in the brain."""
    names = [entry.name for entry in entries if getattr(entry, "name", "")]
    for relative in OWNER_TREE_ROOTS:
        base = real_target(relative, root)
        if base.is_dir():
            names.extend(path.name for path in sorted(base.iterdir()) if path.is_dir())
    return tuple(dict.fromkeys(n for n in names if n))


def notes_for(scan: Scan, aliases: Iterable[str]) -> list[NoteEntry]:
    wanted = {_norm(alias) for alias in aliases if str(alias).strip()}
    if not wanted:
        return []
    return [note for note in scan.notes if wanted & {_norm(item) for item in note.belongs}]


def unknown_belongs(scan: Scan, known: Iterable[str]) -> list[str]:
    """``belongs:`` values that name nothing registered — surfaced, never dropped."""
    known_norm = {_norm(item) for item in known}
    collected: list[str] = []
    for note in scan.notes:
        for item in note.belongs:
            if _norm(item) not in known_norm and item not in collected:
                collected.append(item)
    return collected[:MAX_UNKNOWN_NAMES]


def _budgeted(notes: list[NoteEntry]) -> tuple[list[NoteEntry], int, bool]:
    """Cap by count then by payload bytes; report what the cap actually cut."""
    kept: list[NoteEntry] = []
    used = 0
    truncated = False
    for note in notes:
        cost = len(f"{note.path}{note.title}".encode("utf-8"))
        if len(kept) >= MAX_ENTRIES or used + cost > MAX_PAYLOAD_BYTES:
            truncated = True
            break
        kept.append(note)
        used += cost
    return kept, used, truncated


def build_view(root: Optional[Path], entry: Any, registry_entries: Iterable[Any]) -> dict:
    """One agent's notes-view payload. Raises when the brain is not configured."""
    if root is None:
        raise BrainNotConfigured("MIND_ROOT is not set and no Mind vault was found")

    scan = scan_notes(root)
    matched = notes_for(scan, agent_aliases(entry))
    kept, used_bytes, truncated = _budgeted(matched)
    return {
        "agent": getattr(entry, "name", ""),
        "category": getattr(entry, "category", ""),
        "note_roots": list(NOTE_ROOTS),
        "notes": [
            {
                "path": note.path,
                "fsPath": note.fs_path,
                "title": note.title,
                "chars": note.chars,
                "belongs": list(note.belongs),
            }
            for note in kept
        ],
        "unknown_belongs": unknown_belongs(scan, known_names(root, registry_entries)),
        "totals": {
            "scanned": scan.scanned,
            "with_frontmatter": scan.parsed,
            "skipped": len(scan.skipped),
            "matched": len(matched),
        },
        "budget": {
            "max_entries": MAX_ENTRIES,
            "max_payload_bytes": MAX_PAYLOAD_BYTES,
            "used_bytes": used_bytes,
            "truncated": truncated,
        },
    }
