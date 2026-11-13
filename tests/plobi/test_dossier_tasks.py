"""档案的每日任务清单（``tasks.json``）——读、勾选、原子写回。

钉住四件事：坏文件不许糊成空表、勾选真的落盘、落盘是原子的、端点层先翻译名字。
"""

from __future__ import annotations

import asyncio
import json
import textwrap
from pathlib import Path

import pytest

from plobi.mind import dossier_tasks as dt
from plobi.mind import surface_api

ROSTER = textwrap.dedent(
    """
    version: 1
    agents:
      Aura:
        role: l2_project
        category: projects
        mind_subtree: Dossiers/projects/Aura
      secretary:
        role: l1_secretary
        display_name: 总秘书
        mind_subtree: Dossiers/butler/secretary
    """
).lstrip()

TASKS = {
    "version": 1,
    "days": [
        {
            "date": "2026-11-08",
            "items": [
                {
                    "id": "2026-11-08#1",
                    "title": "WidgetViewModel.ets 抽象",
                    "done": False,
                    "doneBy": "",
                    "updatedAt": "2026-11-08T09:12:00",
                },
                {
                    "id": "2026-11-08#2",
                    "title": "接线",
                    "done": True,
                    "doneBy": "agent",
                    "updatedAt": "2026-11-08T10:00:00",
                },
            ],
        },
        {
            "date": "2026-11-09",
            "items": [
                {
                    "id": "2026-11-09#1",
                    "title": "收尾",
                    "done": False,
                    "doneBy": "",
                    "updatedAt": "2026-11-09T08:00:00",
                }
            ],
        },
    ],
}


@pytest.fixture()
def live(monkeypatch, tmp_path):
    """一套能读档案的现场：脑根、人读面根、名册都在临时目录里。"""
    mind = tmp_path / "mind"
    surface = tmp_path / "surface"
    mind.mkdir(parents=True)
    (surface / "dossiers" / "projects" / "Aura").mkdir(parents=True)
    (surface / "dossiers" / "butler" / "secretary").mkdir(parents=True)
    roster = tmp_path / "projects.yaml"
    roster.write_text(ROSTER, encoding="utf-8")
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("HUMAN_ROOT", str(surface))
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(roster))
    return tmp_path, surface


def _write_tasks(surface: Path, name: str, payload) -> Path:
    """把一份 tasks.json 放进某个 Agent 的档案目录。"""
    if name == "Aura":
        home = surface / "dossiers" / "projects" / "Aura"
    else:
        home = surface / "dossiers" / "butler" / "secretary"
    home.mkdir(parents=True, exist_ok=True)
    path = home / "tasks.json"
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _body(response):
    """路由函数有时回 dict、有时回 JSONResponse——都摊成同一个 dict。"""
    return response if isinstance(response, dict) else json.loads(response.body)


# ── 读：坏文件不许糊成「一个都没有」 ──────────────────────────────────────────


def test_missing_file_is_empty_and_absent_without_raising(live):
    _, surface = live
    path = surface / "dossiers" / "projects" / "Aura" / "tasks.json"

    data = dt.read_tasks(path)

    assert data.exists is False
    assert data.error is None
    assert data.days == []


def test_broken_json_reports_an_error_instead_of_an_empty_list(live):
    _, surface = live
    path = _write_tasks(surface, "Aura", "{ this is not json")

    data = dt.read_tasks(path)

    assert data.exists is True
    assert data.error  # 读不到
    assert data.days == []


def test_json_that_is_not_an_object_is_also_an_error(live):
    _, surface = live
    path = _write_tasks(surface, "Aura", "[1, 2, 3]")

    data = dt.read_tasks(path)

    assert data.error


def test_normal_file_groups_by_date_with_full_item_fields(live):
    _, surface = live
    path = _write_tasks(surface, "Aura", TASKS)

    data = dt.read_tasks(path)

    assert data.exists is True and data.error is None
    assert [day.date for day in data.days] == ["2026-11-08", "2026-11-09"]
    first = data.days[0].items[0]
    assert set(first.to_dict()) == {"id", "title", "done", "doneBy", "updatedAt"}
    assert first.done is False
    assert data.days[0].items[1].done is True


def test_done_is_normalized_to_bool(live):
    _, surface = live
    path = _write_tasks(
        surface,
        "Aura",
        {
            "version": 1,
            "days": [
                {
                    "date": "2026-11-08",
                    "items": [
                        {"id": "a", "title": "写成了字符串", "done": "true"},
                        {"id": "b", "title": "没写"},
                    ],
                }
            ],
        },
    )

    data = dt.read_tasks(path)

    assert data.days[0].items[0].done is True
    assert data.days[0].items[1].done is False


