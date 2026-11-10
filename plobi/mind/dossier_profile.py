"""档案本体（``dossier.json``）——档案的新真源：目标／状况／阶段／流水。

用户 11-09：「**Plan现在占的位置太长了，本质还是md，没有UI设计。**」「而且我叫
Agent维护相关的笔记，Plan或者progress它都不知道有这个东西的存在。」「**以后我们不
需要md文件的形式了。**」「**抽成结构化并把 md 删掉。**」

⇒ 档案不再是一篇 md（frontmatter ＋ 七节正文 ＋ 一张 Roadmap 表），而是**一条结构化
记录**：一档一份 ``dossier.json``，与 ``tasks.json`` 同目录、在同一个 git 仓里。

为什么落文件而不是落库（长远口径，纸 ``tasks/dossier-shape-task.md`` §2）：

* 每日备份捆的是**五个仓**，人读面（``HUMAN_ROOT``）是其中之一；``vault.db`` 不是
  仓，捆不到。
* 写路径只有一条：Agent 用 ``dossier_write``（同一套 L1 点头闸）。走库要么绕过那道
  闸，要么另写一个写入器——两样都比现在差。
* 与 ``tasks.json`` 同构：同一目录、同一套原子写、同一套「坏文件不许糊成空表」。

这一层的四件事（与 :mod:`plobi.mind.dossier_tasks` 同一套纪律，不另立规矩）：

* **坏文件不许糊住读手**：文件不在 ⇒「还没建立」；文件在但 JSON 坏了 ⇒「读不到」。
  这两件事必须分开——空档案会被读成「什么都没有」，而那是**假的**。
* **只认自己那几个键**：写回时原样保留文件里我们不认识的键，不把别人的字段洗掉。
* **落盘要原子**：临时文件 ＋ ``os.replace``，写到一半崩了不会留下半份档案。
* **md 只在迁移时读**：:func:`parse_plan_markdown` 是**一次性迁移**用的（把旧档案抽
  成结构化），迁移完 md 就退役了，读侧不再看它一眼。

四条数据格（与屏上那张卡的五段对应）：

* ``goal`` —— **目标**（进度条右上角那枚图标 hover 出来的详情）。
* ``status`` —— **现状**（一段话；与「记忆系统喂给 L1 的各 L2 现状摘要」**同一份**）。
* ``stages[]`` —— **阶段**（进度条上一个节点 ＝ 一个阶段；``done`` 决定填到哪）。
* ``log`` —— **流水**（旧 ``progress.md`` 的正文，原样留着，不丢东西）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 档案本体那一份文件名。与 ``tasks.json`` 同目录。
PROFILE_FILENAME = "dossier.json"
# 记录形状的版本号：以后加字段时在这里加一档，旧记录照旧读。
PROFILE_VERSION = 1

# 这一层自己写的那几格。**不在这里面的键，写回时原样留着**（只增不改）。
KNOWN_KEYS: frozenset[str] = frozenset(
    {"version", "agent", "title", "category", "goal", "status", "stages", "registry", "updated", "log"}
)

# 屏上一格简介的上限：一句话／一段，不是一篇。截断了要看得出来（尾上那点省略号）。
GOAL_MAX_CHARS = 240
STATUS_MAX_CHARS = 320

# md 里那些节的名字——**只在迁移时用**（``## Goal``／``## Roadmap``／``## Status``）。
_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
# 代码块里那种路线图写法（``Phase 1  数据层：…``）。
_PHASE_RE = re.compile(r"^((?:Phase|阶段|Stage)\s*\d+)(.*)$")
_FRONT_KEYS = (
    "project",
    "title",
    "type",
    "group",
    "cloud",
    "status",
    "created",
    "updated",
    "tech",
    "summary",
)


class ProfileError(RuntimeError):
    """档案本体这一层的失败。"""


@dataclass
class Stage:
    """一个阶段（进度条上的一个节点）。"""

    id: str
    title: str
    detail: str = ""
    done: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "detail": self.detail, "done": self.done}


@dataclass
class DossierProfile:
    """一档档案。"""

    agent: str
    title: str = ""
    category: str = "projects"
    goal: str = ""
    status: str = ""
    stages: list[Stage] = field(default_factory=list)
    registry: dict[str, Any] = field(default_factory=dict)
    updated: str = ""
    log: str = ""

    @property
    def milestones_done(self) -> int:
        return sum(1 for stage in self.stages if stage.done)

    @property
    def milestones_total(self) -> int:
        return len(self.stages)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": PROFILE_VERSION,
            "agent": self.agent,
            "title": self.title or self.agent,
            "category": self.category,
            "goal": self.goal,
            "status": self.status,
            "stages": [stage.to_dict() for stage in self.stages],
            "registry": dict(self.registry),
            "updated": self.updated,
            "log": self.log,
        }


@dataclass
class ProfileFile:
    """读回来的整份档案。``error`` 有值 = 读不到（不是「什么都没有」）。"""

    exists: bool
    error: str | None
    profile: DossierProfile | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "exists": self.exists,
            "error": self.error,
            "profile": self.profile.to_dict() if self.profile else None,
        }


def profile_path(home: Path) -> Path:
    """档案目录下的本体文件。"""
    return home / PROFILE_FILENAME


def home_for_agent(name: str, roster_path: Path | None = None) -> Path | None:
    """名册那一行落到的档案目录。

    **复用清单那一层的解析**（:func:`plobi.mind.dossier_tasks.find_home`）：名册 →
    ``mind_subtree`` → :func:`plobi.mind.paths.real_target` 这一条路只许有一处，
    两处各写一套迟早分叉（档案卡说有档案、清单说没有，比没有还糟）。

    * 名册里没这一行 ⇒ :class:`~plobi.mind.dossier_tasks.AgentNotRegistered`。
    * 这一行没绑 ``mind_subtree`` ⇒ ``None``：它压根没有档案可谈。
    * 绑了但根读不到 ⇒ :class:`~plobi.mind.dossier_tasks.DossierRootUnavailable`。
    """
    from .dossier_tasks import find_home

    return find_home(name, roster_path)


def read_profile(path: Path) -> ProfileFile:
    """读一份档案本体。坏了不抛异常糊住读手——返回「读不到」这一格。"""
    if not path.is_file():
        return ProfileFile(exists=False, error=None, profile=None)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("dossier profile: %s is unreadable: %s", path, exc)
        return ProfileFile(
            exists=True, error=f"{path.name} is not valid JSON: {exc}", profile=None
        )
    if not isinstance(raw, dict):
        return ProfileFile(
            exists=True, error=f"{path.name} must be a JSON object", profile=None
        )
    return ProfileFile(exists=True, error=None, profile=_parse_profile(raw))


def profile_for_agent(name: str, roster_path: Path | None = None) -> ProfileFile:
    """一个 Agent 的档案本体（名册 → 目录 → 读文件）。"""
    home = home_for_agent(name, roster_path)
    if home is None:
        return ProfileFile(exists=False, error=None, profile=None)
    return read_profile(profile_path(home))


def write_profile(path: Path, profile: DossierProfile, *, extra: dict[str, Any] | None = None) -> None:
    """写一份档案本体（原子）。

    ``extra`` 是**文件里我们不认识的键**（原样带回）——只增不改：不把别人的字段洗掉。
    """
    payload: dict[str, Any] = dict(extra or {})
    payload.update(profile.to_dict())
    _write_atomic(path, payload)


def update_profile(path: Path, profile: DossierProfile) -> ProfileFile:
    """读回来 → 只改我们认得的那几格 → 原子写回。

    文件里**我们不认识的键原样留着**（只增不改）：别人往这份档案里加的东西，不许被
    我们的写回顺手洗掉。
    """
    extra: dict[str, Any] = {}
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if isinstance(raw, dict):
            extra = {key: value for key, value in raw.items() if key not in KNOWN_KEYS}
    write_profile(path, profile, extra=extra)
    return read_profile(path)


def apply_profile_update(
    path: Path,
    *,
    goal: Any = None,
    status: Any = None,
    stages: Any = None,
    log_append: Any = None,
    today: str = "",
) -> ProfileFile:
    """按**字段**改一份档案本体——只改点名的几格，其余原样留着。

    走 :func:`update_profile`：文件里我们不认识的键原样带回（只增不改），落盘原子
    （临时文件 ＋ ``os.replace``）。``updated`` 由**代码**盖成今天——模型不填日期，
    免得它把档案的时间写成上周。

    形状不对就在**落盘前**抛 :class:`ProfileError`：盘上那一份一个字节都不动
    （``stages`` 里有一条缺 ``title`` ⇒ 整次调用失败，不留半份档案）。

    ``stages`` 是**整组替换**：传进来的就是全部阶段，没传进来的那几条等于被删了
    （要保留的阶段请照原样一起传回来）。
    """
    current = read_profile(path)
    if not current.exists:
        raise ProfileError(f"{path.name} 还不存在：这份档案还没有建起来（{path}）")
    if current.error:
        # 坏文件不许被写回盖掉——盖掉了就是把一份「读不到」说成「什么都没有」。
        raise ProfileError(f"{path.name} 读不出来：{current.error}——坏文件不许被写回盖掉")
    profile = current.profile
    if profile is None:  # pragma: no cover - read_profile 不会这么答
        raise ProfileError(f"{path.name} 读不出来：档案本体不在预期形状里")

    if goal is not None:
        profile.goal = _require_text(goal, "goal")
    if status is not None:
        profile.status = _require_text(status, "status")
    if stages is not None:
        profile.stages = _validated_stages(stages)
    if log_append is not None:
        text = _require_text(log_append, "log_append")
        if text:
            profile.log = f"{profile.log}\n\n{text}" if profile.log else text

    # 日期由代码盖：模型不填，免得档案的时间停在上一轮。
    profile.updated = today or date.today().isoformat()
    return update_profile(path, profile)


def _require_text(value: Any, field: str) -> str:
    """一格文本：类型不对当场说清，不落盘。"""
    if not isinstance(value, str):
        raise ProfileError(f"{field} 必须是字符串（收到 {type(value).__name__}）")
    return value.strip()


def _validated_stages(raw: Any) -> list[Stage]:
    """阶段列表的形状闸：每一条都要 ``id`` ＋ ``title``，``done`` 必须是布尔。

    有一条不合法 ⇒ 整组不合法（抛 :class:`ProfileError`）——半份阶段表比不改更糟。
    """
    if not isinstance(raw, list):
        raise ProfileError(f"stages 必须是数组（收到 {type(raw).__name__}）")
    stages: list[Stage] = []
    for index, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            raise ProfileError(f"stages 第 {index} 条必须是对象（收到 {type(entry).__name__}）")
        sid = str(entry.get("id") or "").strip()
        if not sid:
            raise ProfileError(f"stages 第 {index} 条缺 id")
        title = str(entry.get("title") or "").strip()
        if not title:
            raise ProfileError(f"stages 第 {index} 条缺 title")
        done = entry.get("done", False)
        if not isinstance(done, bool):
            raise ProfileError(f"stages 第 {index} 条的 done 必须是 true／false")
        detail = entry.get("detail", "")
        if not isinstance(detail, str):
            raise ProfileError(f"stages 第 {index} 条的 detail 必须是字符串")
        stages.append(Stage(id=sid, title=title, detail=detail.strip(), done=done))
    return stages


def _parse_profile(raw: dict[str, Any]) -> DossierProfile:
    """把文件里的东西收成一份档案。缺字段的补默认，坏的跳过。"""
    registry = raw.get("registry")
    profile = DossierProfile(
        agent=str(raw.get("agent") or "").strip(),
        title=str(raw.get("title") or "").strip(),
        category=str(raw.get("category") or "projects").strip() or "projects",
        goal=str(raw.get("goal") or "").strip(),
        status=str(raw.get("status") or "").strip(),
        stages=_parse_stages(raw.get("stages")),
        registry=dict(registry) if isinstance(registry, dict) else {},
        updated=str(raw.get("updated") or "").strip(),
        log=str(raw.get("log") or "").strip(),
    )
    if not profile.agent:
        profile.agent = profile.title
    return profile


def _parse_stages(raw: Any) -> list[Stage]:
    """阶段列表。缺 ``title`` 的**跳过那一条**，不炸整份。"""
    if not isinstance(raw, list):
        return []
    stages: list[Stage] = []
    for index, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        if not title:
            continue
        stages.append(
            Stage(
                id=str(entry.get("id") or f"s{index}").strip(),
                title=title,
                detail=str(entry.get("detail") or "").strip(),
                done=bool(entry.get("done")),
            )
        )
    return stages


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    """临时文件 ＋ ``os.replace``：读者要么看到旧的，要么看到新的，看不到半份。"""
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(prefix=".dossier-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(body)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# ── 迁移：把旧档案（md）抽成结构化 ─────────────────────────────────────────
# 这一段**只在迁移时用**：抽完 md 就退役了，读侧不再看它一眼。


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """``---`` 那一块（键值都按字符串收）与正文。"""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    meta: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta, text[end + 4 :].lstrip("\n")


def split_sections(body: str) -> dict[str, str]:
    """``## 小节`` → 那一节的内容（键是小节名，去掉空格、忽略大小写）。"""
    sections: dict[str, str] = {}
    marks = list(_SECTION_RE.finditer(body))
    if not marks:
        return sections
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(body)
        name = mark.group(1).strip().casefold()
        sections[name] = body[mark.end() : end].strip()
    return sections


