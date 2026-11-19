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

from plobi.human.dossier_profile import PROFILE_FILENAME, Stage, read_profile
from .paths import real_target, resolve_root

logger = logging.getLogger(__name__)

# 详版画像在**脑**里，而且**不是 skill**（用户 2026-10-24：「用户画像这种东西不能以 skill 存在，
# 应该是 mind 的一个部分」）。它从 skills/profile/SKILL.md 搬到了 persona/，与 skills 这条线彻底脱钩；
# 精简版仍是 L0 的 USER.md。
PERSONA_RELATIVE = "persona/profile.md"
PROJECTS_RELATIVE = "Dossiers/projects"
RESEARCH_RELATIVE = "Dossiers/research"
COURSES_RELATIVE = "Dossiers/courses"
BUTLER_RELATIVE = "Dossiers/butler"
EVENTS_RELATIVE = "Dossiers/events"

# 上下文接哪几类档案（11-09 起是「项目 ＋ 研究」，11-19 扩到**五类**）。
#
# 为什么扩：用户 11-19 22:0x 拍定「**五种档案是一套模型 ＋ 五个类别**」，
# 首席的五种都是首席的事，总秘书该看得见。扩的是**读侧那张表**，不是权限
# （写仍过 L1 那道闸，裁 199）。
#
# 11-09 那三条「不进上下文」的理由已经不成立：
# * ``courses``——那时盘上只有 ``requirements.md``／``syllabus.md``，**没有档案本体**；
#   现在课程也有档案本体了（同一套形状：目标／现状／节点）。
# * ``events``——那时是空目录；现在事件那一类也有档案本体了。
# * ``butler``——「秘书自己」那一格是 L1 自己的 ``mind_subtree``；读侧按**子目录**
#   扫（``_category_briefs``），L1 自己那份躺在目录根上，扫不到 ⇒ 不会自读。
#
# **上下文不会因为多了三类就变长**：每类各自限量（:meth:`MindReader.dossiers`），
# 且空档（没有目标也没有现状）根本进不来。
DOSSIER_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("projects", PROJECTS_RELATIVE),
    ("research", RESEARCH_RELATIVE),
    ("courses", COURSES_RELATIVE),
    ("butler", BUTLER_RELATIVE),
    ("events", EVENTS_RELATIVE),
)
# 类别 → 那一类的档案目录（逻辑路径）。
CATEGORY_RELATIVES: dict[str, str] = dict(DOSSIER_CATEGORIES)

# 类别在屏上／上下文里的说法（标题行那一格）。
CATEGORY_LABELS: dict[str, str] = {
    "projects": "项目",
    "research": "研究",
    "courses": "课程",
    "butler": "管家",
    "events": "事件",
}

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
    category: str = "projects"
    # 档案本体里那个 ``agent`` 格（一般等于目录名；名册上的名字可能大小写不同）。
    agent: str = ""
    # **阶段**（屏上进度条上的那些节点）。与目标、现状同出一份 ``dossier.json``。
    stages: list[Stage] = field(default_factory=list)

    @property
    def stages_done(self) -> int:
        """已经做完的阶段数。"""
        return sum(1 for stage in self.stages if stage.done)

    @property
    def current_stage(self) -> Stage | None:
        """正在做的那一个（第一个没做完的）；全做完了就是最后一个。"""
        if not self.stages:
            return None
        for stage in self.stages:
            if not stage.done:
                return stage
        return self.stages[-1]


