"""M1 全链路端到端：一条群消息走完 采集→规则→确认→入库(pending)→推送→看板→落定。

对应验收：docs/specs/MVP-AI-Secretary-Requirements.md §3.2/§3.3 与 §8 第 6 项；
状态机对齐 docs/specs/ui-l1-console-spec.md §4（确认入口三处等价）。

"群里通知明天调课" 场景：
    微信班级群消息「明天的高数课调到下午3点」
    → chatlog webhook（真实路由，非直调 pipeline）
    → 白名单 + 去重 + 本地规则 + 启发式确认
    → SQLite events（pending，带 prev_value 新旧值）
    → 钉钉推送（RecordingNotifier 捕获文案与编号）
    → 看板 API 可见（/api/agenda 契约，§5）
    → 「忽略 N」回复落定回滚（ADR-0009）
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plobi.agenda import dispatch as agenda_dispatch
from plobi.agenda import router as agenda_router
from plobi.agenda import service as agenda_service
from plobi.agenda.dispatch import PendingDispatcher, get_dispatcher, set_dispatcher
from plobi.agenda.service import AgendaService
from plobi.collectors.chatlog.config import CollectorConfig
from plobi.collectors.chatlog.confirm import HeuristicConfirmer
from plobi.collectors.chatlog.pipeline import ChatlogPipeline
from plobi.collectors.chatlog.state import SeenStore
from plobi.collectors.chatlog.webhook import router as chatlog_router, set_pipeline
from plobi.notify.base import RecordingNotifier


def _tomorrow(hour: int, minute: int = 0) -> str:
    day = datetime.now().date() + timedelta(days=1)
    return datetime.combine(day, datetime.min.time()).replace(
        hour=hour, minute=minute
    ).isoformat()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """一条真实链路共享的状态：service / dispatcher / pipeline / HTTP app."""
    service = AgendaService(tmp_path / "agenda.db")
    monkeypatch.setattr(agenda_service, "_DEFAULT", service)

    notifier = RecordingNotifier()
    monkeypatch.setattr(
        agenda_dispatch, "_DEFAULT", PendingDispatcher(service=service, notifier=notifier)
    )

    pipeline = ChatlogPipeline(
        config=CollectorConfig(talkers=["班级群"], enabled=True),
        service=service,  # client 不会被 webhook 路径用到
        seen=SeenStore(tmp_path / "seen.db"),
        confirmer=HeuristicConfirmer(),
    )
    set_pipeline(pipeline)

    app = FastAPI()
    app.include_router(agenda_router.router, prefix="/api/agenda")
    app.include_router(chatlog_router, prefix="/api/chatlog")
    with TestClient(app) as http:
        yield type("Env", (), {"http": http, "service": service, "notifier": notifier})

    set_pipeline(None)
    set_dispatcher(None)


def _post_group_message(http: TestClient, *, msg_id: str, content: str) -> dict:
    """chatlog 服务把新消息 POST 到我们的 webhook —— 真实入口。"""
    response = http.post(
        "/api/chatlog/webhook",
        json={
            "data": [
                {
                    "id": msg_id,
                    "talker": "班级群",
                    "senderName": "课代表",
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "content": content,
                }
            ]
        },
    )
    assert response.status_code == 200
    return response.json()


def test_group_reschedule_flows_to_pending_push_and_board(env):
    """主场景：群里通知明天调课 → pending + 推送 + 看板可见。"""
    http, service = env.http, env.service

    # 看板上先有一节明早的高数课（手动录入即事实，ADR-0009 例外）
    seeded = http.post(
        "/api/agenda",
        json={"title": "高数课", "start_at": _tomorrow(8), "kind": "class"},
    )
    assert seeded.status_code == 200
    original_start = seeded.json()["start_at"]

    # 群消息：调课
    report = _post_group_message(
        env.http,
        msg_id="wx_1001",
        content="各位同学，明天的高数课调到下午3点上，地点不变",
    )
    assert report["created"] == [] and len(report["updated"]) == 1
    event_id = report["updated"][0]
    assert report["notified"] == [event_id], "推送必须跟着入库走"

    # 推送文案：§7 硬冻结格式（编号 + 新旧值 + 来源 + 回复语法）
    pushed = env.notifier.sent[0]
    assert "高数课" in pushed
    assert re.search(r"【待确认 #\d+】", pushed), "文案必须带确认编号"
    assert "原值:" in pushed and "→" in pushed and "新值:" in pushed, "必须显示新旧值"
    assert "08:00" in pushed and "15:00" in pushed, "改课前后时刻都要出现"
    assert "「班级群」" in pushed and "调到下午3点" in pushed, "必须带来源与摘录"
    assert "回复: 确认" in pushed and "忽略" in pushed

    # 看板（§5 契约）：pending 可见、带 prev_value 证据
    board = http.get(
        "/api/agenda",
        params={
            "from": datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
            "to": (datetime.now() + timedelta(days=2)).isoformat(),
        },
    )
    assert board.status_code == 200
    row = next(e for e in board.json() if e["id"] == event_id)
    assert row["status"] == "pending"
    assert row["prev_value"]["start_at"] == original_start
    assert row["evidence"]["talker"] == "班级群"
    assert "调到下午3点" in row["evidence"]["snippet"]

    # 看板确认入口与钉钉回复等价：API 落定
    confirmed = http.post(f"/api/agenda/{event_id}/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    assert confirmed.json()["start_at"] == _tomorrow(15)


def test_dingtalk_reply_dismiss_rolls_back(env):
    """ADR-0009：回复「忽略 N」→ 恢复原值，不落成新事实。"""
    http = env.http

    http.post(
        "/api/agenda",
        json={"title": "组会", "start_at": _tomorrow(9), "kind": "meeting"},
    )
    report = _post_group_message(
        env.http,
        msg_id="wx_2001",
        content="明天的组会改到下午两点半",
    )
    event_id = report["updated"][0]
    pending = http.get("/api/agenda/pending").json()
    seq = next(e["confirm_seq"] for e in pending if e["id"] == event_id)

    ack = get_dispatcher().handle_reply(f"忽略 {seq}")
    assert ack is not None and "已忽略" in ack

    row = http.get(
        "/api/agenda",
        params={"from": _tomorrow(0), "to": _tomorrow(23, 59)},
    ).json()
    meeting = next(e for e in row if e["title"] == "组会")
    assert meeting["status"] == "confirmed"
    assert meeting["start_at"] == _tomorrow(9), "忽略必须回滚到 prev_value"


def test_reply_confirm_via_short_sequence(env):
    """ADR-0009：回复「确认 N」→ 落定并收到回执（终态+结果）。"""
    http = env.http

    report = _post_group_message(
        env.http,
        msg_id="wx_3001",
        content="明天下午两点开班会，全员参加",
    )
    event_id = report["created"][0]
    seq = next(
        e["confirm_seq"] for e in http.get("/api/agenda/pending").json()
        if e["id"] == event_id
    )

    ack = get_dispatcher().handle_reply(f"确认{seq}")
    assert ack is not None and "已确认" in ack

    row = http.get("/api/agenda").json()
    assert any(e["id"] == event_id and e["status"] == "confirmed" for e in row)


def test_unknown_and_invalid_replies_do_not_crash(env):
    """无效/过期回复：给用法口径，不吞对话、不落库。"""
    get_dispatcher().handle_reply("确认 999")  # 无此编号 → 提示，不抛
    assert get_dispatcher().handle_reply("今天天气怎么样") is None
