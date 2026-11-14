"""笔记那一族（``note`` 门类）的**注册表工具**。

从 provider 搬过来（``WP-HUMAN-REGISTRY-TOOLS``，11-14）：**provider 只管脑页**，
人读面（笔记／日记／档案）那 17 把改成注册表工具 ⇒ 门类 note／diary／dossier 在
``toolsets.py`` 里那一层**一个字没动**，Tools 页那一格照样管用。

这一族八把：

* ``note_read``／``note_write``／``note_rename``／``note_delete`` —— 基本四把；
* ``note_list``／``note_links``／``note_set_property`` —— 细动作（11-13 补）；
* ``agent_notes`` —— 列自己名下有哪些笔记。

**schema 从 provider 逐字搬过来**——那些描述文案是给模型看的契约，一个字不动。
**闸也不松**：写文件的那几把仍走 ``MindWriter``（信任闸），改名与删除仍**永远先问**。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

from tools.human_common import (
    FACE_HINTS,
    NOTE_PREFIX,
    agent_note_aliases,
    current_identity,
    face_guard,
    json_result,
    read_face,
    roster_entry_for,
    to_logical,
    write_face,
)

logger = logging.getLogger(__name__)

NOTE_READ_TOOL = "note_read"
NOTE_WRITE_TOOL = "note_write"
NOTE_RENAME_TOOL = "note_rename"
NOTE_DELETE_TOOL = "note_delete"
AGENT_NOTES_TOOL = "agent_notes"
NOTE_LIST_TOOL = "note_list"
NOTE_LINKS_TOOL = "note_links"
NOTE_SET_PROPERTY_TOOL = "note_set_property"

NOTE_READ_SCHEMA = {
    "name": NOTE_READ_TOOL,
    "description": (
        "Read one note on the human side by its LOGICAL path, ``Notes/...`` — "
        "e.g. ``Notes/<topic>/<title>.md``. A note carries a hard contract in "
        "its frontmatter: ``title``, ``tags`` and ``belongs`` (the agent "
        "names it belongs to; ``[]`` = public knowledge), optionally "
        "``relates``. Only paths under ``Notes/`` resolve — a dossier "
        "record is ``dossier_read``, today's diary is ``diary_read``, the "
        "brain's own pages are ``mind_read``."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Logical path of the note, e.g. Notes/<topic>/<title>.md",
            }
        },
        "required": ["path"],
    },
    }

NOTE_WRITE_SCHEMA = {
    "name": NOTE_WRITE_TOOL,
    "description": (
        "Overwrite one note on the human side (``Notes/...``) by its LOGICAL "
        "path. Notes carry a hard contract: frontmatter must carry title, "
        "tags and a `belongs` key (`[]` = public knowledge, otherwise "
        "registered agent names), may carry `relates` for agents whose tasks "
        "use it, have exactly one top-level heading matching the title, and "
        "live under an existing topic folder — miss any of those and the "
        "write is refused with the reasons listed. The write is checked by "
        "the trust gate — the result says whether it landed, went to the "
        "pending queue (with an id), or was refused, and why. Never report "
        "success the model did not read back. The diary and the dossier "
        "records are other tools (``diary_write`` / ``dossier_write``)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Logical path of the note, e.g. Notes/<topic>/<title>.md",
            },
            "content": {
                "type": "string",
                "description": (
                    "The full new contents of that note (markdown, with the "
                    "frontmatter block)."
                ),
            },
        },
        "required": ["path", "content"],
    },
    }

NOTE_RENAME_SCHEMA = {
    "name": NOTE_RENAME_TOOL,
    "description": (
        "Rename one note on the human side (``Notes/...``). The file name, the "
        "frontmatter title, the top-level heading and every ``[[wikilink]]`` "
        "pointing at it move together — the note keeps one name, and the links "
        "into it keep working. Give the new name without ``.md`` and without "
        "path separators. Never automatic: this always waits for the user's "
        "approval, and says so with a pending id."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Logical path of the note, e.g. Notes/<topic>/<title>.md",
            },
            "name": {
                "type": "string",
                "description": "The new name (no .md, no path separators).",
            },
        },
        "required": ["path", "name"],
    },
    }

NOTE_DELETE_SCHEMA = {
    "name": NOTE_DELETE_TOOL,
    "description": (
        "Delete one note on the human side (``Notes/...``). It goes to the "
        "vault's trash and can be restored from there. Never automatic: this "
        "always waits for the user's approval, and says so with a pending id."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Logical path of the note, e.g. Notes/<topic>/<title>.md",
            },
        },
        "required": ["path"],
    },
    }

AGENT_NOTES_SCHEMA = {
    "name": AGENT_NOTES_TOOL,
    "description": (
        "List the notes that belong to you on the human side — the same list "
        "the sidebar shows, by the same rule (a note's frontmatter ``belongs`` "
        "names you). You get titles, logical paths and sizes, never note "
        "bodies. Use it to see which notes exist before reading or writing "
        "one; to read a note, use ``note_read``."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "description": (
                    "Whose notes to list — by **name** (the name the user gave "
                    "that agent). Leave it out to list your own."
                ),
            }
        },
        "required": [],
    },
    }

NOTE_LIST_SCHEMA = {
    "name": NOTE_LIST_TOOL,
    "description": (
        "List the notes in the human-side library — **every** note, "
        "including the public ones (``belongs: []``) that belong to no "
        "agent. You get titles, logical paths and sizes, never note "
        "bodies. Use it to find out what exists before reading or "
        "writing one — ``note_read`` works by logical path, so without "
        "a list you can only reach notes whose path you already know. "
        "Pass ``agent`` to narrow it to one agent's notes instead."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "description": (
                    "Whose notes to list — by **name** (the name the user "
                    "gave that agent). Leave it out to list the whole "
                    "library."
                ),
            }
        },
        "required": [],
    },
    }

NOTE_LINKS_SCHEMA = {
    "name": NOTE_LINKS_TOOL,
    "description": (
        "Show how one note is wired into the library: the notes it "
        "links out to, and the notes that link back to it. Links to "
        "notes that do not exist yet are listed as they are written "
        "(the screen shows them as “not created”). Use it to see "
        "what a note touches before you rewrite or delete it; to read "
        "a note, use ``note_read``."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Logical path of the note, e.g. Notes/<topic>/<title>.md",
            }
        },
        "required": ["path"],
    },
    }

NOTE_SET_PROPERTY_SCHEMA = {
    "name": NOTE_SET_PROPERTY_TOOL,
    "description": (
        "Change a note's identity metadata — its ``tags``, who it "
        "``belongs`` to, which agents it ``relates`` to — **without "
        "rewriting the body**. The title is the note's name (the file "
        "name and every link into it move with it), so use "
        "``note_rename`` for that. The write goes through the same "
        "checks as ``note_write``: the note contract is re-checked and "
        "the trust gate decides whether it lands or waits for approval."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Logical path of the note, e.g. Notes/<topic>/<title>.md",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The note's topic words (replaces the current ones).",
            },
            "belongs": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "The agents this note belongs to — registered agent "
                    "names, or ``[]`` for public knowledge."
                ),
            },
            "relates": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The agents whose tasks use this note.",
            },
        },
        "required": ["path"],
    },
    }


# ── 改 frontmatter 的那几格（正文一个字不动） ────────────────────────────────
#
# 这几格怎么写，**格式真相在 :mod:`plobi.human.note_manage`**（frontmatter 的切法、
# 标量的引号、行尾那句 ``# 备注`` 怎么办，都在那儿）——这里只借用它那几个小工具，
# 不另开一份格式口径。

_KEY_LINE_RE = re.compile(r"^([ \t]*)([A-Za-z_][\w-]*)([ \t]*:[ \t]*)(.*)$")


def _yaml_flow_list(values) -> str:
    """一行写完的 YAML 列表（``[数学, 物理]``）——引号交给 yaml 决定。"""
    from plobi.human.note_manage import _yaml_scalar

    return "[" + ", ".join(_yaml_scalar(v) for v in values) + "]"


def _rewrite_frontmatter_keys(block: str, changes: Dict[str, Any]) -> str:
    """把 frontmatter 里指定的那几格换掉；其余行（含行尾备注）原样留着。"""
    from plobi.human.note_manage import (
        _split_line_ending,
        _split_yaml_comment,
        _yaml_scalar,
    )

    lines = block.splitlines(keepends=True)

    # 每一格占的行段：块式列表（值在行尾是空的）的续行归它
    spans: Dict[str, tuple] = {}
    index = 0
    while index < len(lines):
        content = lines[index].rstrip("\r\n")
        match = _KEY_LINE_RE.match(content)
        if not match:
            index += 1
            continue
        key = match.group(2)
        end = index + 1
        if not match.group(4).strip():
            while end < len(lines) and lines[end][:1] in (" ", "\t"):
                end += 1
        spans[key] = (index, end)
        index = end

    out: List[str] = []
    index = 0
    while index < len(lines):
        content = lines[index].rstrip("\r\n")
        match = _KEY_LINE_RE.match(content)
        if not match or match.group(2) not in changes:
            out.append(lines[index])
            index += 1
            continue
        key = match.group(2)
        start, end = spans[key]
        _body, ending = _split_line_ending(lines[start])
        _value, comment = _split_yaml_comment(match.group(4))
        tail = (" " + comment) if comment else ""
        value = changes[key]
        rendered = (
            _yaml_flow_list(value)
            if isinstance(value, (list, tuple))
            else _yaml_scalar(value)
        )
        out.append(f"{match.group(1)}{key}: {rendered}{tail}{ending}")
        index = end  # 块式列表的续行一起吃掉
    return "".join(out)


def set_note_properties(
    text: str,
    *,
    tags=None,
    belongs=None,
    relates=None,
) -> str:
    """改一篇笔记 frontmatter 的那几格，**正文一个字不动**。

    为什么只这三格：**标题**就是那篇的名字（文件名与入链都跟它一起动）⇒ 改它是改名，
    走 ``note_rename`` 那条路；这里管的是「这篇归谁、有哪些主题词、与谁相关」这三样
    **身份元数据**。

    行尾那句 ``# 备注`` 原样留着（那是用户自己写在那儿的话）；frontmatter 里没有的
    那一格**不加**：不替用户造结构。
    """
    from plobi.human.note_manage import _split_frontmatter

    changes: Dict[str, Any] = {}
    if tags is not None:
        changes["tags"] = list(tags)
    if belongs is not None:
        changes["belongs"] = list(belongs)
    if relates is not None:
        changes["relates"] = list(relates)
    if not changes:
        return text
    block, sep, body = _split_frontmatter(text)
    if not sep:
        return text
    return "---" + _rewrite_frontmatter_keys(block, changes) + sep + body


# ── 八把手 ──────────────────────────────────────────────────────────────────


def note_read(args: Dict[str, Any], **_kw: Any) -> str:
    """按逻辑路径读一篇笔记（``Notes/<主题>/<名字>.md``）。"""
    return json_result(read_face(args, (NOTE_PREFIX,), FACE_HINTS[NOTE_PREFIX]))


def note_write(args: Dict[str, Any], **_kw: Any) -> str:
    """整篇覆写一篇笔记（过笔记硬契约 ＋ 信任闸）。"""
    return json_result(write_face(args, (NOTE_PREFIX,), FACE_HINTS[NOTE_PREFIX]))


def _manage(args: Dict[str, Any], kind: str) -> Dict[str, Any]:
    """改名与删除：**只出计划，永远先问**（用户 10-23：永不自动＝日记＋画像＋删除）。

    屏上那一格（人右键）是直接落盘的；Agent 这一侧不一样——这两样动的是**人的东西**
    （改一个文件名会牵动指向它的双链，一删就少一篇）⇒ 一律进待批，盘上一个字节不许动。

    走自己的载荷（``kind: "note_manage"``）而不是 L1 那张 ``mind_plan``：
    ``apply_plan`` 只认 ``MEMORY_ZONES``（用户 10-24／10-25「记忆只落脑」），
    ``Notes/…`` 不在里面 ⇒ 走那条路，点了头也落不下去。

    批准那一趟由 :func:`plobi_cli.write_approval_commands._apply_one` 里新加的一支执行
    （批准的**行为**只有一处实现，不在这里另写一遍 apply）。
    """
    from plobi.human import note_manage
    from plobi.mind.paths import is_safe_relative

    payload = args or {}
    shown = str(payload.get("path") or "").strip().replace("\\", "/")
    path = to_logical(shown)
    if not path:
        return {"success": False, "error": "path is required"}
    off = face_guard(path, (NOTE_PREFIX,), FACE_HINTS[NOTE_PREFIX])
    if off:
        return off
    if not is_safe_relative(path):
        return {"success": False, "error": f"{shown} 不在这一枚工具的面里"}
    try:
        if kind == note_manage.RENAME:
            name = str(payload.get("name") or "").strip()
            if not name:
                return {"success": False, "error": "name is required"}
            op = note_manage.plan_rename(path, name)
        else:
            op = note_manage.plan_delete(path)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - 算不出来也要回话，不能崩掉这一轮
        logger.warning("[human] %s(%s) failed: %s", kind, path, exc)
        return {"success": False, "error": str(exc)}

    from tools import write_approval as wa

    linked = sum(1 for change in getattr(op, "changes", ()) if change.path != op.new_path)
    if kind == note_manage.RENAME:
        summary = f"改名 {op.path} → {op.new_path}"
        if linked:
            summary += f"（连带 {linked} 篇双链）"
    else:
        summary = f"删除 {op.path}（进回收站，可恢复）"
    record = wa.stage_write(
        wa.MEMORY,
        {"kind": "note_manage", "op": json.loads(op.to_json())},
        summary=summary,
        origin="foreground",
    )
    pending_id = str(record.get("id") or "")
    return {
        "success": False,
        "staged": True,
        "pending_id": pending_id,
        "path": shown,
        "detail": (
            "这一笔没有落盘：改名与删除永远先问。看：/memory pending · "
            f"点头：/memory approve {pending_id} · 驳回：/memory reject {pending_id}"
        ),
    }


def note_rename(args: Dict[str, Any], **_kw: Any) -> str:
    """给一篇笔记改名（文件名、frontmatter 标题、一级标题与所有入链一起动）。永远先问。"""
    return json_result(_manage(args, "rename"))


def note_delete(args: Dict[str, Any], **_kw: Any) -> str:
    """删一篇笔记（进回收站，可恢复）。永远先问。"""
    return json_result(_manage(args, "delete"))


def agent_notes(args: Dict[str, Any], **_kw: Any) -> str:
    """**列**当前 Agent 名下的笔记——与屏上「相关笔记」同一份投影。

    同一份投影＝同一套规则：笔记的 frontmatter ``belongs`` 里点了谁的名，
    那一篇就算谁的（``plobi.human.notes_view`` 是那一处真源，屏上与这里共用）。
    纪律同屏上：只给标题、逻辑路径与字数，**不给正文**。
    """
    from plobi.human.notes_view import agent_aliases, notes_for, scan_notes
    from plobi.mind.paths import MindUnavailable

    from tools.human_common import resolve_roots_or_error

    payload = args or {}
    raw = str(payload.get("agent") or "").strip()
    who = raw or current_identity()
    if not who:
        return json_result({
            "success": False,
            "error": "不知道要列谁的笔记：这一枚工具要一个 Agent 名（不给参数就是列你自己的）。",
        })
    # **模型自己填**的名字要在名册里认得出来——否则它会拿一个自己猜的词来问
    # （用户 11-12 22:32 那一轮它猜的是 `default`，那是 L1 那一格的作用域键，
    # 不是人也不是 Agent），答「0 篇」还会被它当成「库是空的」。
    # 不给参数（列自己的）不设这道门：会话身份本来就不一定在名册里。
    if raw and roster_entry_for(raw) is None:
        return json_result({
            "success": False,
            "error": f"名册里没有叫「{raw}」的 Agent。不给参数就是列你自己的笔记。",
        })
    try:
        roots = resolve_roots_or_error()
    except MindUnavailable as exc:
        return json_result({"success": False, "error": str(exc)})
    try:
        scan = scan_notes(roots.mind)
    except OSError as exc:
        return json_result({"success": False, "error": str(exc)})
    aliases = agent_note_aliases(who)
    matched = notes_for(scan, aliases)
    entry = roster_entry_for(who)
    display = str(getattr(entry, "display_name", "") or "").strip() if entry else ""
    return json_result({
        "success": True,
        # 回给模型的是**名字**（用户命名的那个），不是机器键——可见面一律用它。
        "agent": display or who,
        "notes": [
            {"path": note.path, "title": note.title, "chars": note.chars}
            for note in matched
        ],
        "totals": {"scanned": scan.scanned, "matched": len(matched)},
    })


def note_list(args: Dict[str, Any], **_kw: Any) -> str:
    """列人读面那一库（默认**整库**，含公共知识）。

    以前 Agent 只拿得到自己名下那一份（``agent_notes``），而 ``note_read``
    是**按路径**读的 ⇒ 公共知识（``belongs: []``）那一块它连清单都没有，
    更别说路径。这一把把整库的清单给它：标题、逻辑路径、字数、归属。
    **正文一个字不给**（纪律同屏上）。
    """
    from plobi.human.notes_view import notes_for, scan_notes
    from plobi.mind.paths import MindUnavailable

    from tools.human_common import resolve_roots_or_error

    payload = args or {}
    raw = str(payload.get("agent") or "").strip()
    if raw and roster_entry_for(raw) is None:
        return json_result({
            "success": False,
            "error": f"名册里没有叫「{raw}」的 Agent。不给参数就是列整库。",
        })
    try:
        roots = resolve_roots_or_error()
    except MindUnavailable as exc:
        return json_result({"success": False, "error": str(exc)})
    try:
        scan = scan_notes(roots.mind)
    except OSError as exc:
        return json_result({"success": False, "error": str(exc)})
    if raw:
        matched = notes_for(scan, agent_note_aliases(raw))
    else:
        matched = scan.notes
    return json_result({
        "success": True,
        "scope": raw or "library",
        "notes": [
            {
                "path": note.path,
                "title": note.title,
                "chars": note.chars,
                "belongs": list(note.belongs),
            }
            for note in matched
        ],
        "totals": {
            "scanned": scan.scanned,
            "matched": len(matched),
            "library": len(scan.notes),
        },
    })


def note_links(args: Dict[str, Any], **_kw: Any) -> str:
    """一篇笔记的入链与出链——屏上那一格（``/api/surface/notes/links``）的同一份真源。"""
    from plobi.human.notes_links import build_link_graph
    from plobi.mind.paths import (
        MindUnavailable,
        is_safe_relative,
    )

    from tools.human_common import resolve_roots_or_error

    shown = str((args or {}).get("path") or "").strip().replace("\\", "/")
    path = to_logical(shown)
    if not path:
        return json_result({"success": False, "error": "path is required"})
    off = face_guard(path, (NOTE_PREFIX,), FACE_HINTS[NOTE_PREFIX])
    if off:
        return json_result(off)
    if not is_safe_relative(path):
        return json_result({"success": False, "error": f"{shown} 不在可读写的面里"})
    try:
        roots = resolve_roots_or_error()
        graph = build_link_graph(roots.mind)
    except (MindUnavailable, FileNotFoundError) as exc:
        return json_result({"success": False, "error": str(exc)})
    outgoing = list(graph.get("links", {}).get(path, []))
    backlinks = list(graph.get("backlinks", {}).get(path, []))
    if path not in graph.get("titles", {}):
        # 查不到的那一篇要**说清楚**——回一句「没有链接」会被读成「它没被引用过」。
        return json_result({"success": False, "error": f"{shown} 还不存在（路径要对）"})
    return json_result({
        "success": True,
        "path": shown,
        "title": graph.get("titles", {}).get(path, ""),
        "outgoing": outgoing,
        "backlinks": backlinks,
        "totals": {"outgoing": len(outgoing), "backlinks": len(backlinks)},
    })


def note_set_property(args: Dict[str, Any], **_kw: Any) -> str:
    """改一篇笔记的身份元数据（tags / belongs / relates），**正文一个字不动**。

    走 ``note_write`` 那条路（契约复验 ＋ 信任闸 ＋ 原子写，一样都不少）——
    这一把只是**先算出新正文**再交出去，不另开一条写路。

    标题不给改：**标题就是那篇的名字**（文件名与入链都跟它一起动）⇒ 那是改名，
    走 ``note_rename``。
    """
    from plobi.mind.paths import (
        MindUnavailable,
        UnsafeMindPath,
        is_safe_relative,
        safe_target,
    )

    from tools.human_common import resolve_roots_or_error

    payload = args or {}
    shown = str(payload.get("path") or "").strip().replace("\\", "/")
    path = to_logical(shown)
    if not path:
        return json_result({"success": False, "error": "path is required"})
    if payload.get("title"):
        return json_result({
            "success": False,
            "error": (
                "标题就是那篇的名字（文件名与入链都跟它一起动）——"
                "改它用 note_rename；这里只改 tags／belongs／relates。"
            ),
        })
    off = face_guard(path, (NOTE_PREFIX,), FACE_HINTS[NOTE_PREFIX])
    if off:
        return json_result(off)
    if not is_safe_relative(path):
        return json_result({"success": False, "error": f"{shown} 不在可读写的面里"})
    changes: Dict[str, Any] = {}
    for key in ("tags", "belongs", "relates"):
        value = payload.get(key)
        if value is None:
            continue
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return json_result({"success": False, "error": f"{key} 要是一个字符串的列表"})
        changes[key] = [item.strip() for item in value if item.strip()]
    if not changes:
        return json_result({
            "success": False,
            "error": "至少给一格：tags／belongs／relates。",
        })
    try:
        roots = resolve_roots_or_error()
        target = safe_target(roots, path)
    except (MindUnavailable, UnsafeMindPath) as exc:
        return json_result({"success": False, "error": str(exc)})
    if not target.is_file():
        return json_result({"success": False, "error": f"{shown} 还不存在"})
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return json_result({"success": False, "error": str(exc)})
    new_content = set_note_properties(text, **changes)
    result = write_face(
        {"path": path, "content": new_content},
        (NOTE_PREFIX,),
        FACE_HINTS[NOTE_PREFIX],
    )
    if isinstance(result, dict) and result.get("success"):
        result["changed"] = sorted(changes)
    return json_result(result)


# ── 注册 ────────────────────────────────────────────────────────────────────

from tools.registry import registry

registry.register(
    name=NOTE_READ_TOOL,
    toolset="note",
    schema=NOTE_READ_SCHEMA,
    handler=note_read,
    description="Read one note on the human side by its logical path.",
    emoji="📓",
)
registry.register(
    name=NOTE_WRITE_TOOL,
    toolset="note",
    schema=NOTE_WRITE_SCHEMA,
    handler=note_write,
    description="Overwrite one note on the human side (contract + trust gate).",
    emoji="📓",
)
registry.register(
    name=NOTE_RENAME_TOOL,
    toolset="note",
    schema=NOTE_RENAME_SCHEMA,
    handler=note_rename,
    description="Rename one note — always asks first.",
    emoji="📓",
)
registry.register(
    name=NOTE_DELETE_TOOL,
    toolset="note",
    schema=NOTE_DELETE_SCHEMA,
    handler=note_delete,
    description="Delete one note (recoverable) — always asks first.",
    emoji="📓",
)
registry.register(
    name=AGENT_NOTES_TOOL,
    toolset="note",
    schema=AGENT_NOTES_SCHEMA,
    handler=agent_notes,
    description="List the notes that belong to this agent.",
    emoji="📓",
)
registry.register(
    name=NOTE_LIST_TOOL,
    toolset="note",
    schema=NOTE_LIST_SCHEMA,
    handler=note_list,
    description="List the note library (titles, paths, sizes — no bodies).",
    emoji="📓",
)
registry.register(
    name=NOTE_LINKS_TOOL,
    toolset="note",
    schema=NOTE_LINKS_SCHEMA,
    handler=note_links,
    description="Incoming and outgoing links of one note.",
    emoji="📓",
)
registry.register(
    name=NOTE_SET_PROPERTY_TOOL,
    toolset="note",
    schema=NOTE_SET_PROPERTY_SCHEMA,
    handler=note_set_property,
    description="Change a note's tags / belongs / relates — body untouched.",
    emoji="📓",
)
