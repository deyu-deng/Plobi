"""笔记的硬约束（WP-NOTES-CONTRACT，用户 2026-10-25 答「硬约束，不合规拒写」）。

约束只管 **Agent 往 ``Vault/notes`` 写的那一手**，不管人自己写（人写不需要闸门）。
不合规一律拒写并逐条说清缺了什么——不是"照样写、屏上标黄"，也不是"先只补字段"。

四条规则，都是从这台的真实现状里挑出来的（现核：79 篇笔记里 68 有 frontmatter、
64 有 tags，但**只有 7 篇有 `belongs`** ⇒ 「某个 Agent 的笔记」那一面几乎是空的，
因为 `plobi/mind/notes_view.py` 就是靠 `belongs` 认归属的）：

1. 必须落在 ``Vault/notes/<已有主题>/`` 下——主题目录得先在盘上，不许顺手发明新主题。
2. frontmatter 必填 ``title``／``tags``（至少一个）／``belongs``（至少一个，且名字要在名册里）。
3. 一篇一主题：正文里只许一个一级标题，且它应当就是那一格 ``title``。
4. 索引件（``INDEX.md``、``tags.md``）不在此闸内——它们由脑侧 verifier 与词表那一格管。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

NOTES_LOGICAL_ROOT = "Vault/notes"
REQUIRED_KEYS = ("title", "tags", "belongs")
# 索引与词表不是"一篇笔记"，别拿这套规则去拦生成体。
EXEMPT_FILE_NAMES = ("INDEX.md", "tags.md")


def _frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """返回 (frontmatter, 正文)。没有那一圈 --- 就是空 dict + 原文。"""
    if not content.startswith("---"):
        return {}, content
    end = content.find("\n---", 3)
    if end < 0:
        return {}, content
    block = content[3:end]
    body = content[end + 4 :]
    try:
        parsed = yaml.safe_load(block)
    except Exception as exc:  # 语法坏了的 frontmatter 也算不合规，下面按缺字段报
        return {"__broken__": str(exc)}, body
    if not isinstance(parsed, dict):
        return {"__not_mapping__": True}, body
    return parsed, body


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [part.strip() for part in value.replace("，", ",").split(",")]
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _registered_names() -> set[str]:
    """名册里活着的那些 Agent 名（小写比）。拿不到名册时返回空集 = 不卡名字。"""
    try:
        from .agent_dossier import load_entries

        _path, entries = load_entries()
    except Exception:
        return set()
    return {str(entry.name).lower() for entry in entries}


def _heading_lines(body: str) -> list[str]:
    return [line[2:].strip() for line in body.splitlines() if line.startswith("# ")]


def validate_note(logical: str, content: str, notes_root: Path | None = None) -> list[str]:
    """不合规的原因列表；空列表 = 可以写。

    ``notes_root`` 是 ``Vault/notes`` 落到盘上的那一格；不传就自己解析一次。
    """
    path = (logical or "").strip().replace("\\", "/")
    if not path.startswith(NOTES_LOGICAL_ROOT + "/"):
        return []  # 不是笔记，这一闸不管
    name = path.rsplit("/", 1)[-1]
    if name in EXEMPT_FILE_NAMES:
        return []
    if not path.endswith(".md"):
        return [f"笔记必须是 .md：{path}"]

    problems: list[str] = []
    meta, body = _frontmatter(content)
    if meta.get("__broken__"):
        problems.append(f"frontmatter 解析不了：{meta['__broken__']}")
    elif meta.get("__not_mapping__"):
        problems.append("frontmatter 必须是键值块（--- 包起来的 YAML mapping）")
    elif not meta:
        problems.append("缺 frontmatter：至少要写 title／tags／belongs 三格")

    if meta and not meta.get("__broken__") and not meta.get("__not_mapping__"):
        if not str(meta.get("title") or "").strip():
            problems.append("frontmatter 缺 `title`")
        if not _as_list(meta.get("tags")):
            problems.append("frontmatter 缺 `tags`（至少一个主题词）")
        owners = _as_list(meta.get("belongs"))
        if not owners:
            problems.append("frontmatter 缺 `belongs`——没有它，这篇笔记在「某个 Agent 的笔记」那一面永远不出现")
        else:
            known = _registered_names()
            if known:
                unknown = [owner for owner in owners if owner.lower() not in known]
                if unknown:
                    problems.append("`belongs` 里有名册不认的名字：" + "、".join(unknown))

    headings = _heading_lines(body)
    if len(headings) > 1:
        problems.append(f"一篇只许一个一级标题，这篇有 {len(headings)} 个（拆开或降级）")
    title = str(meta.get("title") or "").strip() if meta else ""
    if headings and title and headings[0] != title:
        problems.append(f"一级标题「{headings[0]}」与 frontmatter 的 title「{title}」不一致")

    if notes_root is None:
        try:
            from .paths import MindUnavailable, real_target

            notes_root = real_target(NOTES_LOGICAL_ROOT)
        except MindUnavailable:
            notes_root = None
    if notes_root is not None:
        relative = Path(path).relative_to(NOTES_LOGICAL_ROOT)
        if len(relative.parts) < 2:
            problems.append(f"笔记要挂在已有主题下（{NOTES_LOGICAL_ROOT}/<主题>/…），这篇直接落在根上")
        else:
            topic = notes_root / relative.parts[0]
            if not topic.is_dir():
                problems.append(f"主题目录还不存在：{relative.parts[0]}/（要新主题先说出来，别顺手发明）")
    return problems
