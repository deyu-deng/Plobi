"""日程两枚原子的判据（`WP-AGENDA-ATOMS` 第二格，裁 185／裁 175）。

跑法：``bash scripts/run_tests.sh tests/plobi/test_agenda_atoms.py``（唯一入口，禁直调 pytest）。

四件事，全部走**注册表里那条 handler**（就是模型调用时真正经过的那一步）：

1. **形状**：参数面里没有 `action`／`intent`／`mode`／`purpose` 这类选分支的东西（裁 185），
   两条都挂在 `toolsets.py` 的默认束里——与 `pool_read`／`pool_mark` 同一 rung、同一理由。
2. **写下去是真实 events 行**（裁 175）：每条都有自己的 `evt_…` id，不是 `l1:block-N` 那种自造身份。
3. **一条一件、逐条点名**：一批里第 N 条坏了不影响前 N-1 条，也不许吞掉——`errors` 里要说得出为什么。
4. **旧 `mutate_agenda` 那五条 `action` 的等价路径**逐条验：create／update（＝改时改名，就是 move_block）、
   delete（`status=cancelled`）、按 id 勾完成（`completed`）。
   **对照表里还缺的那一条本档不假装覆盖**：作息模板（`add_life_block` 写的是 `routine_templates` 那张表，
   带自己的撞段闸门）——它需要一枚模板原子，没做就不许撤那张 intent 表（判据③）。
"""

from __future__ import annotations

import json

import pytest

from plobi.agenda import store
from tools import agenda_tools  # noqa: F401  — 注册这两枚工具本体
from tools.registry import registry

TOMORROW = "2026-10-21"


@pytest.fixture()
def agenda(tmp_path, monkeypatch):
    """日程库钉到临时文件。真库一个字节不碰（`PLOBI_AGENDA_DB` 在 connect 时才解析，所以顺序无所谓）。"""
    db = tmp_path / "agenda.db"
    monkeypatch.setenv("PLOBI_AGENDA_DB", str(db))
    yield db


def _dispatch(name: str, args: dict) -> dict:
    entry = registry.get_entry(name)
    assert entry is not None, f"{name} 没注册上"
    return json.loads(entry.handler(args))


def _read(name: str = "agenda_read", **filters) -> dict:
    return _dispatch(name, filters)


def _write(*items) -> dict:
    return _dispatch("agenda_write", {"items": list(items)})


def _seed_pending(db, title="明天下午三点开组会", event_id=None) -> str:
    conn = store.connect(db)
    try:
        made = store.create_event(
            conn, title=title, start_at=f"{TOMORROW}T15:00:00", kind="meeting",
            status="pending", source="wechat",  # 消息提议出来的那一条：库里允许的 source 见 store.EVENT_SOURCES
        )
        conn.commit()
        return str(event_id or made.id)
    finally:
        conn.close()


class TestShape:
    def test_no_branch_picking_parameter_on_either_atom(self):
        banned = {"action", "intent", "mode", "purpose", "operation"}
        for tool in agenda_tools._AGENDA_READ_SCHEMA, agenda_tools._AGENDA_WRITE_SCHEMA:
            props = set(tool["parameters"]["properties"])
            assert not (props & banned), f"{tool['name']} 带了选分支的参数：{props & banned}"

    def test_the_write_side_offers_no_operation_menu_only_record_fields(self):
        """写手能表达的只有记录自己的字段＋两种库里已有的状态值——没有动词菜单。"""
        props = agenda_tools._AGENDA_WRITE_SCHEMA["parameters"]["properties"]["items"]["items"]["properties"]
        assert set(props) == {"event_id", "title", "start_at", "end_at", "kind", "location",
                              "notes", "status", "completed"}
        assert props["status"]["enum"] == ["confirmed", "cancelled"]

    def test_both_atoms_are_in_the_bundle_every_role_inherits(self):
        import toolsets

        for name in ("agenda_read", "agenda_write"):
            assert name in toolsets._PLOBI_CORE_TOOLS, f"{name} 没进默认束 = 只有某个角色拿得到"
        assert toolsets.TOOLSETS["agenda"]["tools"] == ["agenda_read", "agenda_write"]

    def test_the_writer_is_classified_as_mutating_by_the_loop_guard(self):
        from agent.tool_guardrails import MUTATING_TOOL_NAMES

        assert "agenda_write" in MUTATING_TOOL_NAMES
        assert "agenda_read" not in MUTATING_TOOL_NAMES