@dataclass
class MindContext:
    persona: str = ""
    # 项目 ＋ 研究，按"当前优先、其余按名"排好序。
    dossiers: list[ProjectBrief] = field(default_factory=list)

    @property
    def projects(self) -> list[ProjectBrief]:
        """项目类档案（旧名 ``projects`` 留着，老调用方还在用）。"""
        return [brief for brief in self.dossiers if brief.category == "projects"]

    @property
    def research(self) -> list[ProjectBrief]:
        """研究类档案。"""
        return [brief for brief in self.dossiers if brief.category == "research"]

    @property
    def is_empty(self) -> bool:
        return not self.persona and not self.dossiers


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

    def brief(self, category: str, name: str) -> Optional[ProjectBrief]:
        """一档档案的摘要（``category`` 是 ``projects``／``research``）。"""
        root = self.root
        if root is None:
            return None

        relative = CATEGORY_RELATIVES.get(category, PROJECTS_RELATIVE)
        directory = real_target(f"{relative}/{name}", root)
        if not directory.is_dir():
            return None

        # **档案本体**（``dossier.json``）：目标与现状都在这里。md 那一对已退役，
        # 读侧不再看它一眼——屏上那张卡与这段摘要读的是同一个文件。
        profile = read_profile(directory / PROFILE_FILENAME).profile
        if profile is None:
            return None

        return ProjectBrief(
            name=name,
            goal=profile.goal,
            status=profile.status,
            category=category,
            agent=profile.agent,
            stages=list(profile.stages),
        )

    def project(self, name: str) -> Optional[ProjectBrief]:
        """项目类档案的摘要（老名字，老调用方还在用）。"""
        return self.brief("projects", name)

    def _category_briefs(self, category: str, relative: str) -> list[ProjectBrief]:
        """一个类目下的全部档案摘要，按名字排。"""
        root = self.root
        if root is None:
            return []

        base = real_target(relative, root)
        if not base.is_dir():
            return []

        briefs: list[ProjectBrief] = []
        for directory in sorted(
            (p for p in base.iterdir() if p.is_dir()), key=lambda p: p.name.casefold()
        ):
            brief = self.brief(category, directory.name)
            if brief and (brief.goal or brief.status):
                briefs.append(brief)
        return briefs

    def dossiers(
        self,
        *,
        current: str = "",
        limits: Optional[dict[str, int]] = None,
    ) -> list[ProjectBrief]:
        """进上下文的那几档档案——**当前优先**，其余按名字。

        限量口径（数值由调用方给，默认每类全要；上下文的实际数值见
        ``plobi/mind/provider.py`` 的 ``PROFILE_PROJECT_LIMIT``／``PROFILE_RESEARCH_LIMIT``）：

        * **按类目各自限量**（``limits={"projects": 3, "research": 2}``），
          不是拿一个总数去切——否则项目一多，研究那两类就一份都进不来。
        * **当前那一份无条件入选**（它落在哪个类目就算哪个类目的名额），
          其余按名字补足：旗舰项目不许被字母序切掉。
        * 排出来的顺序：**当前那一份第一**，其余按类目（项目 → 研究）、类内按名字。
        """
        root = self.root
        if root is None:
            return []

        caps = limits or {}
        picked: list[list[ProjectBrief]] = []
        for category, relative in DOSSIER_CATEGORIES:
            briefs = self._category_briefs(category, relative)
            limit = max(int(caps.get(category, len(briefs))), 0)
            chosen: list[ProjectBrief] = []
            current_brief = next(
                (brief for brief in briefs if _is_current(brief, current)), None
            )
            if current_brief is not None:
                # 当前那一份**无条件入选**：它占掉一个名额，字母序排最后的那份被挤掉。
                chosen.append(current_brief)
                limit = max(limit, 1)
            for brief in briefs:
                if len(chosen) >= limit:
                    break
                if current_brief is not None and brief is current_brief:
                    continue
                chosen.append(brief)
            picked.append(chosen)

        ordered: list[ProjectBrief] = []
        for group in picked:
            ordered.extend(brief for brief in group if _is_current(brief, current))
        for group in picked:
            ordered.extend(brief for brief in group if not _is_current(brief, current))
        return ordered

    def projects(self, limit: int = 10) -> list[ProjectBrief]:
        """项目类档案摘要（老接口：按名字排序截断）。"""
        briefs = self._category_briefs("projects", PROJECTS_RELATIVE)
        return briefs[:limit]

    def context(
        self,
        *,
        project_limit: int = 5,
        research_limit: int = 2,
        other_limit: int = 2,
        current: str = "",
    ) -> MindContext:
        """Everything M1 is allowed to pull in one call.

        ``other_limit`` 是 11-19 新加的那三类（课程／管家／事件）的**每类**限量——
        五类都在上下文里，但每类都限量 ⇒ 上下文不会因为类别多就变长。
        """
        if not self.available:
            return MindContext()
        return MindContext(
            persona=self.persona(),
            dossiers=self.dossiers(
                current=current,
                limits={
                    "projects": project_limit,
                    "research": research_limit,
                    "courses": other_limit,
                    "butler": other_limit,
                    "events": other_limit,
                },
            ),
        )


def _is_current(brief: ProjectBrief, current: str) -> bool:
    """这一份是不是"当前那一份"。

    名册上的名字与盘上的目录名可能只差大小写（名册 ``srtp`` ↔ 目录 ``SRTP``），
    所以两边都比**小写**。
    """
    wanted = (current or "").strip().casefold()
    if not wanted:
        return False
    return wanted in {brief.name.casefold(), brief.agent.casefold()}


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