def parse_plan_markdown(text: str, *, agent: str, category: str = "projects") -> DossierProfile:
    """把一篇旧 ``plan.md`` 抽成一条档案。

    抽四样：**目标**（``## Goal`` 的第一段）、**状况**（``## Status`` 那几行）、
    **阶段**（``## Roadmap`` 表的一行一格 ＋ ``## Milestones`` 的勾选态）、
    **注册表字段**（frontmatter 那十来个键）。
    """
    meta, body = split_frontmatter(text)
    sections = split_sections(body)

    goal = (
        _first_paragraph(_pick_section(sections, "goal", "定位"))
        or _first_paragraph(body)
    )
    status = _status_line(_pick_section(sections, "status", "现状"))
    stages = _stages_from(sections)

    registry: dict[str, Any] = {}
    for key in _FRONT_KEYS:
        if key not in meta:
            continue
        value = meta[key].strip()
        if key == "tech":
            registry["tech"] = [part.strip() for part in value.strip("[]").split(",") if part.strip()]
        elif key == "status":
            registry["state"] = value
        elif key == "updated":
            registry["updated"] = value
        else:
            registry[key] = value

    return DossierProfile(
        agent=agent,
        title=meta.get("title") or agent,
        category=category,
        goal=_clip(goal, GOAL_MAX_CHARS),
        status=_clip(status, STATUS_MAX_CHARS),
        stages=stages,
        registry=registry,
        updated=meta.get("updated") or date.today().isoformat(),
    )