def test_days_and_items_missing_fields_are_skipped_not_fatal(live):
    _, surface = live
    path = _write_tasks(
        surface,
        "Aura",
        {
            "version": 1,
            "days": [
                {"items": [{"id": "x", "title": "这天没有日期"}]},
                {"date": "不是日期", "items": []},
                {
                    "date": "2026-11-08",
                    "items": [
                        {"title": "没有 id"},
                        {"id": "ok", "title": "好的那条"},
                        "不是对象",
                    ],
                },
            ],
        },
    )

    data = dt.read_tasks(path)

    assert [day.date for day in data.days] == ["2026-11-08"]
    assert [item.id for item in data.days[0].items] == ["ok"]


# ── 勾选：改的是盘上那一份 ────────────────────────────────────────────────────


def test_toggle_marks_done_and_the_file_on_disk_says_so(live):
    _, surface = live
    path = _write_tasks(surface, "Aura", TASKS)

    dt.toggle_task(path, "2026-11-08", "2026-11-08#1", True)

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    item = on_disk["days"][0]["items"][0]
    assert item["done"] is True


def test_toggle_back_marks_not_done_on_disk(live):
    _, surface = live
    path = _write_tasks(surface, "Aura", TASKS)

    dt.toggle_task(path, "2026-11-08", "2026-11-08#2", False)

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    item = on_disk["days"][0]["items"][1]
    assert item["done"] is False


def test_toggle_writes_done_by_and_updated_at(live):
    _, surface = live
    path = _write_tasks(surface, "Aura", TASKS)

    dt.toggle_task(path, "2026-11-08", "2026-11-08#1", True)

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    item = on_disk["days"][0]["items"][0]
    assert item["doneBy"] == "user"
    assert item["updatedAt"]


def test_toggle_only_touches_that_one_item(live):
    _, surface = live
    path = _write_tasks(surface, "Aura", TASKS)

    dt.toggle_task(path, "2026-11-08", "2026-11-08#1", True)

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    other = on_disk["days"][0]["items"][1]
    assert other["done"] is True and other["doneBy"] == "agent"
    assert on_disk["days"][1]["items"][0]["done"] is False


def test_unknown_keys_in_the_file_survive_a_toggle(live):
    _, surface = live
    path = _write_tasks(
        surface,
        "Aura",
        {
            "version": 1,
            "generatedBy": "surface_write",
            "days": [
                {
                    "date": "2026-11-08",
                    "note": "这一天是赶出来的",
                    "items": [
                        {
                            "id": "2026-11-08#1",
                            "title": "WidgetViewModel.ets 抽象",
                            "done": False,
                            "owner": "Aura",
                        }
                    ],
                }
            ],
        },
    )

    dt.toggle_task(path, "2026-11-08", "2026-11-08#1", True)

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["generatedBy"] == "surface_write"
    assert on_disk["version"] == 1
    assert on_disk["days"][0]["note"] == "这一天是赶出来的"
    assert on_disk["days"][0]["items"][0]["owner"] == "Aura"


def test_the_write_leaves_no_temporary_file_behind(live):
    _, surface = live
    path = _write_tasks(surface, "Aura", TASKS)

    dt.toggle_task(path, "2026-11-08", "2026-11-08#1", True)

    home = path.parent
    assert sorted(p.name for p in home.iterdir()) == ["tasks.json"]


def test_toggling_an_unknown_item_raises_instead_of_writing(live):
    _, surface = live
    path = _write_tasks(surface, "Aura", TASKS)

    with pytest.raises(dt.TaskNotFound):
        dt.toggle_task(path, "2026-11-08", "没有这一条", True)

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["days"][0]["items"][0]["done"] is False


def test_toggling_without_a_file_raises_file_missing(live):
    _, surface = live
    path = surface / "dossiers" / "projects" / "Aura" / "tasks.json"

    with pytest.raises(dt.TasksFileMissing):
        dt.toggle_task(path, "2026-11-08", "2026-11-08#1", True)


# ── 档案在哪儿：走名册那一格，不自己拼盘符 ────────────────────────────────────


def test_home_comes_from_the_roster_row(live):
    _, surface = live

    home = dt.find_home("Aura")

    assert home == surface / "dossiers" / "projects" / "Aura"


def test_unknown_agent_has_no_home(live):
    with pytest.raises(dt.AgentNotRegistered):
        dt.find_home("不存在")


