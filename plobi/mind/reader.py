"""Narrow reads from Mind.

Milestone M1 needs two things only: who the user is, and what their projects
are currently about. Reads are unrestricted by the write prefixes (reading has
no concurrency problem) but are deliberately *narrow* — the contract forbids
pouring the whole vault into a model's context.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .paths import resolve_root

logger = logging.getLogger(__name__)

PERSONA_RELATIVE = "Vault/meta/Persona.md"
PROJECTS_RELATIVE = "Vault/projects"

# Cap on what any single read may contribute to a prompt.
MAX_EXCERPT_CHARS = 4000


@dataclass
class ProjectBrief:
    name: str
    plan_excerpt: str = ""
    progress_excerpt: str = ""


@dataclass
class MindContext:
    persona: str = ""
    projects: list[ProjectBrief] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.persona and not self.projects


def _read_excerpt(path: Path, limit: int = MAX_EXCERPT_CHARS) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "\n…"


class MindReader:
    def __init__(self, root: Path | str | None = None):
        self._explicit_root = root

    @property
    def root(self) -> Path | None:
        return resolve_root(self._explicit_root)

    @property
    def available(self) -> bool:
        return self.root is not None

    def persona(self) -> str:
        root = self.root
        if root is None:
            return ""
        return _read_excerpt(root / PERSONA_RELATIVE)

    def project(self, name: str) -> Optional[ProjectBrief]:
        root = self.root
        if root is None:
            return None

        directory = root / PROJECTS_RELATIVE / name
        if not directory.is_dir():
            return None

        return ProjectBrief(
            name=name,
            plan_excerpt=_read_excerpt(directory / "plan.md"),
            progress_excerpt=_read_excerpt(directory / "progress.md"),
        )

    def projects(self, limit: int = 10) -> list[ProjectBrief]:
        root = self.root
        if root is None:
            return []

        base = root / PROJECTS_RELATIVE
        if not base.is_dir():
            return []

        briefs: list[ProjectBrief] = []
        for directory in sorted(p for p in base.iterdir() if p.is_dir()):
            brief = self.project(directory.name)
            if brief and (brief.plan_excerpt or brief.progress_excerpt):
                briefs.append(brief)
            if len(briefs) >= limit:
                break
        return briefs

    def context(self, *, project_limit: int = 5) -> MindContext:
        """Everything M1 is allowed to pull in one call."""
        if not self.available:
            return MindContext()
        return MindContext(persona=self.persona(), projects=self.projects(limit=project_limit))


_DEFAULT: Optional[MindReader] = None
_DEFAULT_LOCK = threading.Lock()


def get_reader() -> MindReader:
    global _DEFAULT
    if _DEFAULT is None:
        with _DEFAULT_LOCK:
            if _DEFAULT is None:
                _DEFAULT = MindReader()
    return _DEFAULT


def set_reader(reader: Optional[MindReader]) -> None:
    global _DEFAULT
    _DEFAULT = reader