class TestRead:
    def test_an_empty_range_says_empty_instead_of_erroring(self, agenda):
        out = _read(start=TOMORROW, end=TOMORROW)

        assert out["ok"] is True and out["count"] == 0 and out["returned"] == []

    def test_it_returns_the_rows_with_their_ids(self, agenda):
        _seed_pending(agenda)
        _write({"title": "写实验报告", "start_at": f"{TOMORROW}T19:00:00",
                "end_at": f"{TOMORROW}T20:30:00", "kind": "task"})

        out = _read(start=TOMORROW, end=TOMORROW)

        titles = {i["title"] for i in out["returned"]}
        assert {"写实验报告", "明天下午三点开组会"} <= titles
        assert all(str(i["id"]).startswith("evt_") for i in out["returned"])

    def test_a_bare_date_covers_that_whole_day(self, agenda):
        """库里比的是 `start_at <= to`，所以光给一天必须铺到 `23:59:59`——否则当天 00:01 之后全查不到。"""
        _write({"title": "晚自习", "start_at": f"{TOMORROW}T21:00:00"})

        out = _read(start=TOMORROW, end=TOMORROW)

        assert out["count"] == 1
        assert out["range"] == {"start": f"{TOMORROW}T00:00:00", "end": f"{TOMORROW}T23:59:59"}

    def test_the_filters_are_filters(self, agenda):
        _write({"title": "理论力学", "start_at": f"{TOMORROW}T08:00:00", "kind": "class"})
        _write({"title": "实验三报告", "start_at": f"{TOMORROW}T15:50:00", "kind": "ddl"})

        assert [i["title"] for i in _read(start=TOMORROW, end=TOMORROW, kind="class")["returned"]] == ["理论力学"]
        hits = _read(start=TOMORROW, end=TOMORROW, contains="实验三")["returned"]
        assert [i["title"] for i in hits] == ["实验三报告"]
        assert _read(start=TOMORROW, end=TOMORROW, source="manual")["count"] == 2

    def test_pending_and_the_plan_header_come_back_too(self, agenda):
        _seed_pending(agenda)
        conn = store.connect(agenda)
        try:
            store.upsert_daily_plan(conn, for_date=TOMORROW, status="pending",
                                    summary="秘书自己排的", event_count=2, conflict_count=1)
            conn.commit()
        finally:
            conn.close()

        out = _read(start=TOMORROW, end=TOMORROW)

        assert [i["title"] for i in out["pending"]] == ["明天下午三点开组会"]
        header = out["plans"]
        assert [p["for_date"] for p in header] == [TOMORROW]
        assert header[0]["summary"] == "秘书自己排的" and header[0]["conflict_count"] == 1

    def test_an_unreadable_time_is_named_not_ignored(self, agenda):
        out = _read(start="下礼拜")

        assert out["ok"] is False and "读不出来" in out["error"]

    def test_a_broken_store_says_so_instead_of_passing_as_empty(self, agenda, monkeypatch):
        from plobi.agenda import service as service_module

        def boom(*a, **kw):
            raise OSError("database is locked")

        monkeypatch.setattr(service_module.AgendaService, "list_agenda", boom)

        out = _read(start=TOMORROW)

        assert out["ok"] is False and "读日程失败" in out["error"]