def parse_progress_markdown(text: str) -> str:
    """把一篇旧 ``progress.md`` 抽成**流水**（正文原样留着）。

    frontmatter 剥掉（那几格已经在 ``registry`` 里了），其余一字不改——流水是
    人写的历史，不许在这一步被压成摘要。
    """
    _, body = split_frontmatter(text)
    return body.strip()


def _clip(text: str, limit: int) -> str:
    """截到上限，截了要看得出来。"""
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    return flat[: max(0, limit - 1)].rstrip() + "…"


def _pick_section(sections: dict[str, str], *names: str) -> str:
    """按名字取小节内容。

    名字后面可以带括号说明——老档案里有 ``## 现状（2026-09-28，取自仓内 README）``
    这种写法（小节名对不上 ⇒ 现状读成空，等于把一份档案说成「什么都不知道」）。
    """
    for name in names:
        if name in sections:
            return sections[name]
        for key, value in sections.items():
            if key.startswith(name):
                return value
    return ""


def _first_paragraph(text: str) -> str:
    """第一段不像标题／表格／列表的正文，压成一行。"""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ">", "|", "```", "---", "-", "*")):
            continue
        return line
    return ""


def _status_line(text: str) -> str:
    """``## Status`` 那几行压成一句（现状 ＋ 下一步）。"""
    parts: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.lstrip("-*+ ").strip()
        if line and line not in parts:
            parts.append(line)
    return "；".join(parts)


