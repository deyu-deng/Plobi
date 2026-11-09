"""Narrow reads from Mind.

Milestone M1 needs two things only: who the user is, and what their projects
are currently about. Reads are unrestricted by the write prefixes (reading has
no concurrency problem) but are deliberately *narrow* — the contract forbids
pouring the whole surface into a model's context.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .dossier_profile import PROFILE_FILENAME, read_profile
from .paths import real_target, resolve_root

logger = logging.getLogger(__name__)

# 详版画像在**脑**里，而且**不是 skill**（用户 2026-10-24：「用户画像这种东西不能以 skill 存在，
# 应该是 mind 的一个部分」）。它从 skills/profile/SKILL.md 搬到了 persona/，与 skills 这条线彻底脱钩；
# 精简版仍是 L0 的 USER.md。
PERSONA_RELATIVE = "persona/profile.md"
PROJECTS_RELATIVE = "Surface/dossiers/projects"

# Cap on what any single read may contribute to a prompt.
MAX_EXCERPT_CHARS = 4000


@dataclass
class ProjectBrief:
    """一个项目的**现状摘要**。

    11-09 起（WP-DOSSIER-SHAPE）这份摘要来自**档案本体**（``dossier.json``），
    与屏上那张卡的第一段**同一份数据**——一个真源两个消费者，不再各读各的 md。
    """

    name: str
    goal: str = ""
    status: str = ""


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
        path = real_target(PERSONA_RELATIVE, root)
        if not path.is_file():
            # Loud on purpose: this pointer used to name a file the real surface never
            # had, while the test fixture created that phantom path — so the slice was
            # silently empty in production and green in tests.
            logger.warning("Mind persona slice missing: %s", path)
            return ""
        return _read_excerpt(path)

    def project(self, name: str) -> Optional[ProjectBrief]:
        root = self.root
        if root is None:
            return None

        directory = real_target(f"{PROJECTS_RELATIVE}/{name}", root)
        if not directory.is_dir():
            return None

        # **档案本体**（``dossier.json``）：目标与现状都在这里。md 那一对已退役，
        # 读侧不再看它一眼——屏上那张卡与这段摘要读的是同一个文件。
        profile = read_profile(directory / PROFILE_FILENAME).profile
        if profile is None:
            return None

        return ProjectBrief(name=name, goal=profile.goal, status=profile.status)

    def projects(self, limit: int = 10) -> list[ProjectBrief]:
        root = self.root
        if root is None:
            return []

        base = real_target(PROJECTS_RELATIVE, root)
        if not base.is_dir():
            return []

        briefs: list[ProjectBrief] = []
        for directory in sorted(p for p in base.iterdir() if p.is_dir()):
            brief = self.project(directory.name)
            if brief and (brief.goal or brief.status):
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
