"""用户那一屏：谁想「对用户说一句话」，落点和提醒都从这里走（裁定 201）。

用户 10-23 拍的口径：「回访 / 提醒这类对你说的话，落在你在 L1 的聊天」，并且
「要有提示用户的手段」。所以「用户」是一个**有名字的收件人**，不是某个 Agent：
`say(to='用户')` 与 cron 的 `deliver=user` 都落到同一个地方。

**落点判据只有这一份**：单库里 `source='desktop'` 且 `agent_id` 为空、未归档的会话里
最近活跃的那一条。2026-10-23 现核：全库 9 条 desktop 会话，符合这一条的只有
``20261007_100942_d4883e`` 一条——其余 8 条都挂着某个 Agent 的名字（那是旧的
per-agent 聊天）。判据与桌面 `plobi.desktop.l1MainSession.default` 指向同一条。

**提醒**：消息落下之后尽量发一条 `user.notice` 网关事件（桌面据此弹原生通知 +
在左栏那一行挂「需要你」的标记）。发不出去不算失败——话已经落进那一屏了，
进程边界（被叫醒的子进程没有 WS 连接）也能解释得通。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from plobi_state import SessionDB

logger = logging.getLogger(__name__)


def user_inbox_session_id() -> Optional[str]:
    """用户那一屏的会话 id；找不到就 ``None``（调用方自己决定怎么报）。"""
    try:
        rows = SessionDB().list_sessions_rich(
            source="desktop", limit=20, order_by_last_active=True, include_archived=False
        )
    except Exception as exc:  # noqa: BLE001 - 读不到就说读不到，别假装有
        logger.warning("user_inbox: 读不到会话总账（%s）", exc)
        return None
    for row in rows:
        if not str(row.get("agent_id") or "").strip():
            return str(row.get("id") or "") or None
    return None


def append_user_message(
    body: str,
    *,
    asked_by: str,
    label: str,
    when: Optional[datetime] = None,
    kind: str = "agent",
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """把一句话落进用户那一屏。返回 ``(session_id, message_id, 错误)``。

    角色用 ``user``：这是别人捎给主人的话，不是主人自己说的（渲染层该按
    `asked_by` / `kind` 区分，而不是靠正文前缀——裁定 196）。
    """
    session_id = user_inbox_session_id()
    if not session_id:
        return None, None, "找不到你那一屏（会话总账里没有 source=desktop 且未挂 Agent 的会话）。"
    stamp = (when or datetime.now()).replace(microsecond=0).isoformat()
    try:
        message_id = SessionDB().append_message(
            session_id,
            role="user",
            content=f"[{label} · {stamp}]\n{body}",
            asked_by=asked_by,
            kind=kind,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("user_inbox: 写不进那一屏（%s）", exc)
        return session_id, None, f"写不进你那一屏：{exc}"
    return session_id, str(message_id), None


def notify_user(body: str, *, title: str, session_id: str = "") -> bool:
    """尽量提醒主人一声；发不出去返回 ``False``，绝不抛进调用方的回合。"""
    try:
        from tui_gateway.server import _emit

        _emit(
            "user.notice",
            session_id or "",
            {"title": title, "body": body, "session_id": session_id},
        )
        return True
    except Exception:  # noqa: BLE001 - 子进程里没有 WS，属正常
        logger.debug("user_inbox: 提醒发不出去（没有网关连接）", exc_info=True)
        return False
