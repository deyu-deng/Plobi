"""`say` 与 `ask` —— Agent 之间的两枚原子工具（裁定 183）。

**为什么是这两枚**：用户 10-19 的原话「问话和派工或者别的不都是一件事情吗？就是一个 Agent
给另一个 Agent 发了一段提示词吗？」。所以工具面上不再有带 `intent`/`action` 枚举的嘴
（那 7 枚已删），只剩两个动作：**发出去**（`say`）与**发出去并等回答**（`ask`）。

定案六条（逐条落在下面）：

1. `say(to, parts)` 投递即返回一条痕迹 id（默认，像真人）。
2. `ask(to, parts, timeout)` 投递并等；`timeout` 只影响返回，**不影响副作用**；
   超时不丢答上来的那半（沿用 ``agent_ask`` 乙档那套读回，别丢会话号）。
3. `parts` 是多段，每段要么是**文本**，要么是**文件指针**（相对 `vault/`·`mind/`·共享文件根）。
   **禁止 base64 内联进上下文**（炸前缀缓存）——接收方用自己的工具（`read_file`）去读。
4. `from` 由**运行时按当前 Agent 身份**写入，不许模型自报（否则可冒充）。
5. **副作用不由发起方指定**：接话的那个 Agent 用它自己的工具去改日程／勾完成／再往下说。
6. 每条消息落一条有身份的痕（from／to／时间／载荷／回答／这一趟的会话号）——落
   ``PLOBI_HOME/plobi/agent_channel.jsonl``，追加式。

**消息落在哪**：接收方自己那个会话（``PLOBI_STATE_HOME/state.db``），最新一个；没有就开一个
``source="agent_message"`` 的。桌面里那个 Agent 的聊天就是它，不另起界面。

`ask` 的执行沿用 ``plobi-north-star/agent_ask.py`` 那条已经在生产上跑通的 spawn 路子
（子进程 + 子进程自己的会话 + 超时预算）；本模块不再复制一份调度器。
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import agent_ask as AA
from . import wake as WK

logger = logging.getLogger(__name__)

#: 痕的文件名。追加式，一次一行 JSON。
TRACE_FILENAME = "agent_channel.jsonl"

#: 单条消息最大字符数——免得模型把整篇文档贴进来当一段。
_PART_MAX_CHARS = 20000

_PART_ITEM = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "一段文字。"},
        "path": {
            "type": "string",
            "description": "一个文件指针，相对 vault/·mind/·共享文件根；要给路径，不给 base64。",
        },
    },
}

SAY_SCHEMA = {
    "name": "say",
    "description": (
        "给另一个 Agent 说一段话（投递即返回，不等它回，像真人留个言）。\n"
        "派活、追问、转告、把一段材料递给它让它自己看，都用这一枚。\n"
        "`to` 填名册里那个 Agent 的名字（大小写、显示名都认）；"
        "`parts` 是要说的内容，可以好几段，每段要么是文字，要么是**文件指针**"
        "（`{\"path\": \"vault/…\"}`）——**不要把文件内容贴进来**，那样会炸前缀缓存，对方会自己去读。\n"
        "消息落进**对方自己的会话**（同一个单库），他打开那个 Agent 就能看到；"
        "会留下一条带收发双方的痕。\n"
        "对方要改什么、要不要改，由它自己用它的工具决定，你只负责把话说清楚；"
        "需要它**现在**回答你才继续，就改用 `ask`（`ask` 会等它答完）。\n"
        "这一枚就是说话本身：不要开工具菜单、不要列 MCP/skills 让用户手动选、不要让用户改配置。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "接收方的 Agent 名字（名册里那一行的名字）。"},
            "parts": {
                "type": "array",
                "description": "要说的内容，一段或多段。",
                "items": _PART_ITEM,
            },
        },
        "required": ["to", "parts"],
    },
}

ASK_SCHEMA = {
    "name": "ask",
    "description": (
        "给另一个 Agent 说一段话，并且**等它把话说完**再回来（带超时）。\n"
        "需要它现在的回答才能继续时才用这枚；只是交待事情用 `say`，别让两边互相等。\n"
        "`timeout` 只影响你多久没等到，**不影响它那边已经做了什么**；"
        "超时不代表没答——答上来的部分照带回来，别丢。\n"
        "`to`／`parts` 与 `say` 完全一样。这一问一答会留在对方自己的会话里。\n"
        "问不到（名字不对、已归档、超时、它那边起不来）会明确回错，并列出现在能发的人名。\n"
        "这一枚就是问话本身：不要开工具菜单、不要列 MCP/skills 让用户手动选、不要让用户改配置。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "就问哪个 Agent（名册里那一行的名字）。"},
            "parts": {
                "type": "array",
                "description": "要问的内容，一段或多段（文字或文件指针）。",
                "items": _PART_ITEM,
            },
            "timeout": {
                "type": "integer",
                "description": "最多等多少秒；不给就按那一行的实测预算。",
            },
        },
        "required": ["to", "parts"],
    },
}


# ── 收件人 ────────────────────────────────────────────────────────────────


def _roster():
    from plobi.agents.registry import load_registry

    return load_registry()


def live_names() -> list[str]:
    """名册里没归档的那些名字——拒绝时列给模型看，不许它硬猜。"""
    try:
        registry = _roster()
    except Exception:  # noqa: BLE001
        return []
    return [row.name for row in registry.agents.values() if not getattr(row, "archived", False)]


def resolve_recipient(name: str):
    """名册里那一行。解析不到 / 归档 ⇒ ``None`` + 一句人话，不猜。"""
    name = (name or "").strip()
    if not name:
        return None, "没说要发给谁（`to` 不能空）。"
    try:
        registry = _roster()
    except Exception as exc:  # noqa: BLE001 - 名册读不动 ≠ 可以匿名发
        return None, f"读不到名册，这次发不了（{exc}）。"
    entry = registry.get(name)
    if entry is None:
        lowered = name.casefold()
        entry = next(
            (row for row in registry.agents.values() if row.name.casefold() == lowered), None
        )
    if entry is None:
        return None, f"名册里没有叫「{name}」的 Agent。"
    if getattr(entry, "archived", False):
        return None, f"「{entry.name}」那一行已经归档了，不发。"
    return entry, None


# ── from：运行时写入，不许模型自报（裁 183④）────────────────────────────


def current_agent_name(session_id: str = "") -> str:
    """当前这一轮的 Agent 身份。取不到就一个中性称呼，绝不让模型自己填（裁 183④）。

    2026-10-22 之前这里**只读进程环境**。裁定 180 之后桌面是一台后端（不按分身起
    独立子进程），同一个进程里可以同时跑着好几个会话，`os.environ` 谁的值为先就是谁
    ——于是同一屏里署出三个名字，人分不清这句话是谁说的。判据改成**读会话那一行**
    （`sessions.agent_id`，裁定 180 的行级归属），进程环境退成"会话还没落账时"的兜底。
    """
    sid = (session_id or "").strip()
    if sid:
        try:
            row = _session_db().get_session(sid) or {}
            landed = str(row.get("agent_id") or "").strip()
            if landed:
                return landed
        except Exception:  # noqa: BLE001 - 读不到会话 ≠ 不能署名，往下走兜底
            logger.debug("say/ask: 按会话号取身份失败 session_id=%s", sid, exc_info=True)
    for key in ("PLOBI_AGENT_ID", "PLOBI_AGENT"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return "plobi"


# ── parts → 一段话 ────────────────────────────────────────────────────────


def _parts_to_text(parts: Any) -> tuple[str, Optional[str]]:
    """返回 ``(文本, 错误)``。文件指针只留路径，不读内容——接话方自己读（裁 183③）。"""
    if isinstance(parts, str):
        parts = [{"text": parts}]
    if not isinstance(parts, list) or not parts:
        return "", "没内容可发（`parts` 至少要有一段）。"

    lines: list[str] = []
    for index, part in enumerate(parts):
        if isinstance(part, str):
            text, pointer = part, ""
        elif isinstance(part, dict):
            text, pointer = str(part.get("text") or "").strip(), str(part.get("path") or "").strip()
        else:
            return "", f"第 {index + 1} 段不认识：只能是 text 或 path。"

        if pointer:
            if len(pointer) > 500 or "base64," in pointer:
                return "", "文件指针太长了——要的是路径，不是文件内容（裁 183③）。"
            lines.append(f"{text or '这一段是材料：'}\n[文件] {pointer}")
        elif text:
            lines.append(text[:_PART_MAX_CHARS])
        else:
            return "", f"第 {index + 1} 段是空的。"
    return "\n\n".join(lines), None


# ── 投递：落进对方自己的会话（裁 183⑤③⑥）───────────────────────────────


def _session_db():
    from plobi_state import SessionDB

    return SessionDB()


def _deliver(entry, body: str, *, sender: str) -> tuple[str, str]:
    """把 ``body`` 追加到接收方「打开就该看到」的那个会话；没有就开一个。

    返回 ``(session_id, message_id)``。异常往外抛——调用方负责变成一句人话，
    不许把「投递失败」伪装成「发好了」。

    落点判据**只有一份**：:meth:`plobi_state.SessionDB.latest_conversational_session_for_agent`
    ——console overview 指给桌面的 ``sessionId`` 与桌面侧栏的可见行都按它算。
    2026-10-21 之前这里自己调 ``list_sessions_rich(agent_id=..., limit=1)``
    （按 started_at 取最新、排除归档与子会话，但**不看来源**），于是：
      * 接收方最新的可见会话是一条 cron 转录时，消息落进调度器的会话里——
        桌面侧栏根本不列 cron ⇒ 点不回来；
      * 两边圈定不一致时，消息落的会话 ≠ 桌面打开的会话。
    现在同源：say 落哪儿，桌面点开这个 Agent 就打开哪儿。
    """
    db = _session_db()
    session_id = db.latest_conversational_session_for_agent(entry.name)
    if not session_id:
        session_id = "chan_" + uuid.uuid4().hex[:16]
        db.create_session(session_id, source="agent_message", agent_id=entry.name)

    stamp = datetime.now().replace(microsecond=0).isoformat()
    message_id = db.append_message(
        session_id,
        role="user",
        content=f"[来自 {sender} · {stamp}]\n{body}",
        asked_by=sender,
    )
    return session_id, str(message_id)
# ── 痕（裁 183⑥）────────────────────────────────────────────────────────


def trace_path() -> Path:
    try:
        from plobi_constants import get_plobi_home

        root = get_plobi_home() / "plobi"
    except Exception:  # noqa: BLE001
        root = Path.home() / ".plobi" / "plobi"
    root.mkdir(parents=True, exist_ok=True)
    return root / TRACE_FILENAME


def _trace(record: dict) -> None:
    try:
        with trace_path().open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        logger.warning("say/ask: 痕没写进去", exc_info=True)


def _result(ok: bool, **fields: Any) -> str:
    return json.dumps({"ok": ok, **fields}, ensure_ascii=False)


def _deliver_or_fail(to: str, parts: Any) -> tuple[Optional[object], Optional[str], Optional[str]]:
    """解析 → 组装 → 投递。返回 ``(entry, text, 错误负载)``，三者恰好一个为 ``None``。"""
    text, parts_error = _parts_to_text(parts)
    if parts_error:
        return None, None, _result(False, error=parts_error)
    entry, refusal = resolve_recipient(to)
    if refusal:
        return None, None, _result(False, error=refusal, reachable=live_names())
    return entry, text, None


_USER_TARGETS = {"用户", "主人", "我", "user"}


def _is_user_target(name: str) -> bool:
    """`to=用户` 是一个**有名字的收件人**，不是名册里的行（裁定 201）。"""
    return (name or "").strip().casefold() in {t.casefold() for t in _USER_TARGETS}


def _deliver_to_user(text: str, *, sender: str) -> str:
    """把话落进主人那一屏，并尽量提醒他一声。"""
    from plobi_user_inbox import append_user_message, notify_user

    session_id, message_id, failure = append_user_message(
        text, asked_by=f"say:{sender}", label=f"来自 {sender}"
    )
    if failure:
        return _result(False, error=failure)
    notified = notify_user(text[:400], title=f"{sender} 找你", session_id=session_id or "")
    trace_id = uuid.uuid4().hex[:12]
    _trace({
        "kind": "say",
        "trace_id": trace_id,
        "from": sender,
        "to": "用户",
        "ts": datetime.now().replace(microsecond=0).isoformat(),
        "parts": text,
        "session_id": session_id,
        "message_id": message_id,
        "notified": notified,
    })
    return _result(
        True, mode="say", trace_id=trace_id, to="用户",
        session_id=session_id, message_id=message_id, deliverer=sender, notified=notified,
    )


def handle_say(args: dict, **kwargs) -> str:
    """投递即返回；投递成功之后**替收件人起一轮**，不等它跑完（裁定 200）。

    用户 10-23 原话：「收件人收到消息会像收到用户消息一样直接开始工作。即使我不去
    打开那个 Agent」，「本质就是用户给它发了一个消息一样」。所以消息落进对方自己的
    会话（user 角色）之后，用 `ask` 已经跑通的那条 spawn 手把那一屏续上一轮——不等，
    让它自己干。**不是**早几版那条 `-p <profile>` 的老路（那套 10-22 已被用户当面
    否掉、整族删除），命令形状与 `ask` 同一份（`wake.py` 的四条护栏写在那边）。

    叫不醒不算 `say` 失败：话已经送到了，返回值里照实写 `woke=false` + 原因。
    """
    args = args if isinstance(args, dict) else {}
    sender = current_agent_name(kwargs.get("session_id") or "")
    if _is_user_target(args.get("to") or ""):
        text, parts_error = _parts_to_text(args.get("parts"))
        if parts_error:
            return _result(False, error=parts_error)
        return _deliver_to_user(text, sender=sender)
    try:
        entry, text, failure = _deliver_or_fail(args.get("to"), args.get("parts"))
        if failure is not None:
            return failure
        if entry.name.strip().casefold() == sender.strip().casefold():
            return _result(
                False,
                error=f"「{entry.name}」就是你自己——别发给自己，这条永远不会有回音。要交给用户就用 `to=用户`。",
            )
        session_id, message_id = _deliver(entry, text, sender=sender)
    except Exception as exc:  # noqa: BLE001
        return _result(False, error=f"say 失败：{exc}")
    woke, wake_note = WK.wake_agent(entry, session_id, sender=sender)
    trace_id = uuid.uuid4().hex[:12]
    _trace({
        "kind": "say",
        "trace_id": trace_id,
        "from": sender,
        "to": entry.name,
        "ts": datetime.now().replace(microsecond=0).isoformat(),
        "parts": text,
        "session_id": session_id,
        "message_id": message_id,
        "woke": woke,
        "wake": wake_note,
    })
    return _result(
        True, mode="say", trace_id=trace_id, to=entry.name,
        session_id=session_id, message_id=message_id,
        deliverer=sender, woke=woke, wakeNote=wake_note,
    )


def handle_ask(args: dict, **kwargs) -> str:
    """问一句并等它答——**答落在同一屏**。投递只做一次，超时不重发。

    2026-10-22 现核的两处断点都在这条路上。旧形状先把话 `_deliver` 进 A 屏，再叫
    `AA.handle_agent_ask` 按**名字**重判一次收件人并起一个全新会话：
    ① 重判那一侧的闸还按「角色是不是项目分身」（`plobi/agents/registry.py:1142`），
      agenda 因此 0.06 秒被拒，而 `say` 同一时刻发得出去（现读名单 24 对 23，差的
      就是 agenda 那一行）——两枚工具对同一个人判定不一致；
    ② 子进程 argv 里没有 `--resume`，真跑完了回答也落在另一条 `source=cli` 的新会话，
      A 屏只剩那句问话——用户在屏幕上看到的「发出去了但那个 Agent 没反应」。

    现在：收件人只由 `resolve_recipient` 判一次（与 `say` 同一份名单），子进程
    `--resume` 到对方那一屏，问话与回答都写进那一屏。**叫不动**（构建失败 / 拒 /
    超时 / 非零退出，且那一屏一条消息都没多出来）才回退成一次投递，让人至少看得见
    这句话；已经落过就不补，那会把一段话说成两段。
    """
    args = args if isinstance(args, dict) else {}
    sender = current_agent_name(kwargs.get("session_id") or "")
    if _is_user_target(args.get("to") or ""):
        return _result(
            False,
            error="`ask` 只问 Agent——用户回不回不由你等。要交给他就用 `say(to=用户)`。",
        )
    try:
        entry, text, failure = _deliver_or_fail(args.get("to"), args.get("parts"))
        if failure is not None:
            return failure
        if entry.name.strip().casefold() == sender.strip().casefold():
            return _result(
                False,
                error="问自己不会得到第二个回答——把问题交给用户就用 `say(to=用户)`。",
            )
        db = _session_db()
        session_id = db.latest_conversational_session_for_agent(entry.name) or ""
        if not session_id:
            # 那一屏还不存在：开一条空的，让子进程那一问成为它的第一条。先 _deliver
            # 再 resume 会把同一句话落成两条。
            session_id = "chan_" + uuid.uuid4().hex[:16]
            db.create_session(session_id, source="agent_message", agent_id=entry.name)
        before = db.message_count(session_id=session_id)
    except Exception as exc:  # noqa: BLE001
        return _result(False, error=f"ask 失败：{exc}")

    started = time.time()
    answer, failure = "", None
    try:
        raw = AA.handle_agent_ask(
            {"agent": entry.name, "question": text},
            entry=entry,
            resume_session=session_id,
            asker=sender,
        )
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            answer = str(parsed.get("answer") or parsed.get("content") or "")
            if parsed.get("ok") is False:
                failure = {k: v for k, v in parsed.items() if k != "ok"}
        if not answer:
            answer = str(raw)
    except Exception as exc:  # noqa: BLE001 - 问不到也要把「话已送到」说清楚
        failure = {"error": f"「{entry.name}」这一轮没答上来：{exc}"}

    delivered_id = ""
    if failure is not None:
        try:
            landed = db.message_count(session_id=session_id) > before
        except Exception:  # noqa: BLE001 - 数不清就当作已落，宁可少发一条也不要发两条
            landed = True
        if not landed:
            _session, delivered_id = _deliver(entry, text, sender=sender)

    _trace({
        "kind": "ask",
        "trace_id": uuid.uuid4().hex[:12],
        "from": sender,
        "to": entry.name,
        "ts": datetime.now().replace(microsecond=0).isoformat(),
        "parts": text,
        "session_id": session_id,
        "message_id": delivered_id,
        "waited_seconds": round(time.time() - started, 2),
        "answer": answer[:_PART_MAX_CHARS],
    })
    if failure is not None:
        return _result(False, delivered=bool(delivered_id), to=entry.name,
                       session_id=session_id, **failure)
    return _result(True, mode="ask", to=entry.name, session_id=session_id, answer=answer)


def check_channel_available() -> bool:
    """名册里一条可发的人都没有 ⇒ 零 schema 足迹（Footprint Ladder rung 3）。"""
    return bool(live_names())
