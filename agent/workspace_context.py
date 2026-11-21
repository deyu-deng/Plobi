"""工作区上下文注入：让 Agent 知道人现在在工作区打开了什么。

桌面端把打开文件快照写到 ``PLOBI_HOME/runtime/open-files.json``（只写不读），
本模块读它并格式化成一段简洁文本块，注入进每轮 user message 的
API-call-time 副本（见 ``conversation_loop.py`` 的 ``_injections``）。

纪律：
  * 只读元数据（路径/类型/激活/未保存），**不读文件内容**；
  * 快照缺失/坏 JSON/结构不对一律静默返回空，绝不因此卡住对话；
  * 不落库、不进 UI、不改 system prompt（注入走 api_msg 的 copy）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from plobi_constants import get_plobi_home

_SNAPSHOT_REL = Path("runtime") / "open-files.json"
DEFAULT_MAX_FILES = 8


def load_open_files_snapshot(home: Path | str | None = None) -> dict[str, Any] | None:
    """读快照；不存在 / 坏 JSON / 顶层不是对象 → None（静默）。"""

    root = Path(home) if home is not None else get_plobi_home()
    try:
        data = json.loads((root / _SNAPSHOT_REL).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def format_workspace_context(
    snapshot: dict[str, Any] | None, max_files: int = DEFAULT_MAX_FILES
) -> str:
    """把快照格式化成简洁文本块；无有效文件 → 空串。

    激活文件排最前，其余保持快照顺序；超出 ``max_files`` 的只报数量。
    """

    if not snapshot:
        return ""

    files = snapshot.get("files")
    if not isinstance(files, list) or not files:
        return ""

    valid = [f for f in files if isinstance(f, dict) and isinstance(f.get("path"), str) and f["path"]]
    if not valid:
        return ""

    active = [f for f in valid if f.get("active")]
    others = [f for f in valid if not f.get("active")]
    ordered = active + others

    lines = ["[Workspace: the human has these files open]"]
    for f in ordered[:max_files]:
        flags: list[str] = []
        if f.get("active"):
            flags.append("active")
        if f.get("dirty") is True:
            flags.append("unsaved")
        editor = f.get("editor")
        if isinstance(editor, str) and editor:
            flags.append(editor)
        suffix = f" ({', '.join(flags)})" if flags else ""
        lines.append(f"- {f['path']}{suffix}")

    extra = len(ordered) - max_files
    if extra > 0:
        lines.append(f"... and {extra} more")

    return "\n".join(lines)