def test_an_agent_without_a_dossier_has_no_home(live, monkeypatch):
    roster = Path(live[0]) / "projects.yaml"
    roster.write_text(
        "version: 1\nagents:\n  无档案:\n    role: l2_project\n    category: projects\n",
        encoding="utf-8",
    )

    assert dt.find_home("无档案") is None


# ── 端点层：作用域哨兵先翻译，再查 ────────────────────────────────────────────


def test_get_tasks_translates_the_l1_scope_before_looking_up(live):
    _, surface = live
    _write_tasks(surface, "secretary", TASKS)

    response = asyncio.run(surface_api.get_surface_agent_tasks("l1"))
    body = _body(response)

    assert body["ok"] is True
    assert "agent 'l1' is not registered" not in json.dumps(body, ensure_ascii=False)
    assert [day["date"] for day in body["data"]["days"]] == ["2026-11-08", "2026-11-09"]


def test_get_tasks_of_an_unknown_agent_is_404(live):
    response = asyncio.run(surface_api.get_surface_agent_tasks("不存在"))
    body = _body(response)

    assert body["ok"] is False
    assert "not registered" in body["error"]


def test_get_tasks_reports_a_broken_file_as_an_error(live):
    _, surface = live
    _write_tasks(surface, "Aura", "{ broken")

    response = asyncio.run(surface_api.get_surface_agent_tasks("Aura"))
    body = _body(response)

    assert body["ok"] is True
    assert body["data"]["error"]
    assert body["data"]["days"] == []


def test_post_toggle_of_an_unknown_item_is_not_ok(live):
    _, surface = live
    _write_tasks(surface, "Aura", TASKS)

    response = asyncio.run(
        surface_api.toggle_surface_agent_task(
            "Aura", {"date": "2026-11-08", "id": "没有这一条", "done": True}
        )
    )
    body = _body(response)

    assert body["ok"] is False
    assert body["error"]


def test_post_toggle_writes_the_file_and_returns_the_same_shape(live):
    _, surface = live
    path = _write_tasks(surface, "Aura", TASKS)

    response = asyncio.run(
        surface_api.toggle_surface_agent_task(
            "Aura", {"date": "2026-11-08", "id": "2026-11-08#1", "done": True}
        )
    )
    body = _body(response)

    assert body["ok"] is True
    assert body["data"]["exists"] is True
    assert body["data"]["error"] is None
    assert body["data"]["days"][0]["items"][0]["done"] is True
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["days"][0]["items"][0]["done"] is True


def test_post_toggle_of_the_l1_scope_also_translates(live):
    _, surface = live
    _write_tasks(surface, "secretary", TASKS)

    response = asyncio.run(
        surface_api.toggle_surface_agent_task(
            "l1", {"date": "2026-11-08", "id": "2026-11-08#1", "done": True}
        )
    )
    body = _body(response)

    assert body["ok"] is True
    assert body["data"]["days"][0]["items"][0]["done"] is True


def test_post_toggle_rejects_a_body_without_a_target_state(live):
    _, surface = live
    _write_tasks(surface, "Aura", TASKS)

    response = asyncio.run(
        surface_api.toggle_surface_agent_task(
            "Aura", {"date": "2026-11-08", "id": "2026-11-08#1"}
        )
    )
    body = _body(response)

    assert body["ok"] is False
    assert "done" in body["error"]


def test_post_toggle_without_a_file_is_not_ok(live):
    response = asyncio.run(
        surface_api.toggle_surface_agent_task(
            "Aura", {"date": "2026-11-08", "id": "2026-11-08#1", "done": True}
        )
    )
    body = _body(response)

    assert body["ok"] is False
    assert body["error"]


def test_the_routes_reach_these_handlers_over_real_http(live):
    """``/surface/agents/{name}`` 不许把 ``…/{name}/tasks`` 吃掉——真 ASGI 才测得出。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from plobi.mind.surface_api import router

    _, surface = live
    path = _write_tasks(surface, "Aura", TASKS)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    client = TestClient(app)

    read = client.get("/api/surface/agents/Aura/tasks")
    assert read.status_code == 200
    assert read.json()["data"]["days"][0]["items"][0]["id"] == "2026-11-08#1"

    written = client.post(
        "/api/surface/agents/Aura/tasks/toggle",
        json={"date": "2026-11-08", "id": "2026-11-08#1", "done": True},
    )
    assert written.status_code == 200
    assert written.json()["ok"] is True
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["days"][0]["items"][0]["done"] is True
