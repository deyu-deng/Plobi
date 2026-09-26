"""C6 daily check-in: deterministic topics, dedupe, guard, card state machine.

验收对照（AGENT-TASK-BACKEND C6）：
1. 脚本逻辑（build_checkin）→ 卡 ≤3 问、每问带 evidence、去重生效；
2. 「3 天前 dismiss 的计划 + 6 天未推进的 pace 项目」两类选题命中；
3. 确认N/忽略N 状态机闭环 + 降频护栏；
4. 配置提案（C6b）确认才落地、忽略不动。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from plobi.agenda import checkin, store
from plobi.agents.registry import AgentEntry, AgentRegistry

NOW = datetime(2026, 9, 10, 21, 30)  # 周四晚，回访时刻


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """隔离：agenda.db / checkin.json / 项目注册表 全部进 tmp。"""
    db = tmp_path / "agenda.db"
    monkeypatch.setenv("PLOBI_CHECKIN_CONFIG", str(tmp_path / "checkin.json"))
    monkeypatch.setenv("PLOBI_AGENDA_DB", str(db))
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(tmp_path / "projects.yaml"))
    registry = AgentRegistry(path=tmp_path / "projects.yaml")
    registry.upsert(
        AgentEntry.from_dict("simulation", {"role": "l2_project", "pace": {"weekly_hours": 6}})
    )
    registry.save()  # upsert 只改内存；磁盘副本供 AgentRegistry.load 读取
    monkeypatch.setattr("plobi.agents.registry.load_registry", lambda path=None: registry)
    # 测试时钟：store.now_iso 用真实墙钟会让 24h 窗口 / 72h 去重全部失真。
    monkeypatch.setattr("plobi.agenda.store.now_iso", lambda: NOW.isoformat())
    return db


def _mk_plan(db_path, day: str, *, with_block: str | None = None, items=()) -> str:
    conn = store.connect(db_path)
    try:
        plan = store.upsert_daily_plan(
            conn, for_date=day, status="pending", summary="s"
        )
        rows = [
            {
                "event_id": f"evt:{title}",
                "title": title,
                "start_at": start,
                "kind": "task",
                "evidence": {"kind": "event", "event_id": f"evt:{title}"},
            }
            for title, start in items
        ]
        if with_block:
            rows.append(
                {
                    "event_id": f"project:{with_block}",
                    "title": f"推进 · {with_block}",
                    "start_at": f"{day}T09:00:00",
                    "end_at": f"{day}T10:00:00",
                    "kind": "task",
                    "evidence": {
                        "kind": "project_block",
                        "project_id": with_block,
                        "pace": "weekly_hours:6",
                    },
                }
            )
        store.replace_plan_items(conn, plan.id, rows)
        return plan.id
    finally:
        conn.close()


# ── 验收 2：两类选题命中 ─────────────────────────────────────────────────────


def test_plan_status_and_unadvanced_topics_hit(env):
    db = env
    # 6 天连续未推进：day-1..day-6 各有一个 simulation 块、计划未确认
    for offset in range(1, 7):
        day = (NOW - timedelta(days=offset)).date().isoformat()
        _mk_plan(db, day, with_block="simulation")
    # 昨夜计划（for_date=今天）被忽略
    today = NOW.date().isoformat()
    plan_id = _mk_plan(db, today, items=[("组会", f"{today}T10:00:00")])
    conn = store.connect(db)
    try:
        store.set_plan_status(conn, plan_id, "dismissed", clear_seq=True)
    finally:
        conn.close()

    result = checkin.build_checkin(now=NOW, db_path=db, draft=False)
    assert "card" in result, result
    assert any(t.startswith("plan_status:") for t in result["topics"])
    assert "unadvanced:simulation" in result["topics"]

    questions = result["card"]["questions"]
    assert 1 <= len(questions) <= 3  # ≤3 问
    assert all(q.get("evidence") for q in questions)  # 每问带 evidence
    body = checkin.format_checkin_text(result["card"])
    assert f"确认{result['card']['confirm_seq']}" in body


def test_no_topics_means_no_card(env):
    result = checkin.build_checkin(now=NOW, db_path=env, draft=False)
    assert result.get("skipped_reason") == "no_topics"


# ── 验收 1：去重 + 每天一条 ──────────────────────────────────────────────────


def test_same_day_card_not_duplicated(env):
    db = env
    conn = store.connect(db)
    try:
        store.create_event(
            conn, title="练琴", start_at=f"{NOW.date().isoformat()}T19:00:00",
            end_at=f"{NOW.date().isoformat()}T20:00:00", kind="task", source="manual",
        )
    finally:
        conn.close()

    first = checkin.build_checkin(now=NOW, db_path=db, draft=False)
    assert "card" in first
    assert any(t.startswith("event_diff:create:") for t in first["topics"])

    again = checkin.build_checkin(now=NOW + timedelta(hours=1), db_path=db, draft=False)
    assert again.get("skipped_reason") == "already_exists"


def test_same_diff_not_reasked_within_72h(env):
    db = env
    created = (NOW - timedelta(hours=1)).isoformat()
    conn = store.connect(db)
    try:
        store.create_event(
            conn, title="练琴", start_at=f"{NOW.date().isoformat()}T19:00:00",
            end_at=f"{NOW.date().isoformat()}T20:00:00", kind="task", source="manual",
        )
        conn.execute("UPDATE events SET created_at = ?", (created,))
        conn.commit()
    finally:
        conn.close()

    first = checkin.build_checkin(now=NOW, db_path=db, draft=False)
    assert any(t.startswith("event_diff:create:") for t in first["topics"])

    # 12h 后同一差异仍在 24h 窗口内，但 72h 去重挡住 → 无题可问
    second = checkin.build_checkin(now=NOW + timedelta(hours=12), db_path=db, draft=False)
    assert second.get("skipped_reason") == "no_topics"


# ── 验收 3：状态机闭环 + 降频护栏 ────────────────────────────────────────────


def test_card_confirm_and_dismiss_state_machine(env):
    db = env
    conn = store.connect(db)
    try:
        store.create_event(
            conn, title="练琴", start_at=f"{NOW.date().isoformat()}T19:00:00",
            end_at=f"{NOW.date().isoformat()}T20:00:00", kind="task", source="manual",
        )
    finally:
        conn.close()

    result = checkin.build_checkin(now=NOW, db_path=db, draft=False)
    card = result["card"]

    from plobi.agenda.service import AgendaService

    service = AgendaService(db_path=db)
    confirmed = service.resolve_by_seq(card["confirm_seq"], accept=True)
    assert confirmed.status == "confirmed"
    assert confirmed.confirm_seq is None  # 已消费

    # 次日新卡 → 忽略
    next_day = NOW + timedelta(days=1)
    conn = store.connect(db)
    try:
        store.create_event(
            conn, title="练琴2", start_at=f"{next_day.date().isoformat()}T19:00:00",
            end_at=f"{next_day.date().isoformat()}T20:00:00", kind="task", source="manual",
        )
    finally:
        conn.close()
    second = checkin.build_checkin(now=next_day, db_path=db, draft=False)
    dismissed = service.resolve_by_seq(second["card"]["confirm_seq"], accept=False)
    assert dismissed.status == "dismissed"

    with pytest.raises(Exception):
        service.resolve_by_seq(card["confirm_seq"], accept=True)  # 已消费的 seq 失效


def test_three_dismissed_cards_downgrade_to_weekly(env):
    db = env
    conn = store.connect(db)
    try:
        for offset in (3, 2, 1):
            day = (NOW - timedelta(days=offset)).date().isoformat()
            store.upsert_checkin_card(conn, for_date=day, status="dismissed")
    finally:
        conn.close()

    result = checkin.build_checkin(now=NOW, db_path=db, draft=False)
    assert result.get("skipped_reason") == "downgraded_to_weekly"

    cfg = json.loads(checkin.config_path().read_text(encoding="utf-8"))
    assert cfg["frequency"] == "weekly"

    # 降频后一周内沉默
    again = checkin.build_checkin(now=NOW + timedelta(days=1), db_path=db, draft=False)
    assert again.get("skipped_reason") == "weekly_cadence"
    # 满 7 天后恢复
    later = checkin.build_checkin(now=NOW + timedelta(days=8), db_path=db, draft=False)
    assert later.get("skipped_reason") != "weekly_cadence"


def test_consecutive_dismissed_counts_run(env):
    db = env
    conn = store.connect(db)
    try:
        store.upsert_checkin_card(conn, for_date="2026-09-08", status="dismissed")
        store.upsert_checkin_card(conn, for_date="2026-09-09", status="confirmed")  # 断
        store.upsert_checkin_card(conn, for_date="2026-09-10", status="dismissed")
        assert checkin.consecutive_dismissed(conn) == 1
    finally:
        conn.close()


# ── 起草：L2 带 evidence，无 evidence 丢弃 ───────────────────────────────────


def _one_topic():
    return [
        {
            "key": "event_diff:create:evt-1",
            "topic": "event_created",
            "evidence": {"event_id": "evt-1", "title": "练琴", "start_at": "2026-09-10T19:00:00"},
            "summary": "你新增了日程「练琴」",
        }
    ]


class _FakePool:
    """让 QuotaAwareCompleter 直接走注入的 send，不探测真实额度源。"""

    def resolve(self):
        return object()  # 任意非 None source

    def mark_failed(self, name):
        pass


def test_l2_draft_keeps_only_evidenced_lines(env):
    def fake_send(prompt, source):
        return (
            "最近怎么样？\n"  # 无依据 → 丢弃
            "1. 看到你新增了「练琴」，这个安排要固定下来吗？（依据：evt-1）"
        )

    questions, drafter = checkin.draft_questions(
        _one_topic(), {}, max_questions=3, pool=_FakePool(), send=fake_send
    )
    assert drafter == "l2"
    assert len(questions) == 1
    assert questions[0]["evidence"]["event_id"] == "evt-1"
    assert "练琴" in questions[0]["text"]


def test_l2_draft_failure_falls_back_deterministic(env):
    def broken_send(prompt, source):
        raise RuntimeError("no quota")

    questions, drafter = checkin.draft_questions(
        _one_topic(), {}, max_questions=3, pool=_FakePool(), send=broken_send
    )
    assert drafter.startswith("fallback")
    assert len(questions) == 1
    assert questions[0]["evidence"]["event_id"] == "evt-1"


def test_draft_caps_at_three_questions(env):
    topics = [
        {"key": f"event_diff:create:e{i}", "topic": "event_created",
         "evidence": {"event_id": f"e{i}"}, "summary": f"差异{i}"}
        for i in range(5)
    ]
    questions, _ = checkin.draft_questions(topics, {}, max_questions=3, draft=None) if False else checkin.draft_questions(
        topics, {}, max_questions=3, send=lambda p, s: "\n".join(
            f"问{i}（依据：e{i}）" for i in range(5)
        )
    )
    assert len(questions) == 3


# ── Mind 存档（自由文本原文） ────────────────────────────────────────────────


class _StubWriter:
    available = True

    def __init__(self):
        self.calls = []

    def write_one(self, relative_path, content, *, mode="overwrite"):
        self.calls.append((relative_path, content, mode))

        from plobi.mind.writer import WriteResult

        return WriteResult(ok=True, written=[relative_path], skipped=[])


def test_free_text_archived_to_mind_daily_digest():
    writer = _StubWriter()
    moment = datetime(2026, 9, 10, 21, 45)
    assert checkin.archive_free_text(
        "我想把睡眠改到 23 点", day=moment, writer=writer
    ) is True
    relative, content, mode = writer.calls[0]
    assert "Loom/raw/chat-logs/digested/2026-09-10/checkin-replies.md" == relative
    assert mode == "append"
    assert "睡眠改到 23 点" in content


# ── C6b：配置提案确认才落地 ──────────────────────────────────────────────────


def test_proposal_applies_only_after_confirm(env):
    db = env
    conn = store.connect(db)
    try:
        card = checkin.propose_config_change(
            conn,
            routine_updates=[
                {"template_id": "seed-sleep", "start_time": "23:00", "end_time": "07:00"}
            ],
            now=NOW,
        )
    finally:
        conn.close()

    # 未确认前不生效（R3：改配置须人批）
    conn = store.connect(db)
    try:
        assert store.get_routine_template(conn, "seed-sleep").start_time == "23:30"
    finally:
        conn.close()

    from plobi.agenda.service import AgendaService

    service = AgendaService(db_path=db)
    service.resolve_by_seq(card.confirm_seq, accept=True)

    conn = store.connect(db)
    try:
        updated = store.get_routine_template(conn, "seed-sleep")
        assert updated.start_time == "23:00"
        assert updated.end_time == "07:00"
    finally:
        conn.close()


def test_proposal_dismiss_leaves_config_untouched(env):
    db = env
    conn = store.connect(db)
    try:
        card = checkin.propose_config_change(
            conn, pace_updates=[{"project_id": "simulation", "weekly_hours": 10}], now=NOW
        )
    finally:
        conn.close()

    from plobi.agenda.service import AgendaService

    AgendaService(db_path=db).resolve_by_seq(card.confirm_seq, accept=False)

    registry = AgentRegistry.load(env.parent / "projects.yaml")
    assert registry.get("simulation").weekly_hours == 6.0  # 未动


def test_proposal_rejects_unknown_template(env):
    conn = store.connect(env)
    try:
        with pytest.raises(store.AgendaValidationError):
            checkin.propose_config_change(
                conn,
                routine_updates=[{"template_id": "nope", "start_time": "08:00", "end_time": "09:00"}],
                now=NOW,
            )
    finally:
        conn.close()


# ── C6b：L1 窄工具 plobi_checkin_respond ────────────────────────────────────


def _load_master_tools():
    """插件目录带连字符，按文件路径加载（沿用 test_master_tools.py 的做法）。"""
    import importlib.util

    path = (
        Path(__file__).resolve().parents[2]
        / "plugins" / "plobi-north-star" / "master_tools.py"
    )
    spec = importlib.util.spec_from_file_location("plobi_master_tools_c6", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MT = _load_master_tools()


def test_checkin_respond_creates_proposal_card_only(env):
    db = env
    out = json.loads(MT.handle_checkin_respond({
        "routine_updates": [{"template_id": "seed-sleep", "start_time": "23:00"}]
    }))
    assert out["ok"] is True, out
    assert out["confirm_seq"]
    assert "确认" in out["note"]

    # R3：提案未确认前配置不动
    conn = store.connect(db)
    try:
        assert store.get_routine_template(conn, "seed-sleep").start_time == "23:30"
    finally:
        conn.close()

    from plobi.agenda.service import AgendaService

    AgendaService(db_path=db).resolve_by_seq(out["confirm_seq"], accept=True)
    conn = store.connect(db)
    try:
        assert store.get_routine_template(conn, "seed-sleep").start_time == "23:00"
    finally:
        conn.close()


def test_checkin_respond_rejects_bad_input(env):
    assert json.loads(MT.handle_checkin_respond({}))["ok"] is False
    assert json.loads(MT.handle_checkin_respond({"routine_updates": "oops"}))["ok"] is False
    out = json.loads(MT.handle_checkin_respond(
        {"pace_updates": [{"project_id": "nope", "weekly_hours": 5}]}
    ))
    assert out["ok"] is False


def test_checkin_respond_free_text_only_archives(env, monkeypatch):
    calls = []

    class _W:
        available = True

        def write_one(self, rel, content, *, mode="overwrite"):
            calls.append((rel, mode))
            from plobi.mind.writer import WriteResult

            return WriteResult(ok=True, written=[rel], skipped=[])

    monkeypatch.setattr("plobi.mind.get_writer", lambda: _W())
    out = json.loads(MT.handle_checkin_respond({"free_text": "想把睡眠改到 23 点"}))
    assert out["ok"] is True and out["archived"] is True
    assert calls and calls[0][1] == "append"
    assert "checkin-replies.md" in calls[0][0]
