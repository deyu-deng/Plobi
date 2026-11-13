"""笔记的硬约束（WP-NOTES-CONTRACT，用户 2026-10-25 答「硬约束，不合规拒写」）。

约束只管 **Agent 往 ``Notes`` 写的那一手**，不管人自己写（人写不需要闸门）。
不合规一律拒写并逐条说清缺了什么——不是"照样写、屏上标黄"，也不是"先只补字段"。

五条规则，都是从这台的真实现状里挑出来的（现核 2026-10-27：79 篇笔记里 68 篇有 frontmatter、
63 篇有 tags，但**只有 8 篇有 `belongs`**（全是大学物理那 8 章）⇒ 「某个 Agent 的笔记」
那一面几乎是空的，而 ``plobi/mind/notes_library.py`` 与档案页就是靠 `belongs` 认归属的）：

1. 必须落在 ``Notes/<已有主题>/`` 下——主题目录得先在盘上，不许顺手发明新主题。
2. frontmatter 必填 ``title``／``tags``（至少一个）／``belongs``；``belongs`` **这一格必须在，
   但可以是空列表** —— 空＝公共知识（不属于任何 Agent）。名字必须是名册里活着的。
3. ``relates`` 可选：谁的任务用得上这篇（可多于一方，例如微积分同时被理论力学、SRTP、
   Wealth-Lab 用着）。写了就不能是空列表，名字同样要名册认得。归属与关联是两根轴，
   别拿一根去装两件事。（注意别和存量笔记自带的 ``related`` 混：那一格指的是**别的笔记**。）
4. 一篇一主题：正文里只许一个一级标题（围栏代码块里的 ``#`` 是示例不算标题），
   且它应当就是那一格 ``title``。
5. 索引件（``INDEX.md``、``tags.md``、主题目录的 ``README.md``）不在此闸内——
   它们是目录不是笔记，由脑侧 verifier 与词表那一格管。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .paths import normalize_logical

from .paths import normalize_logical

from .paths import normalize_logical

from .paths import normalize_logical

NOTES_LOGICAL_ROOT = "Notes"
# `belongs` 这一格**必须在**，但值可以是空列表 —— 空＝公共知识（谁都能读，不属于谁）。
# 用户 2026-10-27：「允许空＝公共知识」＋「微积分现在很多 Agent 的任务就和它有关」
# ⇒ 归属与关联拆成两根轴：`belongs` 是谁的资产，`relates` 是谁的任务用得上（可多于一方）。
REQUIRED_KEYS = ("title", "tags", "belongs")
OPTIONAL_LIST_KEYS = ("relates",)
# 索引与词表不是"一篇笔记"，别拿这套规则去拦生成体。
INDEX_FILE_NAMES = ("INDEX.md", "tags.md")
EXEMPT_FILE_NAMES = INDEX_FILE_NAMES + ("README.md",)


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
    """名册里活着的那些 Agent 名（小写比）：**显示名**与机器键（``agent_id``）都算。

    可见面用**用户命名的那个名字**（用户 11-12 22:32：「我明明设置的文字
    『大学物理（甲）II』怎么机器记录的是 ii」）⇒ Agent 照着自己被告知的名字写
    ``belongs``，这道闸就得认它；机器键**继续认**（存量那几篇靠它，改了契约它们
    就找不到了）。

    拿不到名册时返回空集 = 不卡名字。
    """
    try:
        from .agent_dossier import load_entries

        _path, entries = load_entries()
    except Exception:
        return set()
    names: set[str] = set()
    for entry in entries:
        for value in (getattr(entry, "name", ""), getattr(entry, "display_name", "")):
            text = str(value or "").strip()
            if text:
                names.add(text.lower())
    return names


def _heading_lines(body: str) -> list[str]:
    """正文里的一级标题，**跳过围栏代码块**。

    讲 Markdown／Obsidian 的那几篇会在 ``` 里示范 `# 一级标题`——按行扫会把示例当标题，
    一篇合规笔记就被判成「有 18 个一级标题」（2026-10-27 存量核对时实测到的假阳性）。
    """
    headings: list[str] = []
    fence: str | None = None
    for line in body.splitlines():
        stripped = line.strip()
        marker = stripped[:3]
        if fence is None:
            if marker in ("```", "~~~"):
                fence = marker
                continue
            if stripped.startswith("# "):
                headings.append(stripped[2:].strip())
        elif marker == fence:
            fence = None
    return headings


def validate_note(logical: str, content: str, notes_root: Path | None = None) -> list[str]:
    """不合规的原因列表；空列表 = 可以写。

    ``notes_root`` 是 ``Notes`` 落到盘上的那一格；不传就自己解析一次。

    ``Surface/notes/…`` 那套旧写法 2026-11-13 停用（归一已删，不再认）。
    """
    path = normalize_logical((logical or "").strip().replace("\\", "/"))
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
        if "belongs" not in meta:
            problems.append(
                "frontmatter 缺 `belongs` 这一格——公共知识请显式写 `belongs: []`，"
                "不许整个不留（留空分不清「没归属」和「还没想过归属」）"
            )
        elif not isinstance(meta.get("belongs"), (list, tuple, str)):
            problems.append("`belongs` 要写成列表（公共知识用 `[]`）")
        owners = _as_list(meta.get("belongs"))
        relates = _as_list(meta.get("relates"))
        if "relates" in meta and not relates:
            problems.append("写了 `relates` 就得至少一个 Agent；没有相关方就别写这一格")
        known = _registered_names()
        if known:
            for key, names in (("belongs", owners), ("relates", relates)):
                unknown = [name for name in names if name.lower() not in known]
                if unknown:
                    problems.append(f"`{key}` 里有名册不认的名字：" + "、".join(unknown))

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
