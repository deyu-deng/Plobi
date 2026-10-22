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
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import agent_ask as AA

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
        "消息落进**对方自己的会话**，并且**会把它叫醒**——它自己就会开始按你说的做，"
        "不用你打开它、也不用你等。所以别为了催它再 say 一遍。\n"
        "对方要改什么、要不要改，由它自己用它的工具决定，你只负责把话说清楚；"
        "需要它**现在**回答你才继续，就改用 `ask`。\n"
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


def current_agent_name() -> str:
    """当前这一轮的 Agent 身份。取不到就一个中性称呼，绝不让模型自己填。"""
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


# ── 唤醒：投递之后让它真的开始干活（用户 10-21）─────────────────────────

#: 唤醒进程的输出落这儿（`$PLOBI_HOME/logs/`）。打包版主进程不落盘，这条链
#: 出问题时必须有一份自己的证据——上一轮 aigw 就是因为"原因只走 console.log"
#: 才查了半天的。
WAKE_LOG = "channel-wake.log"

#: 每个 Agent 一个标记文件，防同一个 Agent 同时被唤醒两次（两次 = 两份钱、
#: 两条打架的回答）。
_WAKE_DIRNAME = "channel-wake"


def _wake_dir() -> Path:
    try:
        from plobi_constants import get_plobi_home

        root = get_plobi_home() / "plobi" / _WAKE_DIRNAME
    except Exception:  # noqa: BLE001
        root = Path.home() / ".plobi" / "plobi" / _WAKE_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def _wake_log_path() -> Path:
    try:
        from plobi_constants import get_plobi_home

        root = get_plobi_home() / "logs"
    except Exception:  # noqa: BLE001
        root = Path.home() / ".plobi" / "logs"
    root.mkdir(parents=True, exist_ok=True)
    return root / WAKE_LOG


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _already_waking(marker: Path) -> bool:
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        return _pid_alive(int(data.get("pid") or 0))
    except (OSError, ValueError, TypeError):
        return False


def wake_prompt(sender: str) -> str:
    """唤醒时补的那一句。**不重述消息内容**——它就在这一屏最上面那条里。"""
    return (
        f"【{sender} 刚捎来一句话，就在这一屏最上面那条（以「[来自 {sender} · 时间]」开头）。】\n"
        "去读它，按它说的做；做完用一两句话讲清你做了什么、卡在哪儿。\n"
        "如果这件事需要反过来问他，就用 `ask` 问一次，别用 `say`——`say` 不会把他叫醒。"
    )


