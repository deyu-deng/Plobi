"""Plobi 自己开出来的 ACP 会话台账：纯 ACP 客席的「会话面」。

为什么要有这一份：29 家纯 ACP 客席（goose／droid／devin／junie／glm…）本机读不到
它们的会话库，活却是我们派进去的、会话号也在我们手上。以前这个号只跟着那一趟的
回报走（``RoundLedger`` 一行），下一趟就没人记得 ⇒ 左栏「外部会话」里根本没有它：
用户点不到，模型也捡不回那个号，接不上就只能从头再说一遍。

这份台账补的就是那一格：**派活落一行**（哪家、哪个号、哪个项目目录、第一句说的是
什么、末次回的是什么、接着说到了第几轮），**续接改同一行**。会话面照它出数，
``session/send`` 照它走 ACP ``session/load`` —— 与实测过的那两家（qoder／opencode）
同一条腿，只是账本在我们自己这边。

**能不能续，不猜**：ACP 的 ``loadSession`` 是客席握手时自己宣告的，静态表里断言
不了（断言了就是编）。所以行里留 ``resume_state`` 三态：

- ``unknown``：派进去过，还没试过续（会话面照实说「没试过」）；
- ``ok``：试过、真接上了（之后一路照常）；
- ``no``：试过、它没宣告 ``loadSession``（或那条腿当场拒了）⇒ 不再勾引用户点它。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

__all__ = [
    "ENV_LEDGER",
    "LEDGER_FILENAME",
    "RESUME_NO",
    "RESUME_OK",
    "RESUME_UNKNOWN",
    "as_sessions",
    "find",
    "forget",
    "ledger_path",
    "record",
    "rows",
]

#: 台账落在哪：默认 ``$PLOBI_HOME/external_sessions/acp_sessions.json``，测试可用它顶掉。
ENV_LEDGER = "PLOBI_ACP_LEDGER"
LEDGER_FILENAME = "acp_sessions.json"
VERSION = 1

RESUME_UNKNOWN = "unknown"
RESUME_OK = "ok"
RESUME_NO = "no"
RESUME_STATES = (RESUME_UNKNOWN, RESUME_OK, RESUME_NO)

#: 末次回复存多长：够模型认出「刚才说到哪儿」，又不会把台账撑成另一个聊天库。
MAX_REPLY_CHARS = 600
#: 标题存多长：与外部会话面同一条规矩（120 字，第一行）。
MAX_TITLE_CHARS = 120


def ledger_path() -> Path:
    """台账文件在哪。"""
    override = (os.environ.get(ENV_LEDGER) or "").strip()
    if override:
        return Path(override).expanduser()
    from plobi_constants import get_plobi_home

    return get_plobi_home() / "external_sessions" / LEDGER_FILENAME


def _key(vendor: str, session_id: str) -> str:
    return f"{str(vendor or '').strip().lower()}:{str(session_id or '').strip()}"


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _load() -> dict[str, Any]:
    """读整本。读不动就当空账：**台账坏了不许把会话面整格掀掉**。"""
    try:
        payload = json.loads(ledger_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": VERSION, "sessions": {}}
    if not isinstance(payload, dict):
        return {"version": VERSION, "sessions": {}}
    sessions = payload.get("sessions")
    if not isinstance(sessions, dict):
        payload["sessions"] = {}
    return payload


def _save(payload: dict[str, Any]) -> bool:
    path = ledger_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        return False

    return True


def _clean(value: Any, limit: int = 0) -> str:
    text = str(value or "").strip()
    return text[:limit] if limit else text


def record(
    vendor: str,
    session_id: str,
    *,
    project: str = "",
    title: str = "",
    purpose: str = "",
    reply: str = "",
    source: str = "",
    resume_state: str = "",
    at: str = "",
) -> dict[str, Any] | None:
    """记一行（已在这一行上接着说的，改成同一行，不新开一条）。

    缺什么就不写什么：``project`` / ``title`` 空着就保留原值，**不用空串盖掉**已有的。
    """
    vendor = str(vendor or "").strip().lower()
    session_id = str(session_id or "").strip()
    if not vendor or not session_id:
        return None

    payload = _load()
    sessions = payload.setdefault("sessions", {})
    key = _key(vendor, session_id)
    row = sessions.get(key)
    now = at or _stamp()

    if not isinstance(row, dict):
        row = {
            "vendor": vendor,
            "session_id": session_id,
            "project": "",
            "title": "",
            "purpose": "",
            "reply": "",
            "source": "",
            "turns": 0,
            "resume_state": RESUME_UNKNOWN,
            "created_at": now,
            "updated_at": now,
        }

    if project:
        row["project"] = _clean(project)
    if purpose:
        row["purpose"] = _clean(purpose)
    if source:
        row["source"] = _clean(source)
    # 标题只认第一次说的那句：后面接着说，第一句才认得出这是哪件事。
    if title and not _clean(row.get("title")):
        row["title"] = _clean(title, MAX_TITLE_CHARS)
    if reply:
        row["reply"] = _clean(reply, MAX_REPLY_CHARS)
        row["turns"] = int(row.get("turns") or 0) + 1
    state = _clean(resume_state).lower()
    if state in RESUME_STATES:
        row["resume_state"] = state
    row["updated_at"] = now

    sessions[key] = row
    payload["version"] = VERSION
    _save(payload)

    return dict(row)


def rows() -> list[dict[str, Any]]:
    """整本账，最近的在前。"""
    sessions = _load().get("sessions") or {}
    items = [dict(row) for row in sessions.values() if isinstance(row, dict)]
    items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)

    return items


def find(vendor: str, session_id: str) -> dict[str, Any] | None:
    """这一家的这个号在不在我们账上。"""
    sessions = _load().get("sessions") or {}
    row = sessions.get(_key(vendor, session_id))
    return dict(row) if isinstance(row, dict) else None


def forget(vendor: str, session_id: str) -> bool:
    """从账上划掉一行（会话本体不归我们管，划掉的只是我们自己的记录）。"""
    payload = _load()
    sessions = payload.setdefault("sessions", {})
    if sessions.pop(_key(vendor, session_id), None) is None:
        return False

    return _save(payload)


def as_sessions() -> list[dict[str, Any]]:
    """投影成外部会话面认的那一行（与 ``external_sessions._entry`` 同形）。"""
    out: list[dict[str, Any]] = []
    for row in rows():
        out.append({
            "id": str(row.get("session_id") or ""),
            "vendor": str(row.get("vendor") or ""),
            # 账在我们这边、号也在 ⇒ 这一行点得动；真能不能续看 `resume_state`。
            "sendable": str(row.get("resume_state") or RESUME_UNKNOWN) != RESUME_NO,
            "title": str(row.get("title") or row.get("session_id") or ""),
            "project": str(row.get("project") or ""),
            "updated_at": str(row.get("updated_at") or ""),
            "resume_state": str(row.get("resume_state") or RESUME_UNKNOWN),
            "via": "acp",
            "turns": int(row.get("turns") or 0),
        })

    return out
