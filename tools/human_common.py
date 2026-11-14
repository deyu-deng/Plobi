"""人读面（笔记／日记／档案）那 17 把工具的**共用壳**。

这一族从 provider 里搬出来（``WP-HUMAN-REGISTRY-TOOLS``，11-14）：**provider 只管脑页**，
人读面是**注册表工具**——门类 ``note``／``diary``／``dossier`` 早就在 ``toolsets.py`` 里，
Tools 页能分别开关；这一刀只改**工具从哪来**，不下发口径与默认开关都一个字不动。

共用部分放这里：

* **逻辑路径 → 物理落点**（``plobi.mind.paths`` 的白名单解析：``Notes/…``／``Diary/…``／
  ``Dossiers/…`` 各有自己的家）；
* **一枚工具只碰一类**（面闸：越类时当场说清，不装作读不到）；
* **读写两枚的骨架**：读＝截断；写＝**契约复验 ＋ 信任闸 ＋ 原子写**；
* **「当前是谁」**：会话身份（``PLOBI_AGENT_ID`` → 会话 ContextVar → 家）。

**共享管道没搬**：``plobi.mind.writer`` 的 MindWriter（串行锁 ＋ 白名单 ＋ 信任闸）与
``plobi.mind.paths`` 的解析**两面共用**——搬了就得复制一份，那是更坏的分叉。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# 每把工具**只碰一类**（逻辑前缀）。``Surface/…`` 那套旧写法 2026-11-13 停用。
NOTE_PREFIX = "Notes"
DIARY_PREFIX = "Diary"
DOSSIER_PREFIX = "Dossiers"

VAULT_READ_MAX_CHARS = 24_000
VAULT_WRITE_MAX_CHARS = 60_000

# 档案本体的路径形状：``Dossiers/<类>/<名>/dossier.json``。别的一律不接。
DOSSIER_PATH_PATTERN = re.compile(r"^Dossiers/[^/]+/[^/]+/dossier\.json$")

# 每把工具越界时的那句提示（说清「该用哪一把」）。
FACE_HINTS = {
    NOTE_PREFIX: "笔记用 `note_read`／`note_write`／`note_rename`／`note_delete`",
    DIARY_PREFIX: "日记用 `diary_read`／`diary_write`",
    DOSSIER_PREFIX: "档案用 `dossier_read`／`dossier_write`",
}


def to_logical(path: str) -> str:
    """模型说的路径 → 内部用的逻辑路径（斜杠归一 ＋ 首尾斜杠去掉）。"""
    from plobi.mind.paths import normalize_logical

    return normalize_logical(str(path or "").replace("\\", "/").strip("/"))


def to_spoken(path: str) -> str:
    """反向：回给模型的字符串里不许出现盘上没有的那个词。"""
    return path


def face_guard(logical: str, allowed: tuple[str, ...], hint: str) -> Dict[str, Any] | None:
    """一枚工具只碰一类：越类的形状当场说清，不装作读不到。

    ``plobi/mind/paths.py`` 的白名单比这几类加起来还宽（管道自己写脑的日页面要过它），
    所以这里不是重复检查，是**模型的手**与**管道的写路**各自的面。
    """
    head = logical.strip("/").partition("/")[0]
    if any(head == a or logical == a or logical.startswith(a + "/") for a in allowed):
        return None
    return {
        "success": False,
        "error": (
            f"{to_spoken(logical)} 不在这一枚工具的面里。能碰的形状只有："
            + "、".join(f"`{to_spoken(a)}`" for a in allowed)
            + f"——{hint}。"
        ),
    }


def resolve_roots_or_error():
    """两个家（脑／人读面）解出来；解不出来就抛（调用方把它变成一句人话）。"""
    from plobi.mind.paths import resolve_roots

    roots = resolve_roots()
    if roots is None:
        from plobi.mind.paths import MindUnavailable

        raise MindUnavailable("MIND_ROOT is not set and no Mind surface was found")
    return roots


def current_identity() -> str:
    """**当前是谁**：会话身份。

    真源是 ``plobi.agents.registry._active_agent_profile_name``——先问
    ``PLOBI_AGENT_ID``（spawn 出去的子进程注入）→ 再问会话级 ContextVar（桌面／
    api-server 开的那次 Agent 会话）→ 回退到旧家目录形状。

    provider 原来在 **init 时**问一次（``agent_identity``），这一族改成**调用时**问：
    注册表工具没有自己的实例，身份得现问；多 Agent 后端里也更准。
    """
    try:
        from plobi.agents.registry import _active_agent_profile_name

        identity = (_active_agent_profile_name() or "").strip()
    except Exception:  # noqa: BLE001 - 名册读不到就退到旧口径
        identity = ""
    if identity:
        return identity
    try:
        from plobi_cli.agent_identity import get_active_profile_name

        return str(get_active_profile_name() or "").strip()
    except Exception:  # noqa: BLE001 - 共享层回落进程级那一份
        return ""


def roster_entry_for(name: str) -> Any | None:
    """名册里那一行：**显示名**（用户命名的那个）或 `agent_id` 都认。

    2026-11-12（用户 11-12 22:32 截图「**我明明设置的文字『大学物理（甲）II』怎么机器
    记录的是 ii**」）：可见面一律用**名字**，机器面用 `agent_id` ⇒ 这一只两样都认，
    谁叫得出来都给同一行。
    """
    wanted = (name or "").strip()
    if not wanted:
        return None
    try:
        from plobi.human.agent_dossier import _find, load_entries

        _path, entries = load_entries()
    except Exception:  # noqa: BLE001 - 名册读不到就只按名字认
        return None
    entry = _find(entries, wanted)
    if entry is not None:
        return entry
    folded = wanted.casefold()
    for candidate in entries:
        display = str(getattr(candidate, "display_name", "") or "").strip()
        if display and display.casefold() == folded:
            return candidate
    return None


def agent_note_aliases(name: str) -> tuple[str, ...]:
    """这个 Agent 认领笔记时可能用的那几个名字（**显示名**在前，`agent_id` 与脑树末段也算）。

    与屏上**同一只口**：``plobi.human.notes_view.agent_aliases`` 是那处真源，
    这里只负责把名字换成名册里那一行（换了就查得到 ``mind_subtree``）。
    """
    from plobi.human.notes_view import agent_aliases

    entry = roster_entry_for(name)
    if entry is None:
        return (name,)
    return agent_aliases(entry)


def read_face(args: Dict[str, Any], allowed: tuple[str, ...], face: str = "") -> Dict[str, Any]:
    """读一枚：逻辑路径 → 物理落点 → 正文（截断）。越类／不安全／不存在都当场说清。"""
    from plobi.mind.paths import MindUnavailable, UnsafeMindPath, is_safe_relative, safe_target

    shown = str((args or {}).get("path") or "").strip().replace("\\", "/")
    path = to_logical(shown)
    if not path:
        return {"success": False, "error": "path is required"}
    off = face_guard(path, allowed, face or FACE_HINTS.get(allowed[0], ""))
    if off:
        return off
    if not is_safe_relative(path):
        return {"success": False, "error": f"{shown} 不在可读写的面里"}
    try:
        roots = resolve_roots_or_error()
        target = safe_target(roots, path)
    except (MindUnavailable, UnsafeMindPath) as exc:
        return {"success": False, "error": str(exc)}
    if not target.is_file():
        return {"success": False, "error": f"{shown} 还不存在（读不到不等于空文件，别当它是空的）"}
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"success": False, "error": str(exc)}
    truncated = len(text) > VAULT_READ_MAX_CHARS
    return {
        "success": True,
        "path": shown,
        "content": text[:VAULT_READ_MAX_CHARS],
        "chars": len(text),
        "truncated": truncated,
    }


def write_face(args: Dict[str, Any], allowed: tuple[str, ...], face: str = "") -> Dict[str, Any]:
    """写一枚：逻辑路径 → 白名单 → 契约复验 → **信任闸**（MindWriter）→ 原子写。

    写文件的那几把一律走 :class:`plobi.mind.writer.MindWriter`，因此过 L1 那道闸：
    闸关着时落不了盘，就照实回「已进待批 + id」。
    """
    from plobi.human.notes_contract import validate_note
    from plobi.mind.paths import MindUnavailable, is_safe_relative
    from plobi.mind.writer import MindWriter, WriteRequest

    payload = args or {}
    shown = str(payload.get("path") or "").strip().replace("\\", "/")
    path = to_logical(shown)
    content = payload.get("content")
    if not path:
        return {"success": False, "error": "path is required"}
    if not isinstance(content, str) or not content.strip():
        return {"success": False, "error": "content must be a non-empty string"}
    if len(content) > VAULT_WRITE_MAX_CHARS:
        return {
            "success": False,
            "error": f"content too large ({len(content)} chars > {VAULT_WRITE_MAX_CHARS})",
        }
    off = face_guard(path, allowed, face or FACE_HINTS.get(allowed[0], ""))
    if off:
        return off
    if not is_safe_relative(path):
        return {"success": False, "error": f"{shown} 不在可写的面里"}
    # 笔记那一格另有一道硬约束（用户 10-25 答「不合规拒写」）：缺 title/tags/belongs、
    # 一篇多个一级标题、发明新主题目录，一律拒，并把缺什么逐条说回去。
    notes_problems = validate_note(path, content)
    if notes_problems:
        return {
            "success": False,
            "path": path,
            "error": "笔记不合规，没有写入",
            "problems": notes_problems,
        }
    try:
        roots = resolve_roots_or_error()
        writer = MindWriter(roots.mind)
        result = writer.write([WriteRequest(relative_path=path, content=content)])
    except MindUnavailable as exc:
        return {"success": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - a failed write must not crash the turn
        logger.warning("[human] write(%s) failed: %s", path, exc)
        return {"success": False, "error": str(exc)}

    if result.staged:
        return {
            "success": False,
            "staged": True,
            "pending_id": result.pending_id,
            "path": shown,
            "detail": result.detail,
        }
    if result.ok and path in result.written:
        return {
            "success": True,
            "written": [to_spoken(w) for w in result.written],
            "path": shown,
            "detail": result.detail,
        }
    return {
        "success": False,
        "path": shown,
        "error": "这一笔没有落盘",
        "detail": result.detail or f"{shown} 被写路径的守卫拒了",
    }


def resolve_dossier_by_name(root: Path, name: str) -> Optional[tuple[str, Path]]:
    """按**名字**找一份档案：先按盘上的目录找，再问名册。

    名册那一格 ``mind_subtree`` 才是“这个名字的档案在哪儿”的权威（名册名与
    目录名可能只差大小写：名册 ``srtp`` ↔ 目录 ``SRTP``）。
    """
    from plobi.human import dossier_profile as dp
    from plobi.mind.paths import logical_target, real_target
    from plobi.mind.reader import DOSSIER_CATEGORIES

    for category, relative in DOSSIER_CATEGORIES:
        directory = real_target(f"{relative}/{name}", root)
        if (directory / dp.PROFILE_FILENAME).is_file():
            return f"{relative}/{name}", directory / dp.PROFILE_FILENAME
    try:
        home = dp.home_for_agent(name)
    except Exception:  # noqa: BLE001 - 名册那一格读不到就当没有
        home = None
    if home is not None:
        path = home / dp.PROFILE_FILENAME
        if path.is_file():
            return logical_target(path, root) or "", path
    return None


def json_result(payload: Dict[str, Any]) -> str:
    """工具返回一律是 JSON 串（注册表那层的约定：handler 回字符串或 dict 都行）。"""
    return json.dumps(payload, ensure_ascii=False)
