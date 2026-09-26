"""Score helpers for scripts/plobi/e2e_secretary.py — no live model."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _load():
    path = REPO / "scripts" / "plobi" / "e2e_secretary.py"
    spec = importlib.util.spec_from_file_location("plobi_e2e_secretary", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


E = _load()


def test_collect_tool_names_from_calls_and_content():
    names = E.collect_tool_names(
        [
            {"tool_name": "web_search"},
            {"tool_calls": [{"function": {"name": "plobi_secretary_ask"}}]},
            {"content": "called plobi_secretary_ask"},
        ]
    )
    assert "plobi_secretary_ask" in names
    assert "web_search" in names


def test_score_turn_pass_refresh():
    score = E.score_turn(
        "明天的日常安排是什么",
        "refresh_agenda",
        [
            {
                "role": "assistant",
                "tool_calls": [{"function": {"name": "plobi_secretary_ask", "arguments": '{"intent": "refresh_agenda"}'}}],
            }
        ],
        n3_messages=[
            {"role": "user", "content": "明天的日常安排是什么"},
            {"role": "assistant", "content": "【L1 派工简报】\nintent: refresh_agenda"},
        ],
    )
    assert score.asked
    assert score.ok
    assert score.n3 is True
    assert score.forbidden == []


def test_score_turn_fails_on_terminal():
    score = E.score_turn(
        "明天的日常安排是什么",
        "refresh_agenda",
        [{"tool_name": "terminal"}, {"tool_name": "plobi_secretary_ask"}],
    )
    assert score.asked
    assert not score.ok
    assert "terminal" in score.forbidden


def test_score_turn_write_briefing_route():
    score = E.score_turn(
        "根据明天的日程写一段早报",
        "write_briefing",
        [{"content": "plobi_secretary_ask intent=write_briefing", "tool_name": "plobi_secretary_ask"}],
        stdout='{"route": "workbuddy", "briefing": "hi"}',
    )
    assert score.asked
    assert score.route == "workbuddy"
    assert score.ok


def test_dead_honest():
    score = E.score_turn(
        "明天的日常安排是什么",
        "refresh_agenda",
        [{"content": json.dumps({"ok": False, "dead": True})}],
        stdout="plobi_secretary_ask",
    )
    assert score.dead_honest is True


def test_score_direct_dead_or_events():
    dead = E.score_direct_turn(
        "明天的日常安排是什么",
        "refresh_agenda",
        {"ok": False, "dead": True, "error": "chatlog 未启动或 /health 失败，采集不通"},
        n3_messages=[],
    )
    assert dead.direct
    assert dead.dead_honest is True
    assert dead.ok

    events = E.score_direct_turn(
        "明天的日常安排是什么",
        "refresh_agenda",
        {"ok": True, "agenda": {"events": [{"title": "高数课"}]}},
        n3_messages=[
            {"role": "user", "content": "明天的日常安排是什么"},
            {"role": "assistant", "content": "【L1 派工简报】\nintent: refresh_agenda"},
        ],
    )
    assert events.has_events is True
    assert events.n3 is True
    assert events.ok


def test_score_direct_briefing_requires_route():
    good = E.score_direct_turn(
        "根据明天的日程写一段早报",
        "write_briefing",
        {"ok": True, "route": "workbuddy", "briefing": "hi"},
        n3_messages=[
            {"role": "user", "content": "根据明天的日程写一段早报"},
            {"role": "assistant", "content": "【L1 派工简报】\nintent: write_briefing"},
        ],
    )
    assert good.route == "workbuddy"
    assert good.ok

    bad = E.score_direct_turn(
        "根据明天的日程写一段早报",
        "write_briefing",
        {"ok": True, "route": "mystery", "briefing": "hi"},
        n3_messages=[
            {"role": "user", "content": "根据明天的日程写一段早报"},
            {"role": "assistant", "content": "【L1 派工简报】\nintent: write_briefing"},
        ],
    )
    assert not bad.ok


def test_run_direct_pair_with_mock_ask(monkeypatch, tmp_path):
    def ask(intent: str, user_text: str, **_kwargs):
        if intent == "write_briefing":
            return {
                "ok": True,
                "intent": intent,
                "user_text": user_text,
                "route": "fallback",
                "agenda": {"events": [{"title": "课"}]},
            }
        return {
            "ok": True,
            "intent": intent,
            "user_text": user_text,
            "agenda": {"events": [{"title": "课"}]},
        }

    monkeypatch.setattr(E, "plobi_home", lambda: tmp_path)
    monkeypatch.setattr(E, "agenda_state_db", lambda home=None: tmp_path / "state.db")
    monkeypatch.setattr(
        E,
        "_load_messages_from_db",
        lambda *_a, **_k: [
            {"role": "user", "content": "明天的日常安排是什么"},
            {"role": "assistant", "content": "【L1 派工简报】\nintent: refresh_agenda"},
            {"role": "user", "content": "根据明天的日程写一段早报"},
            {"role": "assistant", "content": "【L1 派工简报】\nintent: write_briefing"},
        ],
    )
    scores = E.run_direct_pair(ask=ask)
    assert len(scores) == 2
    assert all(s.asked and s.direct for s in scores)
    assert scores[1].route == "fallback"
    assert all(s.n3 for s in scores)


def test_main_direct_uses_run_direct(monkeypatch, capsys):
    def fake_direct():
        return [
            E.score_direct_turn(
                "明天的日常安排是什么",
                "refresh_agenda",
                {"ok": False, "dead": True, "error": "采集不通"},
                n3_messages=[],
            ),
            E.score_direct_turn(
                "根据明天的日程写一段早报",
                "write_briefing",
                {"ok": False, "dead": True, "error": "采集不通"},
                n3_messages=[],
            ),
        ]

    monkeypatch.setattr(E, "run_direct_pair", fake_direct)
    # WP-SEC-VOCAB: --direct 也会跑 query/decide 直调用例；这里换成假分，
    # 免得单测去碰真实 agenda 库（真实用例由 --direct 真机跑）。
    # WP-PROJECT-PORTFOLIO: 同上，project_status + plan_day 也走 store-only。
    monkeypatch.setattr(
        E,
        "run_direct_reads",
        lambda: [
            E.score_direct_turn("今天有什么安排", "query_agenda", {"ok": True, "range": "today"}),
            E.score_direct_turn(
                "把那条待确认忽略掉",
                "decide_pending",
                {"ok": True, "decision": "dismiss", "deleted": True},
            ),
            E.score_direct_turn("各项目怎么样了", "project_status", {"ok": True, "projects": [], "seeded": []}),
            E.score_direct_turn("排一下明天", "plan_day", {"ok": True, "for_date": "2026-09-16", "items": [], "conflict_count": 0}),
        ],
    )
    assert E.main(["--direct", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["pair_pass"] == 1
    assert out["direct_reads_pass"] == 4


def test_run_direct_reads_with_mock_ask(monkeypatch, tmp_path):
    """两个直调用例：ok 与 intent 都要对上（不打采集、不 spawn）。"""
    seen: list[tuple[str, dict]] = []

    def ask(intent: str, user_text: str, **kwargs):
        seen.append((intent, kwargs))
        if intent == "query_agenda":
            return {"ok": True, "intent": intent, "range": kwargs.get("range"), "events": []}
        if intent == "project_status":
            return {"ok": True, "intent": intent, "projects": [], "seeded": []}
        if intent == "plan_day":
            return {"ok": True, "intent": intent, "for_date": "2026-09-16", "items": [], "conflict_count": 0}
        return {
            "ok": True,
            "intent": intent,
            "decision": kwargs.get("decision"),
            "event": {"title": E.E2E_PENDING_TITLE, "status": "dismissed"},
            "deleted": True,
        }

    monkeypatch.setattr(E, "_seed_e2e_pending", lambda: "evt_e2e")
    scores = E.run_direct_reads(ask=ask)

    assert [s.intent for s in scores] == [
        "query_agenda",
        "decide_pending",
        "project_status",
        "plan_day",
    ]
    assert all(s.ok and s.asked and s.direct for s in scores)
    assert seen[0][1]["range"] == "today"
    assert seen[1][1]["decision"] == "dismiss"
    assert seen[1][1]["title"] == E.E2E_PENDING_TITLE
    # WP-PROJECT-PORTFOLIO: project_status + plan_day are store-only, no
    # kwargs are forwarded (the intent does not take any in this fixture).
    assert seen[2][1] == {}
    assert seen[3][1] == {}


def test_run_direct_reads_reports_a_failed_case(monkeypatch):
    monkeypatch.setattr(E, "_seed_e2e_pending", lambda: "evt_e2e")
    scores = E.run_direct_reads(
        ask=lambda intent, user_text, **kwargs: {"ok": False, "error": "没有匹配的待确认项"}
    )
    assert not scores[0].ok
    assert scores[0].error


def test_run_direct_reads_reports_a_bad_seed(monkeypatch):
    def boom():
        raise RuntimeError("db locked")

    monkeypatch.setattr(E, "_seed_e2e_pending", boom)
    scores = E.run_direct_reads(ask=lambda intent, user_text, **kwargs: {"ok": True})
    assert not scores[0].ok
    assert "seed failed" in scores[0].error


def test_run_pair_with_mock_sender():
    def send(text: str) -> str:
        return f"plobi_secretary_ask {text} " + (
            '{"route": "fallback"}' if "早报" in text else '{"dead": true}'
        )

    scores = E.run_pair(send_turn=send, live=False)
    assert len(scores) == 2
    assert all(s.asked for s in scores)


def test_summarize_pair_rate():
    good = E.score_turn("明天的日常安排是什么", "refresh_agenda", [{"tool_name": "plobi_secretary_ask"}])
    bad = E.score_turn("x", "refresh_agenda", [{"tool_name": "terminal"}])
    summary = E.summarize([[good, good], [bad, good]])
    assert summary["repeat"] == 2
    assert summary["pair_pass"] == 1


def test_main_no_live():
    assert E.main(["--no-live", "--json"]) == 0
