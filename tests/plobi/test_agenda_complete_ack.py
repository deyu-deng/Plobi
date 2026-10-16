"""WP-PLAN-COMPLETION-ACK 判据③的 HTTP 契约：面上那一下承认动作。

跑法：`bash scripts/run_tests.sh tests/plobi/test_agenda_complete_ack.py -q`（唯一入口，不传 `--basetemp`）。
钉的是关系，不是读数：承认⇒那一格有值、撤销⇒清回 None 而不是删行；承认这件事**不许**
被 M2 那把「改动率」尺算成一次人工改动（它数的是 confirm/dismiss/manual_*，见 `stats.py`）；
读侧一个字不许动——做完的行还在面上，只是不再排第二次。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plobi.agenda import router as agenda_router
from plobi.agenda import service as agenda_service
from plobi.agenda.service import AgendaService
from plobi.agenda.stats import change_report
from plobi.agenda.store import connect, list_actions


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(agenda_service, "_DEFAULT", AgendaService(tmp_path / "agenda.db"))
    app = FastAPI()
    app.include_router(agenda_router.router, prefix="/api/agenda")
    with TestClient(app) as test_client:
        yield test_client


def _confirmed_message_event(client):
    """采进来的（非 manual）一条并确认掉——改动率那把尺只数非 manual 的台账。"""
    result = agenda_service.get_service().ingest_candidate(
        title="高等数学期中作业",
        start_at="2026-08-25T14:00:00",
        kind="task",
        evidence={"msg_id": "m1", "snippet": "周五交作业"},
    )
    client.post(f"/api/agenda/{result.event.id}/confirm")

    return result.event.id, result.event.source


def test_ack_marks_the_event_and_undo_clears_it_without_becoming_a_change(client, tmp_path):
    event_id, source = _confirmed_message_event(client)

    acked = client.post(f"/api/agenda/{event_id}/complete")
    assert acked.status_code == 200
    assert acked.json()["id"] == event_id
    assert acked.json()["completed_at"], "承认完成必须把那一格写成时刻"

    undone = client.post(f"/api/agenda/{event_id}/complete", json={"completed": False})
    assert undone.status_code == 200
    assert undone.json()["completed_at"] is None, "撤销是清回 None，不是删行"

    svc = AgendaService(tmp_path / "agenda.db")
    conn = connect(svc.db_path)
    try:
        logged = [(row["action"], row["event_source"])
                  for row in list_actions(conn) if row["event_id"] == event_id]
    finally:
        conn.close()
    assert [a for a, _ in logged] == ["uncomplete", "complete", "confirm"], "台账是 newest-first"
    assert {s for _, s in logged} == {source}

    report = change_report(svc, days=7)
    assert report["accepted"] == 1 and report["overrides"] == 0
    assert report["total"] == 1, "承认完成不该进改动率的分母——否则 M2 那把尺被这把勾污染"


def test_completed_row_is_still_readable_and_an_unknown_id_is_not_invented(client):
    event_id, _ = _confirmed_message_event(client)
    client.post(f"/api/agenda/{event_id}/complete")

    listed = client.get("/api/agenda", params={"from": "2026-08-25T00:00:00", "to": "2026-08-25T23:59:59"})
    assert listed.status_code == 200
    rows = {e["id"]: e for e in listed.json()}
    assert event_id in rows, "「做完就消失」不是本刀要的效果：读侧一个字没动"
    assert rows[event_id]["completed_at"]

    day = client.get("/api/agenda/day", params={"date": "2026-08-25"})
    assert day.status_code == 200
    # /day 走的是 `_store.connect()`（无参=默认家目录那份库），不吃挂载的 service.db_path——
    # 所以在这个把服务指到临时库的夹具里它必然是空的。这是**既有洞**，不是本刀造成的，
    # 已按格上报；这里不拿它当断言，避免把别人的形状钉成我的契约。

    missing = client.post("/api/agenda/evt_nope/complete")
    assert missing.status_code == 404, "id 不存在不许静默成功"

    # 界面上那颗勾要打在事件上，所以计划那一行必须把 `event_id` 带出去（判据③的前置）。
    # 直接钉序列化函数：走 `/day` 会撞上"路由开的是默认家目录那份库"那个既有洞，不拿来当断言。
    from plobi.agenda.planning import _plan_item_dict
    from plobi.agenda.store import PlanItem

    row = _plan_item_dict(PlanItem(id="pi_1", plan_id="dp_1", event_id=event_id,
                                   title="期中考", start_at="2026-08-25T14:00:00"))
    assert row["event_id"] == event_id, "计划行不带 event_id ⇒ 界面无处承认完成，只能再排一遍"