def _stages_from(sections: dict[str, str]) -> list[Stage]:
    """``## Roadmap`` 表的每一行 ＝ 一个阶段；``## Milestones`` 的勾选态按序对上。

    没有那张表的老档案（形状不一样，只有 ``## Milestones`` 那一串复选框）⇒ 直接把
    那一串当阶段：**一条勾选框 ＝ 一个节点**，标题取冒号后面那半句。
    """
    milestones = _milestone_flags(sections.get("milestones", ""))
    stages: list[Stage] = []
    roadmap = sections.get("roadmap", "")
    rows = _roadmap_rows(roadmap) or _phase_rows(roadmap)
    for index, (title, detail) in enumerate(rows):
        done = milestones[index] if index < len(milestones) else _looks_done(detail)
        stages.append(
            Stage(
                id=f"s{index + 1}",
                title=title,
                detail=detail,
                done=done,
            )
        )
    if stages:
        return stages
    for mid, title, done in _milestone_items(sections.get("milestones", "")):
        stages.append(Stage(id=mid, title=title, detail="", done=done))
    return stages


def _phase_rows(text: str) -> list[tuple[str, str]]:
    """``Phase 1  数据层：数据获取/清洗/存储``（代码块里那种写法）＝ 一个阶段。

    有档案的路线图写在一段代码块里而不是表格里——那张表抽不出阶段，就从这段文字抽。
    """
    rows: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip().strip("`").strip()
        match = _PHASE_RE.match(line)
        if not match:
            continue
        label = match.group(1).strip()
        rest = match.group(2).strip()
        head, sep, tail = rest.partition("：")
        if not sep:
            head, sep, tail = rest.partition(":")
        title = f"{label} {head}".strip() if head else label
        rows.append((title, tail.strip() if sep else ""))
    return rows


