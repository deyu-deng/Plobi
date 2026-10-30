"""待确认那批纸的读口与批口 —— 让「进闸了」这件事在屏幕上看得见。

为什么要有这一格（用户 10-27 拍的「先让它进人眼」）：当日摘要每天由 cron 渲染、送进 L1 那道闸，
然后**没人看得见**——现核 ``$PLOBI_HOME/pending/memory/`` 里堆着 7 张，六张早已过了 6 小时 TTL。
机制没错（写脑要人点头），错的是那一问落在只有 CLI 才看得见的地方。

这一格只做两件：把待批的**结构化**列出来，把点头/驳回转给**已有那一层**
（``plobi_cli.write_approval_commands.handle_pending_subcommand``）——批准的行为只有一处实现，
不在这里另写一遍 apply，否则早晚两处不一致（同一型事故在 provider 挂载那格记过）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import FastAPI
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

PREFIX = "/api/memory"


def _describe(rec: Dict[str, Any]) -> Dict[str, Any]:
    """一张待批记录 → 屏幕上能读的三行。机理解释留在 tooltip，不进正文。"""
    payload = rec.get("payload") or {}
    kind = str(payload.get("kind") or "memory")
    paths: List[str] = []
    expired = False
    summary = ""
    if kind == "mind_plan":
        try:
            from plobi.mind import proposal as L1

            plan = L1.Plan.from_json(payload.get("plan") or "")
            # 屏幕上不写逻辑前缀：`Loom/` 那一层物理上早没了（10-14 平铺），
            # 它只是账本里的稳定字符串。用户 2026-10-29 问「不是已经没有 Loom 这个概念了吗」
            # ——他是在待批那一屏上看到的，那是我把内部名字印了上去。
            paths = [c.logical_path[len("Loom/"):] if c.logical_path.startswith("Loom/") else c.logical_path
                     for c in plan.changes]
            expired = L1.is_expired(plan)
            summary = f"{len(plan.changes)} 项 · {plan.digest[:12]}"
        except Exception as exc:  # noqa: BLE001 - 一张读不出来的纸不该拖垮整个列表
            logger.warning("mind pending: plan unreadable (%s)", exc)
            summary = "读不出这张计划"
    else:
        # L0 条目（内置记忆）与技能那两类：卡片要能说清"它想记什么/改什么/删什么"，
        # 所以 action 与 old_text 一并给出（屏幕文案自己决定措辞，这里只给事实）。
        action = str(payload.get("action") or "")
        entries = payload.get("entries")
        entry = (
            ("；".join(str(e) for e in entries) if isinstance(entries, list) else "")
            or payload.get("content")
            or payload.get("entry")
            or payload.get("old_text")
            or ""
        )
        summary = str(entry)[:200]
        target = payload.get("target") or payload.get("skill") or ""
        if target:
            paths = [str(target)]
        if action:
            summary = f"[{action}] {summary}".strip()
    created = rec.get("created_at")
    return {
        "id": str(rec.get("id") or ""),
        "kind": kind,
        "paths": paths,
        "summary": summary,
        "created_at": created,
        "expired": expired,
    }


def _listing() -> Dict[str, Any]:
    from plobi_cli.config import load_config
    from tools import write_approval as wa

    mem = (load_config() or {}).get("memory") or {}
    records = list(wa.list_pending(wa.MEMORY))
    items = [_describe(r) for r in records]
    return {
        "items": items,
        "count": len(items),
        "expired_count": sum(1 for i in items if i["expired"]),
        # 两个开关给屏幕说真话用：闸开没开、自动档开没开（不是给模型解释机理）
        "gate": bool(mem.get("mind_write_approval", True)),
        "auto": bool(mem.get("mind_write_auto_approve", False)),
    }


def register_memory_pending_api(app: FastAPI) -> None:
    """挂三个口：读列表、点头、驳回。行为仍归那一层命令实现。"""

    @app.get(f"{PREFIX}/pending", name="memory_pending_list")
    async def memory_pending_list() -> JSONResponse:
        return JSONResponse(_listing())

    @app.post(f"{PREFIX}/pending/{{pending_id}}/approve", name="memory_pending_approve")
    async def memory_pending_approve(pending_id: str) -> JSONResponse:
        return JSONResponse(_act(pending_id, "approve"))

    @app.post(f"{PREFIX}/pending/{{pending_id}}/reject", name="memory_pending_reject")
    async def memory_pending_reject(pending_id: str) -> JSONResponse:
        return JSONResponse(_act(pending_id, "reject"))

    @app.post(f"{PREFIX}/pending/prune", name="memory_pending_prune")
    async def memory_pending_prune() -> JSONResponse:
        """清掉**已过期**的那些纸（不碰还在有效期内的，也不碰人手写的）。

        过期＝按计划里的 TTL 判，点 approve 也不会落盘（`apply_plan` 会拒），
        留着只会在角标上冒充"等你点头"。清之前把每一张抄进
        `$PLOBI_HOME/archive/pending-expired/`，不是直接丢。
        """
        return JSONResponse(_prune())


def _memory_store(record: Dict[str, Any] | None = None):
    """按出厂上限造一个 store —— 批的不是脑里那张计划时（内置 MEMORY/USER 条目）要用。

    与 ``agent/agent_init.py`` 那一处同一构造方式（``memory_limits_from_config``），
    不在这儿另定一套容量。造不出来就回 ``None``：命令层会明说「memory store unavailable」，
    比假装批成功好。
    """
    try:
        from tools.memory_tool import MemoryStore, memory_limits_from_config

        from plobi_cli.config import load_config

        mem = (load_config() or {}).get("memory") or {}
        payload = (record or {}).get("payload") or {}
        slug = str(payload.get("agent_slug") or "").strip()
        if slug:
            from tools.memory_tool import load_on_disk_store

            return load_on_disk_store(slug)
        return MemoryStore(**memory_limits_from_config(mem))
    except Exception as exc:  # noqa: BLE001 - 造不出 store 不影响脑里那张计划
        logger.warning("mind pending: memory store unavailable (%s)", exc)
        return None


def _prune() -> Dict[str, Any]:
    import json as _json
    import shutil

    from plobi.mind import proposal as L1
    from plobi_constants import get_plobi_home
    from tools import write_approval as wa

    home = get_plobi_home()
    archive = home / "archive" / "pending-expired"
    pruned, kept = [], 0
    for record in list(wa.list_pending(wa.MEMORY)):
        payload = record.get("payload") or {}
        expired = False
        if payload.get("kind") == "mind_plan":
            try:
                expired = L1.is_expired(L1.Plan.from_json(payload.get("plan") or ""))
            except Exception:  # noqa: BLE001 - 读不出来的纸不当过期处理
                expired = False
        if not expired:
            kept += 1
            continue
        try:
            archive.mkdir(parents=True, exist_ok=True)
            src = home / "pending" / "memory" / f"{record.get('id')}.json"
            if src.is_file():
                shutil.copy2(src, archive / src.name)
        except OSError:
            pass
        if wa.discard_pending(wa.MEMORY, str(record.get("id") or "")):
            pruned.append(str(record.get("id")))
    out = _listing()
    out.update({"ok": True, "pruned": pruned, "kept": kept, "archive": str(archive)})
    return out


def _act(pending_id: str, verb: str) -> Dict[str, Any]:
    from plobi_cli.write_approval_commands import handle_pending_subcommand

    message = ""
    try:
        from tools import write_approval as _wa

        record = _wa.get_pending(_wa.MEMORY, pending_id)
        message = handle_pending_subcommand(
            "memory", [verb, pending_id], memory_store=_memory_store(record)
        )
    except Exception as exc:  # noqa: BLE001 - 批不动要说清为什么，不抛给前端一个 500
        logger.warning("mind pending: %s %s failed (%s)", verb, pending_id, exc)
        message = f"没做成：{exc}"
    out = _listing()
    out["message"] = str(message).strip()
    out["ok"] = bool(message) and not any(
        w in out["message"] for w in ("No pending", "Failed:", "Usage:", "没做成", "过期")
    )
    return out
