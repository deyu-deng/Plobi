"""个人记忆的读口与「忘掉」——设置里那一页的背面（用户 2026-10-30 拍：只做个人记忆）。

三份东西，一台机器上的一个人看：
- `GLOBAL.md`（共用纪律，跨 Agent）
- `USER.md`（画像精简版，共用人）
- `agents/<name>/MEMORY.md`（这个 Agent 自己那份）

「忘掉」按用户拍的那条走：**真删，并记一笔**——从那一份里删掉这一条，同时往
`$PLOBI_HOME/memory/deletions.jsonl` 追加一行（谁、哪一份、删了什么、什么时候）。
不保留隐藏副本，但删了什么永远查得到（这也是"它下次不该再学回来"的依据）。

写侧不在这里：写走 `tools.memory_tool` 的待确认层（`write_approval`），读与删只在这里。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse

PREFIX = "/api/memory"

# scope → (人话标签, 上限键)。上限与写入侧同源（memory_limits_from_config）。
_SCOPES = {
    "global": ("共用纪律", "global_char_limit"),
    "user": ("你的画像（精简版）", "user_char_limit"),
    "memory": ("这个 Agent 自己的记忆", "memory_char_limit"),  # 键名以 memory_limits_from_config 为准
    "status": ("这个 Agent 的状态", "status_char_limit"),
}
_SCOPE_ORDER = ("global", "user", "memory", "status")


def _split_entries(text: str) -> List[str]:
    """`§` 分隔的条目（一行只有一个 `§` 就是分隔符，行内出现 `§` 不算）。"""
    blocks: List[str] = []
    current: List[str] = []
    for line in (text or "").splitlines():
        if line.strip() == "§":
            block = "\n".join(current).strip()
            if block:
                blocks.append(block)
            current = []
            continue
        current.append(line)
    tail = "\n".join(current).strip()
    if tail:
        blocks.append(tail)
    return blocks


def _join_entries(entries: List[str]) -> str:
    return "\n§\n".join(e.strip() for e in entries if e.strip()) + ("\n" if entries else "")


def _limits() -> Dict[str, int]:
    from tools.memory_tool import memory_limits_from_config

    try:
        from plobi_cli.config import load_config

        mem = (load_config() or {}).get("memory") or {}
    except Exception:  # noqa: BLE001 - 配置读不到就用出厂上限
        mem = {}
    return memory_limits_from_config(mem)


def _target(scope: str, agent: Optional[str]) -> Path:
    from tools.memory_tool import memory_path

    if scope in ("memory", "status"):
        return memory_path(scope, slug=(agent or None))
    return memory_path(scope)


def _deletions_path() -> Path:
    from tools.memory_tool import get_memory_dir

    return get_memory_dir() / "deletions.jsonl"


def _streaks() -> Dict[str, int]:
    """每个作用域"连续采纳了几次"——屏幕上要能解释"为什么这条还在问我"。"""
    try:
        from plobi.mind.proposal import TrustLedger

        ledger = TrustLedger()
        return {
            scope: int(ledger.entry(scope, "l0").streak)
            for scope in _SCOPE_ORDER
        }
    except Exception:  # noqa: BLE001 - 账本读不到就都算 0（＝要多问几次，不是放行）
        return {scope: 0 for scope in _SCOPE_ORDER}


def _snapshot(agent: Optional[str]) -> Dict[str, Any]:
    from tools.memory_tool import resolve_agent_slug

    slug = (agent or "").strip() or resolve_agent_slug()
    limits = _limits()
    streaks = _streaks()
    try:
        from plobi.mind.proposal import AUTO_THRESHOLD as _threshold
    except Exception:  # noqa: BLE001
        _threshold = 8
    scopes = []
    for key in _SCOPE_ORDER:
        label, limit_key = _SCOPES[key]
        path = _target(key, slug)
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        entries = _split_entries(text)
        scopes.append({
            "key": key,
            "label": label,
            "present": path.is_file(),
            "chars": len(text.strip()),
            "limit": int(limits.get(limit_key, 0) or 0),
            "entries": [{"index": i, "text": e} for i, e in enumerate(entries)],
            "streak": streaks.get(key, 0),
            "threshold": int(_threshold),
        })
    return {"agent": slug, "scopes": scopes}


def register_memory_ledger_api(app: FastAPI) -> None:
    """挂两个口：读三份、忘掉一条。"""

    @app.get(f"{PREFIX}/ledgers", name="memory_ledgers")
    async def memory_ledgers(agent: str = "") -> JSONResponse:
        return JSONResponse(_snapshot(agent))

    @app.post(f"{PREFIX}/forget", name="memory_forget")
    async def memory_forget(body: Dict[str, Any]) -> JSONResponse:
        return JSONResponse(_forget(body or {}))


def _forget(body: Dict[str, Any]) -> Dict[str, Any]:
    scope = str(body.get("scope") or "").strip()
    agent = str(body.get("agent") or "").strip() or None
    if scope not in _SCOPES:
        return {"ok": False, "error": f"不认识的记忆分区：{scope or '(空)'}"}
    try:
        index = int(body.get("index"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "要删哪一条得给序号"}

    path = _target(scope, agent)
    if not path.is_file():
        return {"ok": False, "error": "这一份还没有内容"}
    entries = _split_entries(path.read_text(encoding="utf-8"))
    if index < 0 or index >= len(entries):
        return {"ok": False, "error": f"序号越界：这一份只有 {len(entries)} 条"}

    removed = entries[index]
    left = entries[:index] + entries[index + 1:]
    path.write_text(_join_entries(left), encoding="utf-8")

    # 真删 + 记一笔：删了什么永远查得到，但不留一份隐藏副本。
    record = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scope": scope,
        "agent": (agent or _snapshot(agent)["agent"]),
        "text": removed,
        "left": len(left),
    }
    try:
        _deletions_path().parent.mkdir(parents=True, exist_ok=True)
        with open(_deletions_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass  # 记不上账不该让删本身失败

    out = _snapshot(agent)
    out.update({"ok": True, "removed": removed, "ledger": str(_deletions_path())})
    return out