def _roadmap_rows(text: str) -> list[tuple[str, str]]:
    """``| # | 阶段 | 交付标准 |`` 那张表：跳过表头与分隔行。"""
    rows: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        if set(cells[0]) <= {"-", ":", " "} or cells[0].casefold() == "#":
            continue
        title = cells[1]
        detail = cells[2] if len(cells) > 2 else ""
        if not title:
            continue
        rows.append((title, detail))
    return rows


def _milestone_flags(text: str) -> list[bool]:
    """``## Milestones`` 那一串复选框：勾了是 ``True``。"""
    flags: list[bool] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("- [ ]"):
            flags.append(False)
        elif line.startswith("- [x]") or line.startswith("- [X]"):
            flags.append(True)
    return flags


def _milestone_items(text: str) -> list[tuple[str, str, bool]]:
    """``- [x] M1：某件事`` ⇒ ``("M1", "某件事", True)``。"""
    items: list[tuple[str, str, bool]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not (line.startswith("- [ ]") or line.startswith("- [x]") or line.startswith("- [X]")):
            continue
        done = line[3] in "xX"
        body = line[5:].strip()
        if not body:
            continue
        marker, sep, rest = body.partition("：")
        if not sep:
            marker, sep, rest = body.partition(":")
        if sep:
            items.append((marker.strip(), rest.strip(), done))
        else:
            items.append((f"m{len(items) + 1}", body, done))
    return items


def _looks_done(text: str) -> bool:
    """表里写着「已完成」就算完成（没有 Milestones 那一节时的兜底）。"""
    lowered = text.casefold()
    return "已完成" in text or "done" in lowered or "完成" in text