def wake_agent(entry, session_id: str, *, sender: str) -> tuple[bool, str]:
    """投递之后，让那个 Agent **自己开始干**。返回 ``(起没起得来, 一句说明)``。

    病根（用户 10-21 原话：「Plobi 如果发了一条信息给一个 L2 Agent。如果我不打开
    那个 Agent 的话。它不会自己干活……要我打开之后才会选择模型，才能继续干活」）：
    `say` 只把消息**写进**对方的会话就返回了——那个 Agent 的进程根本不存在
    （L2 的运行时是桌面绑定时才起来的），于是消息躺在那儿，等用户亲手点开才被看到。

    修法：投递之后起一个**后台子进程**跑那一轮，用的是 `ask` 已经在生产上跑通的
    同一条 spawn 路子（`agent_ask.build_child_command`：同一个 profile 家、同一份
    总账、同一套密钥覆盖），只多两样：
      * ``--resume <刚投递的那个会话>`` —— 让它续**这一屏**，而不是另开一屏把
        刚才那条消息甩在身后；
      * 不等待（``Popen`` + 分隔的输出日志）—— `say` 的承诺是"投递即返回"。

    三条护栏：
      1. **同一个人不并发唤醒两次**：标记文件里记 pid，活着就跳过（两次＝两份钱、
         两条互相打架的回答）。
      2. **不唤醒唤醒链**：本进程若是被唤醒起来的（``PLOBI_WAKE=1``），它再 `say`
         时不再唤醒下一跳——否则两个 Agent 互相 `say` 就是一场无限乒乓。
      3. **失败不算 say 失败**：话已经送到了，这是"送到但与没叫醒"的差别，照实
         回给调用方（``woke: false`` + 原因），不把一句好话报成错误。
    """
    if os.environ.get("PLOBI_WAKE") == "1":
        return False, "这一轮本身就是被唤醒的，不再往下叫醒（防止互相叫醒的死循环）。"

    marker = _wake_dir() / f"{_safe_slug(entry.name)}.json"
    if _already_waking(marker):
        return False, "它已经在干上一件活了，这条等它做完再看。"

    try:
        argv, env, cwd = AA.build_child_command(
            entry, wake_prompt(sender), asked_by=f"say:{sender}"
        )
    except Exception as exc:  # noqa: BLE001 - 叫不醒也不许炸掉 L1 的回合
        logger.warning("say: 唤醒命令拼不出来（%s）", exc)
        return False, f"叫不醒它：{exc}"

    if session_id:
        # `--resume` 是全局参数（`plobi -r/--resume <id>`），插在 `-p` 前面。
        try:
            at = argv.index("-p")
        except ValueError:
            at = 1
        argv = [*argv[:at], "--resume", session_id, *argv[at:]]

    env["PLOBI_WAKE"] = "1"
    env["PLOBI_ASKED_BY"] = f"say:{sender}"

    log_path = _wake_log_path()
    try:
        with log_path.open("ab", buffering=0) as handle:
            handle.write(
                f"\n=== wake {entry.name} @ {datetime.now().replace(microsecond=0).isoformat()} "
                f"(from {sender}, session {session_id}) ===\n".encode("utf-8")
            )
            proc = subprocess.Popen(  # noqa: S603 — argv 是本模块自己拼的
                argv,
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=handle,
                env=env,
                cwd=cwd,
                start_new_session=True,
            )
    except FileNotFoundError:
        return False, "这台机器上找不到可执行的命令行，叫不醒它。"
    except OSError as exc:
        logger.warning("say: 唤醒失败（%s）", exc)
        return False, f"叫不醒它：{exc}"

    try:
        marker.write_text(
            json.dumps(
                {
                    "pid": proc.pid,
                    "agent": entry.name,
                    "session": session_id,
                    "from": sender,
                    "started": datetime.now().replace(microsecond=0).isoformat(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        logger.warning("say: 唤醒标记没写进去（不影响这一趟）", exc_info=True)

    # **不许谎报**：进程起得来 ≠ 它真在干活。常见的是「起来就退」——
    # profile 家没落地、`--resume` 找不到那条会话（子分身的 CLI 若还没切到单库，
    # 它读的是分家库）、模型路由失败……所以给它两秒半，再看它还在不在。
    time.sleep(2.5)
    if proc.poll() is not None:
        tail = _log_tail(log_path)
        return False, (
            f"它起来了就退出了（exit {proc.returncode}）——没干活。"
            + (f"日志末尾：{tail}" if tail else "")
        )

    return True, f"已叫醒它（后台 pid {proc.pid}，还在跑），它会在这一屏里接着干。"


def _log_tail(path: Path, limit: int = 400) -> str:
    """唤醒日志的末尾几行——那是子进程唯一留下的证据（为什么秒退就写在这儿）。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    tail = text.strip().splitlines()[-4:]
    return " / ".join(line.strip() for line in tail if line.strip())[-limit:]


def _safe_slug(name: str) -> str:
    """标记文件名：ASCII 安全名，中文 Agent 名不许变成非法文件名。"""
    import hashlib

    cleaned = "".join(ch for ch in name if ch.isalnum() or ch in "-_")[:40]
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"{cleaned or 'agent'}-{digest}"


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


def handle_say(args: dict, **kwargs) -> str:
    """投递即返回。这一步不许抛进 L1 的对话循环。"""
    args = args if isinstance(args, dict) else {}
    try:
        entry, text, failure = _deliver_or_fail(args.get("to"), args.get("parts"))
        if failure is not None:
            return failure
        session_id, message_id = _deliver(entry, text, sender=current_agent_name())
    except Exception as exc:  # noqa: BLE001
        return _result(False, error=f"say 失败：{exc}")
    sender = current_agent_name()
    # 2026-10-21 用户令：「我不打开那个 Agent 的话，它不会自己干活。」——投递
    # 之后把它叫醒（后台跑那一轮，不占这一趟）。叫不醒不算 say 失败：话已经
    # 送到了，那是「送到但我没叫醒它」的差别，照实回给调用方。
    woke, wake_note = wake_agent(entry, session_id, sender=sender)
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
    """投递并等。投递只做一次——超时不重发，那会把一段话说成两段。"""
    args = args if isinstance(args, dict) else {}
    try:
        entry, text, failure = _deliver_or_fail(args.get("to"), args.get("parts"))
        if failure is not None:
            return failure
        sender = current_agent_name()
        session_id, message_id = _deliver(entry, text, sender=sender)
    except Exception as exc:  # noqa: BLE001
        return _result(False, error=f"ask 失败：{exc}")

    started = time.time()
    answer, failure = "", None
    try:
        payload = {"agent": entry.name, "question": text}
        if str(args.get("timeout") or "").strip():
            payload["timeout"] = int(args["timeout"])
        raw = AA.handle_agent_ask(payload)
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

    _trace({
        "kind": "ask",
        "trace_id": uuid.uuid4().hex[:12],
        "from": sender,
        "to": entry.name,
        "ts": datetime.now().replace(microsecond=0).isoformat(),
        "parts": text,
        "session_id": session_id,
        "message_id": message_id,
        "waited_seconds": round(time.time() - started, 2),
        "answer": answer[:_PART_MAX_CHARS],
    })
    if failure is not None:
        return _result(False, delivered=True, to=entry.name, session_id=session_id, **failure)
    return _result(True, mode="ask", to=entry.name, session_id=session_id, answer=answer)


def check_channel_available() -> bool:
    """名册里一条可发的人都没有 ⇒ 零 schema 足迹（Footprint Ladder rung 3）。"""
    return bool(live_names())
