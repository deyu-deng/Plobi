"""C6 每日回访：确定性选题 → L2 起草 → 看板卡 + 钉钉推送。

设计真源：``Docs/DESIGN-M2-ROUTINE-ANCHORS-proposal.md`` §7。四类选题全部是
确定性 diff（events 台账 / 计划状态 / pace 项目推进 / 改动率），零 LLM；L2
便宜模型只负责把差异起草成 ≤3 个问题（走 B3 :class:`QuotaAwareCompleter`，
永不打 L1），且每个问题必须带 evidence——没有 evidence 的问题丢弃，起草
失败时回落到确定性模板问题（依旧带 evidence）。

不变式（§7.2）：问题必须 evidence 驱动；每天最多一条卡；同一差异 72h 内不
重复问；连续 3 天被忽略自动降为每周一次（配置项）；改配置走 C6b 提案卡，
本模块绝不直接改配置。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from . import store

logger = logging.getLogger(__name__)

CHECKIN_PROMPT = (
    "你是 Plobi 秘书。下面是系统观察到的真实差异（JSON）和当前作息/节奏配置。"
    "把它们起草成最多 {max_questions} 个给用户的问题：每个问题必须基于其中一条"
    "差异并以（依据：<event id 或 plan id 或项目名>）结尾；不要寒暄、不要编造"
    "系统没观察到的内容。每行一个问题，不要额外解释：\n\n{brief}"
)

# ── config ───────────────────────────────────────────────────────────────────

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "frequency": "daily",  # daily | weekly（降频护栏自动切换）
    "max_questions": 3,
    "downgrade_after_ignores": 3,
    "dedupe_hours": 72,
    "unadvanced_days": 6,
    "change_rate_window_days": 7,
    "change_rate_threshold": 0.5,
    "change_rate_min_sample": 3,
}


def config_path() -> Path:
    override = str(__import__("os").environ.get("PLOBI_CHECKIN_CONFIG", "")).strip()
    if override:
        return Path(override)
    try:
        from hermes_constants import get_hermes_home

        root = get_hermes_home() / "plobi"
    except Exception:
        root = Path.home() / ".hermes" / "plobi"
    return root / "checkin.json"


def load_config() -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    try:
        raw = json.loads(config_path().read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            cfg.update({k: v for k, v in raw.items() if k in DEFAULT_CONFIG})
    except (OSError, json.JSONDecodeError):
        pass
    return cfg


def save_config(cfg: dict[str, Any]) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    keep = {k: cfg[k] for k in DEFAULT_CONFIG if k in cfg}
    path.write_text(json.dumps(keep, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ── topic selection（全部确定性） ─────────────────────────────────────────────


def _topic_event_diffs(conn, now: datetime) -> list[dict]:
    """a. 近 24h 用户对 events 的新增/修改/删除（top 差异）。"""
    since = (now - timedelta(hours=24)).isoformat()
    topics: list[dict] = []

    rows = conn.execute(
        "SELECT * FROM events WHERE source = 'manual' AND created_at >= ? "
        "ORDER BY created_at DESC LIMIT 5",
        (since,),
    ).fetchall()
    for row in rows:
        event = store._row_to_event(row)
        topics.append(
            {
                "key": f"event_diff:create:{event.id}",
                "topic": "event_created",
                "evidence": {"event_id": event.id, "title": event.title, "start_at": event.start_at},
                "summary": f"你新增了日程「{event.title}」（{event.start_at[:16]}）",
            }
        )

    for action, verb in (("manual_edit", "修改"), ("manual_delete", "删除")):
        rows = conn.execute(
            "SELECT event_id, ts FROM action_log WHERE action = ? AND ts >= ? "
            "ORDER BY ts DESC LIMIT 5",
            (action, since),
        ).fetchall()
        for row in rows:
            event_id = str(row["event_id"])
            if action == "manual_edit":
                current = store.get_event(conn, event_id)
                title = current.title if current else event_id
                start = current.start_at if current else ""
            else:
                title = event_id  # 已删除，events 表里查不到
                start = ""
            topics.append(
                {
                    "key": f"event_diff:{action}:{event_id}",
                    "topic": f"event_{action.split('_')[1]}",
                    "evidence": {"event_id": event_id, "action": action, "ts": row["ts"]},
                    "summary": f"你{verb}了日程「{title}」",
                }
            )
    return topics


def _topic_plan_status(conn, now: datetime) -> Optional[dict]:
    """b. 昨夜计划（即今天的 daily_plan）状态；dismissed 列出条目。"""
    today = now.date().isoformat()
    plan = store.get_daily_plan(conn, today)
    if plan is None:
        return None
    detail: dict[str, Any] = {"plan_id": plan.id, "status": plan.status}
    summary = f"昨夜生成的今日计划状态是「{plan.status}」"
    if plan.status == "dismissed":
        items = store.list_plan_items(conn, plan.id)
        titles = "、".join(i.title for i in items) or "（无条目）"
        detail["items"] = [i.title for i in items]
        summary += f"，被忽略的条目：{titles}"
    return {
        "key": f"plan_status:{today}",
        "topic": "plan_status",
        "evidence": detail,
        "summary": summary,
    }


def _topic_unadvanced_projects(conn, now: datetime, *, days: int) -> list[dict]:
    """c. pace 项目连续 N 天的推进块被忽略/过期（未被确认）。"""
    from .planning import paced_projects

    topics: list[dict] = []
    for entry, weekly_hours in paced_projects():
        hit_dates: list[str] = []
        for offset in range(1, days + 1):
            day = (now.date() - timedelta(days=offset)).isoformat()
            plan = store.get_daily_plan(conn, day)
            if plan is None or plan.status == "confirmed":
                break  # 无计划或已确认 → 推进观察中断
            blocks = [
                item
                for item in store.list_plan_items(conn, plan.id)
                if item.evidence.get("kind") == "project_block"
                and item.evidence.get("project_id") == entry.name
            ]
            if not blocks:
                break  # 那天根本没排它的块 → 观察中断
            hit_dates.append(day)
        if len(hit_dates) >= days:
            topics.append(
                {
                    "key": f"unadvanced:{entry.name}",
                    "topic": "project_unadvanced",
                    "evidence": {
                        "project_id": entry.name,
                        "days": len(hit_dates),
                        "dates": hit_dates,
                        "weekly_hours": weekly_hours,
                    },
                    "summary": (
                        f"项目「{entry.name}」的推进块已连续 {len(hit_dates)} 天"
                        f"未被确认（每周目标 {weekly_hours:g} 小时）"
                    ),
                }
            )
    return topics


def _topic_change_rate(conn, now: datetime, *, window_days: int, threshold: float, min_sample: int) -> list[dict]:
    """d. 改动率异常：近 N 天某类（按 kind 聚合）日程被改动比例超阈值。"""
    since = (now - timedelta(days=window_days)).isoformat()
    rows = conn.execute(
        """
        SELECT e.kind AS kind, COUNT(DISTINCT a.event_id) AS changed
          FROM action_log a
          JOIN events e ON e.id = a.event_id
         WHERE a.ts >= ?
         GROUP BY e.kind
        """,
        (since,),
    ).fetchall()
    changed = {row["kind"]: int(row["changed"]) for row in rows}
    topics: list[dict] = []
    for kind, changed_count in changed.items():
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE kind = ? AND created_at >= ?",
            (kind, since),
        ).fetchone()["n"]
        if total < min_sample:
            continue  # 样本太小，不作结论
        ratio = changed_count / total
        if ratio > threshold:
            topics.append(
                {
                    "key": f"change_rate:{kind}:{now.date().isoformat()}",
                    "topic": "change_rate_anomaly",
                    "evidence": {
                        "kind": kind,
                        "changed": changed_count,
                        "total": total,
                        "ratio": round(ratio, 2),
                        "window_days": window_days,
                    },
                    "summary": (
                        f"近 {window_days} 天「{kind}」类日程 {ratio:.0%} 被改动"
                        f"（{changed_count}/{total}）"
                    ),
                }
            )
    return topics


# ── dedupe + guard ───────────────────────────────────────────────────────────


def _asked_keys(conn, now: datetime, *, dedupe_hours: int) -> set[str]:
    keys: set[str] = set()
    for card in store.list_checkin_cards(conn):
        try:
            created = datetime.fromisoformat(card.created_at)
        except ValueError:
            continue
        if now - created > timedelta(hours=dedupe_hours):
            continue
        for question in card.questions:
            if isinstance(question, dict) and question.get("key"):
                keys.add(str(question["key"]))
    return keys


def consecutive_dismissed(conn) -> int:
    """最近连续被忽略的回访卡数（从最新往回数，遇非 dismissed 即停）。"""
    run = 0
    for card in store.list_checkin_cards(conn, kind=store.CHECKIN_KIND):
        if card.status != "dismissed":
            break
        run += 1
    return run


def _within_weekly_window(conn, now: datetime) -> bool:
    cards = store.list_checkin_cards(conn, kind=store.CHECKIN_KIND)
    if not cards:
        return False
    try:
        last = datetime.fromisoformat(cards[0].for_date)
    except ValueError:
        return False
    return (now.date() - last.date()) < timedelta(days=7)


# ── drafting ─────────────────────────────────────────────────────────────────


def _fallback_questions(topics: list[dict], *, max_questions: int) -> list[dict]:
    """确定性模板问题（零 LLM 兜底），每问必带 evidence。"""
    questions: list[dict] = []
    for topic in topics[:max_questions]:
        questions.append(
            {
                "key": topic["key"],
                "text": f"{topic['summary']}——想调整吗？",
                "evidence": topic["evidence"],
            }
        )
    return questions


def _parse_draft(raw: str, topics: list[dict], *, max_questions: int) -> list[dict]:
    """解析 L2 起草输出；无 evidence 标记的问题丢弃。"""
    known_keys = {topic["key"]: topic for topic in topics}
    questions: list[dict] = []
    for line in raw.splitlines():
        line = line.strip().lstrip("-•*0123456789.、） ")
        if not line:
            continue
        if "依据：" not in line and "依据:" not in line:
            continue  # 无 evidence 的问题丢弃
        for key, topic in known_keys.items():
            evidence = topic["evidence"]
            fingerprint = json.dumps(evidence, ensure_ascii=False)
            marker = next(
                (v for v in (evidence.get("event_id"), evidence.get("plan_id"), evidence.get("project_id")) if v),
                None,
            )
            if marker and marker in line:
                text = line.split("（依据")[0].strip(" ；;，")
                questions.append({"key": key, "text": text, "evidence": evidence})
                break
        if len(questions) >= max_questions:
            break
    return questions


def draft_questions(
    topics: list[dict],
    context: dict,
    *,
    max_questions: int = 3,
    pool=None,
    send=None,
) -> tuple[list[dict], str]:
    """L2 便宜模型起草；失败回落确定性模板。返回 (questions, drafter)。"""
    if not topics:
        return [], "none"
    fallback = _fallback_questions(topics, max_questions=max_questions)
    try:
        from plobi.quota.route import QuotaAwareCompleter

        brief = json.dumps(
            {"topics": topics, "current_config": context},
            ensure_ascii=False,
            indent=1,
        )
        completer = QuotaAwareCompleter(pool=pool, send=send)
        raw = completer(CHECKIN_PROMPT.format(max_questions=max_questions, brief=brief), route=None)
        if not raw:
            return fallback, "fallback:no_completer"
        parsed = _parse_draft(raw, topics, max_questions=max_questions)
        if not parsed:
            return fallback, "fallback:no_evidence_in_draft"
        return parsed, "l2"
    except Exception as exc:  # 额度源全挂/依赖缺失都不能挡住回访卡
        logger.warning("checkin: L2 drafting failed: %s", exc)
        return fallback, "fallback:error"


# ── build + store ────────────────────────────────────────────────────────────


def build_checkin(
    *,
    now: Optional[datetime] = None,
    db_path: Path | str | None = None,
    pool=None,
    send=None,
    draft: bool = True,
) -> dict:
    """选题 → 去重 → 起草 → 落卡。返回结果 dict（含 skipped_reason 时未落卡）。"""
    moment = now or datetime.now()
    today = moment.date().isoformat()
    cfg = load_config()
    conn = store.connect(db_path)
    try:
        existing = store.get_checkin_card(conn, today)
        if existing is not None:
            return {"for_date": today, "skipped_reason": "already_exists", "card": existing.to_dict()}
        if not cfg.get("enabled", True):
            return {"for_date": today, "skipped_reason": "disabled"}
        if cfg.get("frequency") == "weekly" and _within_weekly_window(conn, moment):
            return {"for_date": today, "skipped_reason": "weekly_cadence"}

        # 降频护栏：连续 N 天被忽略 → 降为每周一次并跳过今天。
        if consecutive_dismissed(conn) >= int(cfg.get("downgrade_after_ignores", 3)):
            if cfg.get("frequency") != "weekly":
                cfg["frequency"] = "weekly"
                save_config(cfg)
            return {
                "for_date": today,
                "skipped_reason": "downgraded_to_weekly",
                "payload": {"consecutive_dismissed": consecutive_dismissed(conn)},
            }

        topics: list[dict] = []
        topics.extend(_topic_event_diffs(conn, moment))
        plan_topic = _topic_plan_status(conn, moment)
        if plan_topic:
            topics.append(plan_topic)
        topics.extend(
            _topic_unadvanced_projects(conn, moment, days=int(cfg.get("unadvanced_days", 6)))
        )
        rate_topics = _topic_change_rate(
            conn,
            moment,
            window_days=int(cfg.get("change_rate_window_days", 7)),
            threshold=float(cfg.get("change_rate_threshold", 0.5)),
            min_sample=int(cfg.get("change_rate_min_sample", 3)),
        )
        topics.extend(rate_topics)

        asked = _asked_keys(conn, moment, dedupe_hours=int(cfg.get("dedupe_hours", 72)))
        topics = [t for t in topics if t["key"] not in asked]
        if not topics:
            return {"for_date": today, "skipped_reason": "no_topics"}

        max_questions = max(1, min(3, int(cfg.get("max_questions", 3))))
        if draft:
            questions, drafter = draft_questions(
                topics,
                _current_config_snapshot(conn),
                max_questions=max_questions,
                pool=pool,
                send=send,
            )
        else:
            questions, drafter = _fallback_questions(topics, max_questions=max_questions), "deterministic"
        if not questions:
            return {"for_date": today, "skipped_reason": "no_questions"}

        payload: dict[str, Any] = {
            "topics": [t["key"] for t in topics],
            "drafter": drafter,
        }
        if rate_topics:
            payload["reflection"] = (
                f"数据缺口建议：{rate_topics[0]['summary']}——建议接入更稳定的"
                "数据源（如课表/日历），此为提案，不会自动执行。"
            )
        seq = store.next_confirm_seq(conn)
        card = store.upsert_checkin_card(
            conn,
            for_date=today,
            questions=questions,
            payload=payload,
            confirm_seq=seq,
            confirm_seq_at=store.now_iso(),
        )
        return {"for_date": today, "card": card.to_dict(), "topics": [t["key"] for t in topics]}
    finally:
        conn.close()


def _current_config_snapshot(conn) -> dict:
    """模板与节奏现值——起草时给 L2 的“当前配置”上下文。"""
    try:
        templates = [
            t.to_dict() for t in store.list_routine_templates(conn, enabled_only=True)
        ]
    except Exception:
        templates = []
    try:
        from .planning import paced_projects

        pace = [
            {"project": entry.name, "weekly_hours": hours}
            for entry, hours in paced_projects()
        ]
    except Exception:
        pace = []
    return {"routine_templates": templates, "project_pace": pace}


def format_checkin_text(card: "store.CheckinCard | dict") -> str:
    """钉钉正文：问题列表 + 反思（若有）+ 回复提示。接受卡对象或 to_dict()。"""
    if isinstance(card, dict):
        card = store.CheckinCard(
            id=card.get("id", ""),
            for_date=card.get("for_date", ""),
            kind=card.get("kind", store.CHECKIN_KIND),
            questions=card.get("questions") or [],
            payload=card.get("payload") or {},
            confirm_seq=card.get("confirm_seq"),
        )
    seq = card.confirm_seq if card.confirm_seq is not None else "?"
    lines = [f"【秘书回访 {card.for_date} #{seq}】"]
    for index, question in enumerate(card.questions, start=1):
        lines.append(f"{index}. {question.get('text', '')}")
    payload = card.payload or {}
    if payload.get("reflection"):
        lines.append(f"〔反思〕{payload['reflection']}")
    lines.append(f"回复：确认{seq} / 忽略{seq}（回答可在桌面回访会话里直接说）")
    return "\n".join(lines)


# ── Mind 存档（自由文本原文 → 该日 digest） ──────────────────────────────────


def archive_free_text(
    text: str,
    *,
    day: Optional[datetime] = None,
    source: str = "dingtalk",
    writer=None,
) -> bool:
    """把用户自由文本回复原文追加进 Mind 该日 digest（AI 写入根，append）。

    本期不做自由文本解析——原文存档，供 L2/后续机制消化。
    """
    from plobi.mind import AI_WRITE_ROOT, get_writer

    sink = writer or get_writer()
    if not sink.available:
        logger.info("checkin: mind unavailable; free text not archived")
        return False
    target_day = (day or datetime.now()).date().isoformat()
    stamp = (day or datetime.now()).strftime("%H:%M")
    relative = f"{AI_WRITE_ROOT}/{target_day}/checkin-replies.md"
    content = f"\n- [{stamp}][{source}] {text.strip()}\n"
    result = sink.write_one(relative, content, mode="append")
    if not result.ok:
        logger.warning("checkin: mind archive failed: %s", result.detail)
    return result.ok


# ── C6b：配置提案（R3 分级——改配置必须人批） ─────────────────────────────────


def propose_config_change(
    conn,
    *,
    routine_updates: Optional[list[dict]] = None,
    pace_updates: Optional[list[dict]] = None,
    avoid_windows: Optional[list[dict]] = None,
    now: Optional[datetime] = None,
) -> store.CheckinCard:
    """用户在回访会话里的明确指令 → 配置提案卡。**不直接生效**。

    ``routine_updates``: [{"template_id", "start_time"?, "end_time"?}]
    ``pace_updates``:    [{"project_id", "weekly_hours"}]
    每天每类一张提案卡（重复调用 = 覆盖为最新提案）。
    """
    moment = now or datetime.now()
    routine_updates = list(routine_updates or [])
    pace_updates = list(pace_updates or [])
    avoid_windows = list(avoid_windows or [])
    if not routine_updates and not pace_updates and not avoid_windows:
        raise store.AgendaValidationError("proposal needs at least one change")

    for upd in routine_updates:
        tid = str(upd.get("template_id") or "").strip()
        if store.get_routine_template(conn, tid) is None:
            raise store.AgendaValidationError(f"unknown routine template {tid!r}")
        for field_name in ("start_time", "end_time"):
            if upd.get(field_name) is not None:
                store._validate_hhmm(upd[field_name], field_name)
    if pace_updates:
        from plobi.agents.registry import load_registry

        registry = load_registry()
        for upd in pace_updates:
            entry = registry.get(str(upd.get("project_id") or ""))
            if entry is None or entry.role != "l2_project":
                pid = str(upd.get("project_id") or "")
                raise store.AgendaValidationError(f"unknown l2_project {pid!r}")
            try:
                hours = float(upd.get("weekly_hours"))
            except (TypeError, ValueError) as exc:
                raise store.AgendaValidationError("weekly_hours must be a number") from exc
            if hours <= 0 or hours > 168:
                raise store.AgendaValidationError("weekly_hours must be in (0, 168]")

    cleaned_windows: list[dict] = []
    for win in avoid_windows:
        start = str(win.get("start_time") or "").strip()
        end = str(win.get("end_time") or "").strip()
        if not start or not end:
            raise store.AgendaValidationError(
                "avoid_windows 需要每条都带 start_time 与 end_time"
            )
        start_h = store._validate_hhmm(start, "start_time")
        end_h = store._validate_hhmm(end, "end_time")
        if end_h <= start_h:
            raise store.AgendaValidationError(
                f"avoid 窗 end_time 必须晚于 start_time（{start}-{end}）"
            )
        cleaned_windows.append({"start_time": start_h, "end_time": end_h})

    questions = []
    for upd in routine_updates:
        tid = str(upd.get("template_id"))
        questions.append(
            {
                "key": f"proposal:routine:{tid}",
                "text": (
                    f"把作息模板 {tid} 的时间改为 "
                    f"{upd.get('start_time') or '（不变）'}-{upd.get('end_time') or '（不变）'}？"
                ),
                "evidence": {"template_id": tid},
            }
        )
    for upd in pace_updates:
        pid = str(upd.get("project_id"))
        questions.append(
            {
                "key": f"proposal:pace:{pid}",
                "text": f"把项目「{pid}」的每周节奏改为 {float(upd['weekly_hours']):g} 小时？",
                "evidence": {"project_id": pid, "weekly_hours": float(upd["weekly_hours"])},
            }
        )
    for idx, win in enumerate(cleaned_windows):
        questions.append(
            {
                "key": f"proposal:avoid:{idx}",
                "text": (
                    f"以后 {win['start_time']}–{win['end_time']} 不排会议/项目块？"
                ),
                "evidence": {
                    "kind": "avoid_window",
                    "start_time": win["start_time"],
                    "end_time": win["end_time"],
                },
            }
        )

    seq = store.next_confirm_seq(conn)
    return store.upsert_checkin_card(
        conn,
        for_date=moment.date().isoformat(),
        kind="config_proposal",
        questions=questions,
        payload={
            "changes": {
                "routine_updates": routine_updates,
                "pace_updates": pace_updates,
                "avoid_windows": cleaned_windows,
            }
        },
        confirm_seq=seq,
        confirm_seq_at=store.now_iso(),
    )


def apply_proposal(conn, card: store.CheckinCard) -> list[str]:
    """落地已确认的配置提案。只由 ``AgendaService.confirm_card`` 调用。"""
    changes = (card.payload or {}).get("changes") or {}
    applied: list[str] = []

    for upd in changes.get("routine_updates") or []:
        current = store.get_routine_template(conn, str(upd.get("template_id") or ""))
        if current is None:
            continue
        store.upsert_routine_template(
            conn,
            template_id=current.id,
            title=current.title,
            start_time=str(upd.get("start_time") or current.start_time),
            end_time=str(upd.get("end_time") or current.end_time),
            weekdays=list(current.weekdays),
            enabled=current.enabled if upd.get("enabled") is None else bool(upd["enabled"]),
        )
        applied.append(f"routine:{current.id}")

    pace_updates = changes.get("pace_updates") or []
    if pace_updates:
        from plobi.agents.registry import load_registry

        registry = load_registry()
        for upd in pace_updates:
            entry = registry.get(str(upd.get("project_id") or ""))
            if entry is None:
                continue
            entry.pace = {"weekly_hours": float(upd["weekly_hours"])}
            applied.append(f"pace:{entry.name}")
        registry.save()
    return applied