class TestWrite:
    def test_a_new_block_lands_as_a_real_events_row(self, agenda):
        out = _write({"title": "焊接作业一", "start_at": f"{TOMORROW}T19:10:00",
                      "end_at": f"{TOMORROW}T20:50:00", "kind": "task",
                      "location": "玉泉教四", "notes": "明日 13:13 截止"})

        assert out["ok"] is True and out["written"] == 1
        row = out["items"][0]
        assert row["action"] == "created"
        assert str(row["id"]).startswith("evt_"), "裁 175：不许是自造身份"
        assert not str(row["id"]).startswith("l1:block")
        assert row["source"] == "manual" and row["status"] == "confirmed"
        assert row["location"] == "玉泉教四"

        # 真读回来，不是只看返回值
        conn = store.connect(agenda)
        try:
            assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        finally:
            conn.close()

    def test_editing_by_id_moves_that_row_and_keeps_the_identity(self, agenda):
        created = _write({"title": "理论力学", "start_at": f"{TOMORROW}T08:00:00",
                          "end_at": f"{TOMORROW}T09:35:00", "kind": "class"})["items"][0]

        moved = _write({"event_id": created["id"], "start_at": f"{TOMORROW}T10:00:00",
                        "end_at": f"{TOMORROW}T11:35:00", "title": "理论力学（换教室）"})

        assert moved["ok"] is True and moved["written"] == 1
        row = moved["items"][0]
        assert row["id"] == created["id"], "改时间不许换身份"
        assert row["start_at"].startswith(f"{TOMORROW}T10:00")
        assert "start_at" in row["changed"] and "title" in row["changed"]

    def test_cancel_a_settled_row_removes_it(self, agenda):
        created = _write({"title": "取消我", "start_at": f"{TOMORROW}T09:00:00"})["items"][0]

        out = _write({"event_id": created["id"], "status": "cancelled"})

        assert out["ok"] is True and out["items"][0]["action"] == "deleted"
        assert _read(start=TOMORROW, end=TOMORROW)["count"] == 0

    def test_cancel_a_pending_row_goes_through_the_dismiss_path(self, agenda):
        event_id = _seed_pending(agenda)

        out = _write({"event_id": event_id, "status": "cancelled"})

        assert out["ok"] is True
        assert out["items"][0]["action"] == "dismissed"
        # 那条本来就是消息提议出来的新事件 ⇒ 回滚就是没有它了
        assert out["items"][0]["still_there"] is False

    def test_accepting_a_pending_row_confirms_it_without_moving_the_time(self, agenda):
        event_id = _seed_pending(agenda)

        out = _write({"event_id": event_id, "status": "confirmed"})

        row = out["items"][0]
        assert row["status"] == "confirmed" and row["start_at"].startswith(f"{TOMORROW}T15:00")

    def test_completion_lives_on_the_event_row_not_on_a_plan_row(self, agenda):
        """裁 148：完成态写在事件上——计划行每晚重排会被 DELETE 再插，写在那儿第二天就又排一遍。"""
        created = _write({"title": "实验三报告", "start_at": f"{TOMORROW}T15:50:00"})["items"][0]

        done = _write({"event_id": created["id"], "completed": True})
        undone = _write({"event_id": created["id"], "completed": False})

        assert done["items"][0]["completed_at"], "承认做完了要留时刻"
        assert undone["items"][0]["completed_at"] is None

    def test_overlapping_time_is_a_fact_not_a_refusal(self, agenda):
        """人令不拒：重叠由面上标红，不是把写挡住的理由。"""
        _write({"title": "理论力学", "start_at": f"{TOMORROW}T08:00:00", "end_at": f"{TOMORROW}T09:35:00"})

        out = _write({"title": "补觉", "start_at": f"{TOMORROW}T08:30:00", "end_at": f"{TOMORROW}T09:00:00"})

        assert out["ok"] is True and out["written"] == 1

    def test_a_batch_names_the_bad_one_and_keeps_the_good_one(self, agenda):
        out = _write(
            {"title": "好的那条", "start_at": f"{TOMORROW}T09:00:00"},
            {"title": "没时间的那条"},
        )

        assert out["written"] == 1 and out["failed"] == 1
        assert out["ok"] is False
        assert out["errors"][0]["index"] == 1
        assert "start_at" in out["errors"][0]["error"]
        assert [i["title"] for i in _read(start=TOMORROW, end=TOMORROW)["returned"]] == ["好的那条"]

    def test_an_unknown_id_is_pointed_back_at_the_reader(self, agenda):
        out = _write({"event_id": "evt_nosuch", "title": "改个名"})

        assert out["ok"] is False and "agenda_read" in out["errors"][0]["error"]

    def test_an_unparseable_status_is_refused_rather_than_guessed(self, agenda):
        created = _write({"title": "别乱猜", "start_at": f"{TOMORROW}T09:00:00"})["items"][0]

        out = _write({"event_id": created["id"], "status": "deleted"})

        assert out["ok"] is False and "confirmed" in out["errors"][0]["error"]

    def test_nothing_comes_back_when_items_is_empty_or_missing(self, agenda):
        assert _write()["ok"] is False
        assert _dispatch("agenda_write", {})["ok"] is False
