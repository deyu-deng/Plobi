#!/usr/bin/env python3
"""Shared handlers for the /memory and /skills write-approval subcommands.

Both the interactive CLI (``cli.py``) and the gateway (``gateway/run.py``) call
into this module so the pending-review UX (list / approve / reject / diff /
mode) lives in one place. Each caller owns only its surface concerns:
formatting the returned text and, for the gateway, persisting config + evicting
the cached agent on a mode change.

Every public handler returns a plain text string suitable for both a terminal
and a chat message. Skill diffs are intentionally NOT inlined here — the
``diff`` handler returns the full diff for the CLI pager, but on a messaging
platform the gateway truncates it and points the user at the dashboard / file.
"""

from __future__ import annotations

import json
import logging
from typing import List, Optional

from tools import write_approval as wa

logger = logging.getLogger(__name__)


def _fmt_state(subsystem: str) -> str:
    on = wa.write_approval_enabled(subsystem)
    return f"{subsystem}.write_approval = {'on' if on else 'off'}"


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_pending_list(subsystem: str) -> str:
    records = wa.list_pending(subsystem)
    if not records:
        return f"No pending {subsystem} writes."
    lines = [f"Pending {subsystem} writes ({len(records)}):"]
    for r in records:
        origin = r.get("origin", "foreground")
        tag = " [auto]" if origin == "background_review" else ""
        lines.append(f"  {r['id']}{tag}  {r.get('summary', '')}")
    where = "/{s} approve <id>".format(s=subsystem)
    lines.append("")
    lines.append(f"Apply: {where}   Reject: /{subsystem} reject <id>")
    if subsystem == wa.SKILLS:
        lines.append("Review full diff: /skills diff <id>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Subcommand dispatch
# ---------------------------------------------------------------------------

def handle_pending_subcommand(
    subsystem: str,
    args: List[str],
    *,
    memory_store=None,
    set_mode_fn=None,
) -> Optional[str]:
    """Dispatch a /memory or /skills subcommand.

    Args:
        subsystem: ``memory`` or ``skills``.
        args: tokens after the slash command (e.g. ``["approve", "a1b2"]``).
        memory_store: live MemoryStore for applying approved memory writes
            (CLI passes ``self.agent._memory_store``; gateway applies against a
            freshly loaded store).
        set_mode_fn: optional callable ``(enabled: bool) -> None`` that
            persists the new write_approval boolean to config (gateway provides
            this; CLI uses its own ``save_config_value`` and passes a closure).

    Returns a text string to show the user. Returns None when the args are not
    a write-approval subcommand (caller falls through to its other handling,
    e.g. /skills search).
    """
    if not args:
        # Bare /memory or /skills with no sub → show pending + gate state.
        return f"{_fmt_state(subsystem)}\n\n" + _fmt_pending_list(subsystem)

    sub = args[0].lower()
    rest = args[1:]

    if sub == "pending":
        return _fmt_pending_list(subsystem)

    if sub in {"approve", "apply"}:
        return _approve(subsystem, rest, memory_store)

    if sub in {"reject", "deny", "drop"}:
        return _reject(subsystem, rest)

    if sub == "diff" and subsystem == wa.SKILLS:
        return _diff(rest)

    if sub in {"approval", "mode"}:  # 'mode' kept as a back-compat alias
        return _set_approval(subsystem, rest, set_mode_fn)

    return None  # not ours — caller handles


def _resolve_one(subsystem: str, rest: List[str]):
    if not rest:
        return None, f"Usage: /{subsystem} approve|reject <id>  (or 'all')"
    return rest[0], None


def _approve(subsystem: str, rest: List[str], memory_store) -> str:
    target, err = _resolve_one(subsystem, rest)
    if err or target is None:
        return err or f"Usage: /{subsystem} approve <id>"

    records = wa.list_pending(subsystem)
    if not records:
        return f"No pending {subsystem} writes."

    if target.lower() == "all":
        targets = list(records)
    else:
        rec = wa.get_pending(subsystem, target)
        if not rec:
            return f"No pending {subsystem} write with id '{target}'."
        targets = [rec]

    applied, failed = 0, []
    for rec in targets:
        ok, msg = _apply_one(subsystem, rec, memory_store)
        if ok:
            wa.discard_pending(subsystem, rec["id"])
            applied += 1
        else:
            failed.append(f"{rec['id']}: {msg}")

    out = [f"Approved {applied} {subsystem} write(s)."]
    if failed:
        out.append("Failed:")
        out.extend(f"  {f}" for f in failed)
    return "\n".join(out)


def _remember_l0_decision(payload: dict, outcome: str) -> None:
    """L0 的一次采纳/驳回进信任账（账本只认「人真的点过」这件事本身）。

    记在 (作用域, Agent) 这一格上——见 `_l0_trust_category`：一门课攒的次数只管那门课。
    """
    scope = str((payload or {}).get("target") or "").strip()
    if not scope:
        return
    try:
        from plobi.mind.proposal import TrustLedger
        from tools.memory_tool import _l0_trust_category

        TrustLedger().record(
            outcome,
            _l0_trust_category(scope, str((payload or {}).get("agent_slug") or "")),
            "l0",
        )
    except Exception:  # noqa: BLE001 - 记不上账不该让批准本身失败
        pass


def _record_consent(plan, action: str, pending_id: str) -> None:
    """一次表态落一条不可变事件（规格 §3.1／§5.2，P1 信号层）。

    **与 ``TrustLedger`` 并存、不替代**：那是门槛的当前值（会归零），这是发生过什么
    （只追加）。采纳与驳回**走同一本账**——这正是 P0 修掉的「采纳记 L0、驳回记 L1」
    那类错位在流水层面的对称律。

    记账失败不许让批准本身失败（沿用 ``_remember_l0_decision`` 的口径），但**要留日志**：
    静默失败的账本等于没有流水，下一刀抽偏好时才发现表是空的。
    """
    try:
        from plobi.mind.consent_events import ConsentLedger, comments_for

        comment = "\n".join(comments_for(pending_id))
        ConsentLedger().record_plan(plan, action, pending_id=pending_id, comment=comment)
    except Exception:  # noqa: BLE001 - 记不上账不该让表态本身失败
        logger.warning("consent event (%s) for %s not recorded", action, pending_id, exc_info=True)


def _remember_rejection(rec) -> None:
    """驳回要进信任账（裁定 156／199：被驳一次，那一类立刻退回必问）。"""
    payload = (rec or {}).get("payload", {})
    if payload.get("kind") != "mind_plan":
        # L0 那些改写（action=rewrite）：驳一次，那一类立刻退回必问。
        if str(payload.get("action") or "") == "rewrite":
            _remember_l0_decision(payload, "rejected")
        return
    try:
        from plobi.mind import proposal as L1

        plan = L1.Plan.from_json(payload["plan"])
        L1.record_rejection(plan, reason="用户驳回")
    except Exception:  # noqa: BLE001 - 记不上账不该让驳回本身失败
        plan = None
    if plan is not None:
        _record_consent(plan, "rejected", str((rec or {}).get("id") or ""))


def _apply_one(subsystem: str, rec, memory_store):
    payload = rec.get("payload", {})
    try:
        if subsystem == wa.MEMORY and payload.get("kind") == "note_manage":
            # 改名／删除这一支：人点了头，这一笔就落。
            # 走自己的载荷（不走 ``mind_plan``）——那道 ``apply_plan`` 只认 ``MEMORY_ZONES``，
            # 人读面的 ``Notes/…`` 不在里面。批准的**行为**只有这一处实现。
            import json as _json

            from plobi.human import note_manage

            op = note_manage.NoteOp.from_json(_json.dumps(payload.get("op") or {}, ensure_ascii=False))
            note_manage.apply_op(op)
            return True, ""
        if subsystem == wa.MEMORY and payload.get("kind") == "mind_plan":
            # 写脑那张计划：点这一下＝「你说的就是这个哈希」。apply_plan 会重新比对象身份、
            # 每档新旧 sha256 与 TTL；过期、人先改过、换了根，都在这儿被拒，不落一个字。
            from plobi.mind import proposal as L1
            from plobi.mind.paths import resolve_roots
            from plobi.mind.writer import MindWriter

            plan = L1.Plan.from_json(payload["plan"])
            # 点头这一趟必须绕开自己那道闸，否则 apply 会再出一张计划（自我套娃）。
            report = L1.apply_plan(
                plan, approved=plan.digest, roots=resolve_roots(),
                writer=MindWriter(require_approval=False),
            )
            if report.ok:
                # WP-MIND-PENDING-CONTENT：写脑的采纳要记在 L1 那一格上。
                # 原先一律走 _remember_l0_decision（source="l0"），而驳回 mind_plan
                # 走的是 L1.record_rejection（source="l1"）——采纳永远攒不到 streak，
                # 门槛只降不升。两本账分开：这里只补 L1，L0/note_manage 那两支一个字不动。
                try:
                    from plobi.mind.proposal import TrustLedger, category_of

                    cats = sorted(set(plan.categories.values())) or [category_of(c.logical_path) for c in plan.changes] or ["other"]
                    book = TrustLedger()
                    for cat in set(cats):
                        book.record("adopted", cat, "l1")
                except Exception:  # noqa: BLE001 - 记不上账不该让批准本身失败
                    pass
                # 同一次点头还要落**不可变流水**（P1 信号层）：门槛的当前值与
                # 「发生过什么」是两件事，前者能重算、后者重不出来。
                _record_consent(plan, "adopted", str((rec or {}).get("id") or ""))
            return (True, "") if report.ok else (False, "计划过期或对象/内容已变，重出一张再点头")
        if subsystem == wa.MEMORY:
            if memory_store is None:
                return False, "memory store unavailable"
            from tools.memory_tool import apply_memory_pending, load_on_disk_store

            slug = str(payload.get("agent_slug") or "").strip()
            if slug and getattr(memory_store, "slug", "") != slug:
                memory_store = load_on_disk_store(slug)
            result = apply_memory_pending(payload, memory_store)
            return bool(result.get("success")), result.get("error", "")
        else:
            from tools.skill_manager_tool import apply_skill_pending
            result = json.loads(apply_skill_pending(payload))
            return bool(result.get("success")), result.get("error", "")
    except Exception as e:
        return False, str(e)


def _reject(subsystem: str, rest: List[str]) -> str:
    target, err = _resolve_one(subsystem, rest)
    if err or target is None:
        return err or f"Usage: /{subsystem} reject <id>"
    if target.lower() == "all":
        n = 0
        for rec in wa.list_pending(subsystem):
            _remember_rejection(rec)
            if wa.discard_pending(subsystem, rec["id"]):
                n += 1
        return f"Rejected {n} pending {subsystem} write(s)."
    for rec in wa.list_pending(subsystem):
        if rec["id"] == target:
            _remember_rejection(rec)
    if wa.discard_pending(subsystem, target):
        return f"Rejected pending {subsystem} write '{target}'."
    return f"No pending {subsystem} write with id '{target}'."


def _diff(rest: List[str]) -> str:
    if not rest:
        return "Usage: /skills diff <id>"
    rec = wa.get_pending(wa.SKILLS, rest[0])
    if not rec:
        return f"No pending skill write with id '{rest[0]}'."
    diff = wa.skill_pending_diff(rec)
    header = f"# Pending skill write {rec['id']}: {rec.get('summary', '')}\n"
    return header + "\n" + diff


def _set_approval(subsystem: str, rest: List[str], set_mode_fn) -> str:
    """Turn the approval gate on/off for a subsystem.

    ``set_mode_fn`` (when provided) persists the new boolean to config.
    """
    if not rest:
        return (f"{_fmt_state(subsystem)}\n"
                f"Set with: /{subsystem} approval <on|off>")
    arg = rest[0].strip().lower()
    truthy = {"on", "true", "yes", "1", "enable", "enabled"}
    falsey = {"off", "false", "no", "0", "disable", "disabled"}
    if arg in truthy:
        enabled = True
    elif arg in falsey:
        enabled = False
    else:
        return f"Invalid value '{arg}'. Use: on or off."
    if set_mode_fn is None:
        val = "true" if enabled else "false"
        return (f"To change the {subsystem} approval gate, run:\n"
                f"  plobi config set {subsystem}.write_approval {val}")
    try:
        set_mode_fn(enabled)
    except Exception as e:
        return f"Failed to set {subsystem}.write_approval: {e}"
    return f"{subsystem}.write_approval set to '{'on' if enabled else 'off'}'."
