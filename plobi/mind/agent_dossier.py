"""每个 L2 Agent 的标准档案（WP-VAULT-AGENTS-VIEW）。

用户 2026-10-24 定义：每种 Agent 需要的信息不同，但都能用**两三个标准文件**概括；
这些文件和工作目录不是一回事。这里只回答两件事——「这一页有哪几份档案、缺哪份」
与「这份档案的正文」——不搬文件、不建目录、不回写名册。

两条约束：

* 名册只读。``AgentRegistry.load()`` 会顺手自愈并回写 ``project_path``（一个会写的读手），
  这里只借它的 ``AgentEntry`` 与 ``default_path()``，YAML 自己读。
* 缺领地不等于坏。11 门课今天没有 ``mind_subtree``，那就是 11 张「未接入」的卡，
  不是错误，也不许拿工作目录里的文件冒充档案。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

from .dossier_profile import ProfileFile, profile_path, read_profile
from .paths import MindUnavailable, real_target

logger = logging.getLogger(__name__)

# 类别 -> ((键, 中文名, 文件名), …)。顺序就是屏上的顺序。
#
# 11-09 起（WP-DOSSIER-SHAPE）**档案本体是 ``dossier.json``**：目标／状况／阶段都在
# 那一份结构化记录里（见 :mod:`plobi.mind.dossier_profile`），``plan.md``／``progress.md``
# 那一对退役了——「Plan现在占的位置太长了，本质还是md，没有UI设计」。
#
# 这里剩下的 md 是**文档**，不是档案状态：研究笔记与课程那两份。所以它们照旧列出来，
# 但**不参与**目标／状况／阶段的判读。
CATEGORY_DOCS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "projects": (
        ("research", "研究", "research.md"),
    ),
    "research": (
        ("research", "研究", "research.md"),
    ),
    "courses": (
        ("requirements", "课程要求", "requirements.md"),
        ("syllabus", "教学计划", "syllabus.md"),
    ),
    "butler": (),
    "events": (),
}

CATEGORY_LABELS: dict[str, str] = {
    "projects": "项目",
    "research": "研究",
    "courses": "课程",
    "butler": "管家",
    "events": "事件",
}

# 列表只读开头这么多字节：够抽 frontmatter 与里程碑，不把整篇正文搬进列表响应。
LIST_SCAN_BYTES = 16 * 1024
# 详情页一次最多返回这么多；超出如实标 truncated。
MAX_DOC_BYTES = 128 * 1024
SUMMARY_CHARS = 140


@dataclass
class DocCard:
    key: str
    label: str
    filename: str
    exists: bool = False
    path: str = ""
    fs_path: str = ""
    chars: int = 0
    updated: str = ""
    summary: str = ""
    milestones_done: int = 0
    milestones_total: int = 0
    truncated: bool = False


def docs_for_category(category: str) -> tuple[tuple[str, str, str], ...]:
    return CATEGORY_DOCS.get(category, CATEGORY_DOCS["projects"])


def _frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    block = text[3:end]
    try:
        parsed = yaml.safe_load(block)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _milestones(text: str) -> tuple[int, int]:
    done = total = 0
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("- [ ]", "- [x]", "- [X]")):
            total += 1
            if stripped[3] in "xX":
                done += 1
    return done, total


def _body_excerpt(text: str, limit: int = SUMMARY_CHARS) -> str:
    """frontmatter 之后第一段不像标题/分隔线的正文，压成一行。"""
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end >= 0:
            body = text[end + 4 :]
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ">", "|", "```", "---")):
            continue
        line = line.lstrip("-*+ ").strip()
        if not line:
            continue
        return line if len(line) <= limit else line[: limit - 1] + "…"
    return ""


def _file_updated(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()
    except OSError:
        return ""


def _read_limited(path: Path, limit: int) -> tuple[str, bool]:
    try:
        with path.open("rb") as handle:
            raw = handle.read(limit + 1)
    except OSError:
        return "", False
    truncated = len(raw) > limit
    return raw[:limit].decode("utf-8", errors="replace"), truncated


def _home_for(entry: Any) -> Path | None:
    """名册那一格 ``mind_subtree`` 落到盘上的真实目录；没填或根不可用 = None。"""
    logical = (getattr(entry, "mind_subtree", "") or "").strip()
    if not logical:
        return None
    try:
        return real_target(logical)
    except MindUnavailable:
        return None


def _doc_card(home: Path | None, key: str, label: str, filename: str) -> DocCard:
    card = DocCard(key=key, label=label, filename=filename)
    if home is None:
        return card
    path = home / filename
    if not path.is_file():
        return card
    text, _ = _read_limited(path, LIST_SCAN_BYTES)
    meta = _frontmatter(text)
    done, total = _milestones(text)
    summary = str(meta.get("summary") or "").strip() or _body_excerpt(text)
    card.exists = True
    card.path = path.as_posix()
    card.fs_path = str(path)
    card.chars = path.stat().st_size
    card.updated = str(meta.get("updated") or "").strip() or _file_updated(path)
    card.summary = summary
    card.milestones_done = done
    card.milestones_total = total
    return card


def _profile_for(home: Path | None) -> ProfileFile:
    """档案本体。目录都没有 ⇒「还没建立」；文件在但坏了 ⇒「读不到」（两件事分开）。"""
    if home is None:
        return ProfileFile(exists=False, error=None, profile=None)
    return read_profile(profile_path(home))


def _agent_payload(entry: Any, home: Path | None) -> dict[str, Any]:
    """一档档案在**列表**里的那一格。

    目标／现状／阶段都来自档案本体（``dossier.json``）——同一个真源，屏上那张卡与
    喂给 L1 的现状摘要读的是同一份（用户 11-09：「同一个数据」）。

    ``status`` 这一格**不是**现状，是**生命周期状态**（``active``／``paused``…）：
    侧栏那颗点与状态徽章认它（见 ``agent-category-group.tsx``／``agent-progress-pane.tsx``），
    现状在 ``summary`` 那一格。
    """
    category = (getattr(entry, "category", "") or "butler").strip().lower()
    cards = [_doc_card(home, *spec) for spec in docs_for_category(category)]
    profile_file = _profile_for(home)
    profile = profile_file.profile
    stages = profile.stages if profile else []
    latest = max((c.updated for c in cards if c.exists), default="")
    state = str((profile.registry.get("state") if profile else "") or "").strip()
    return {
        "id": entry.name,
        "name": entry.name,
        "displayName": getattr(entry, "display_name", "") or entry.name,
        "category": category,
        "categoryLabel": CATEGORY_LABELS.get(category, category),
        "role": getattr(entry, "role", ""),
        "description": getattr(entry, "description", ""),
        "homeLabel": (getattr(entry, "mind_subtree", "") or "").strip(),
        "homePath": str(home) if home is not None and home.is_dir() else "",
        "hasHome": bool(home is not None and home.is_dir()),
        "workPath": getattr(entry, "project_path", "") or "",
        "workPaths": list(getattr(entry, "project_paths", ()) or ()),
        "status": state,
        "goal": profile.goal if profile else "",
        "summary": profile.status if profile else "",
        "stages": [stage.to_dict() for stage in stages],
        "updated": (profile.updated if profile else "") or latest or (_file_updated(home) if home is not None else ""),
        "milestones": {
            "done": sum(1 for stage in stages if stage.done),
            "total": len(stages),
        },
        "profileExists": profile_file.exists,
        "profileError": profile_file.error or "",
        "docs": [
            {
                "key": c.key,
                "label": c.label,
                "filename": c.filename,
                "exists": c.exists,
                "chars": c.chars,
                "updated": c.updated,
                "path": c.path,
            }
            for c in cards
        ],
    }


def load_entries(roster_path: Path | None = None) -> tuple[Path, list[Any]]:
    """名册路径 + 未归档条目（按名字排序）。**不调用** ``AgentRegistry.load()``。"""
    from plobi.agents.registry import AgentEntry, default_path

    target = roster_path or default_path()
    entries: list[Any] = []
    if not target.is_file():
        return target, entries
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.warning("agent dossier: could not read roster %s: %s", target, exc)
        return target, entries
    rows = raw.get("agents") if isinstance(raw, dict) else None
    if not isinstance(rows, dict):
        return target, entries
    for name, row in rows.items():
        try:
            entry = AgentEntry.from_dict(str(name), row)
        except Exception as exc:
            logger.warning("agent dossier: skipping %s: %s", name, exc)
            continue
        if getattr(entry, "archived", False):
            continue
        entries.append(entry)
    entries.sort(key=lambda e: (e.category, e.name.lower()))
    return target, entries


# 桌面那边的 Agent 作用域哨兵：``'l1'`` = 总秘书。
#
# 它**不是名册里的 id**——名册里 L1 那一行的 name 是 ``secretary``（role
# ``l1_secretary``）。档案跟着 Agent 走 ⇒ 收到作用域的那一格，得先把它翻译成
# 名册那一行，否则档案卡去要 ``/api/surface/agents/l1`` ⇒ 404
# 「agent 'l1' is not registered」（2026-11-08 现场）。
L1_SCOPE = "l1"


def resolve_dossier_id(name: str, roster_path: Path | None = None) -> str:
    """作用域 → 名册 id。认得出就翻译；认不出原样还回去（后面照旧 404）。

    翻译走**名册**（唯一真源）：用户给秘书改名字，映射跟着走，不另立一张对照表。
    名册里没有 L1 那一行时原样还回去——那时候该 404 就 404，不许瞎猜一个。
    """
    from plobi.agents.registry import L1_SECRETARY_ROLE

    text = (name or "").strip()
    if text.casefold() != L1_SCOPE:
        return text
    _, entries = load_entries(roster_path)
    for entry in entries:
        role = str(getattr(entry, "role", "") or "").strip().casefold()
        if role == L1_SECRETARY_ROLE.casefold():
            return entry.name
    return text


def build_index(roster_path: Path | None = None) -> dict[str, Any]:
    target, entries = load_entries(roster_path)
    agents = []
    with_home = 0
    for entry in entries:
        home = _home_for(entry)
        payload = _agent_payload(entry, home)
        with_home += 1 if payload["hasHome"] else 0
        agents.append(payload)
    return {
        "roster": str(target),
        "agents": agents,
        "totals": {
            "agents": len(agents),
            "withHome": with_home,
            "withoutHome": len(agents) - with_home,
        },
    }


def _find(entries: Iterable[Any], name: str) -> Any | None:
    wanted = (name or "").strip().lower()
    for entry in entries:
        if entry.name.lower() == wanted:
            return entry
    return None


def read_agent(name: str, roster_path: Path | None = None) -> dict[str, Any] | None:
    """一个 Agent 的档案正文；名字不在名册里返回 None（调用方给 404）。"""
    _, entries = load_entries(roster_path)
    entry = _find(entries, name)
    if entry is None:
        return None
    home = _home_for(entry)
    payload = _agent_payload(entry, home)
    # 档案本体整份（含**流水**）：目标／状况／阶段／那份历史都在这儿。
    # 列表腿不带走它——流水有十几 KB，25 张卡一起搬会拖垮那一屏。
    payload["profile"] = _profile_for(home).to_dict()
    docs = []
    for key, label, filename in docs_for_category(payload["category"]):
        card = _doc_card(home, key, label, filename)
        text = ""
        truncated = False
        if card.exists:
            text, truncated = _read_limited(Path(card.fs_path), MAX_DOC_BYTES)
        docs.append(
            {
                "key": key,
                "label": label,
                "filename": filename,
                "exists": card.exists,
                "path": card.path,
                "fsPath": card.fs_path,
                "chars": card.chars,
                "updated": card.updated,
                "text": text,
                "truncated": truncated,
            }
        )
    payload["docs"] = docs
    return payload
