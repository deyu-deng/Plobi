"""Store-level invariants: schema idempotency, validation, confirm sequences."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from plobi.agenda import store
from plobi.agenda.store import AgendaValidationError


@pytest.fixture()
def conn(tmp_path):
    connection = store.connect(tmp_path / "agenda.db")
    yield connection
    connection.close()


def test_init_db_is_idempotent(tmp_path):
    path = tmp_path / "agenda.db"
    first = store.connect(path)
    store.init_db(first)
    store.init_db(first)
    store.create_event(first, title="t", start_at="2026-08-25T09:00:00")
    store.init_db(first)
    assert len(store.list_events(first)) == 1
    first.close()


def test_create_and_get_roundtrip(conn):
    created = store.create_event(
        conn,
        title="组会",
        start_at="2026-08-25T14:00:00",
        end_at="2026-08-25T15:00:00",
        kind="meeting",
        status="pending",
        source="wechat",
        evidence={"msg_id": "m1", "snippet": "组会改到两点"},
    )
    fetched = store.get_event(conn, created.id)
    assert fetched is not None
    assert fetched.title == "组会"
    assert fetched.evidence == {"msg_id": "m1", "snippet": "组会改到两点"}
    assert fetched.id.startswith("evt_")


def test_rejects_bad_enum_and_empty_title(conn):
    with pytest.raises(AgendaValidationError):
        store.create_event(conn, title="x", start_at="2026-08-25T09:00:00", kind="nope")
    with pytest.raises(AgendaValidationError):
        store.create_event(conn, title="  ", start_at="2026-08-25T09:00:00")
    with pytest.raises(AgendaValidationError):
        store.create_event(conn, title="x", start_at="not-a-date")


def test_rejects_end_before_start(conn):
    with pytest.raises(AgendaValidationError):
        store.create_event(
            conn,
            title="x",
            start_at="2026-08-25T10:00:00",
            end_at="2026-08-25T09:00:00",
        )


def test_list_filters_by_range_and_hides_cancelled(conn):
    store.create_event(conn, title="today", start_at="2026-08-25T09:00:00")
    store.create_event(conn, title="tomorrow", start_at="2026-08-26T09:00:00")
    store.create_event(conn, title="later", start_at="2026-09-10T09:00:00")
    dropped = store.create_event(conn, title="gone", start_at="2026-08-25T10:00:00")
    store.update_event(conn, dropped.id, status="cancelled")

    window = store.list_events(
        conn, start_from="2026-08-25T00:00:00", start_to="2026-08-26T23:59:59"
    )
    assert [e.title for e in window] == ["today", "tomorrow"]

    with_cancelled = store.list_events(
        conn,
        start_from="2026-08-25T00:00:00",
        start_to="2026-08-26T23:59:59",
        include_cancelled=True,
    )
    assert "gone" in [e.title for e in with_cancelled]


def test_list_is_ordered_by_start(conn):
    store.create_event(conn, title="late", start_at="2026-08-25T18:00:00")
    store.create_event(conn, title="early", start_at="2026-08-25T08:00:00")
    assert [e.title for e in store.list_events(conn)] == ["early", "late"]


def test_update_rejects_unknown_field(conn):
    event = store.create_event(conn, title="x", start_at="2026-08-25T09:00:00")
    with pytest.raises(AgendaValidationError):
        store.update_event(conn, event.id, nonsense=1)


def test_update_missing_event_returns_none(conn):
    assert store.update_event(conn, "evt_missing", title="x") is None


def test_delete_reports_whether_row_existed(conn):
    event = store.create_event(conn, title="x", start_at="2026-08-25T09:00:00")
    assert store.delete_event(conn, event.id) is True
    assert store.delete_event(conn, event.id) is False


def test_confirm_seq_increments_and_finds_pending(conn):
    stamp = store.now_iso()
    first = store.next_confirm_seq(conn)
    store.create_event(
        conn,
        title="a",
        start_at="2026-08-25T09:00:00",
        status="pending",
        source="wechat",
        confirm_seq=first,
        confirm_seq_at=stamp,
    )
    second = store.next_confirm_seq(conn)
    assert second == first + 1

    found = store.find_by_confirm_seq(conn, first)
    assert found is not None and found.title == "a"


def test_confirm_seq_ttl(conn):
    fresh = store.Event(
        id="evt_x", title="a", start_at="2026-08-25T09:00:00",
        confirm_seq=1, confirm_seq_at=store.now_iso(),
    )
    assert store.confirm_seq_is_live(fresh) is True

    stale_at = (datetime.now() - timedelta(hours=25)).replace(microsecond=0).isoformat()
    stale = store.Event(
        id="evt_y", title="a", start_at="2026-08-25T09:00:00",
        confirm_seq=2, confirm_seq_at=stale_at,
    )
    assert store.confirm_seq_is_live(stale) is False

    never = store.Event(id="evt_z", title="a", start_at="2026-08-25T09:00:00")
    assert store.confirm_seq_is_live(never) is False


def test_db_path_honours_env_override(tmp_path, monkeypatch):
    target = tmp_path / "custom" / "agenda.db"
    monkeypatch.setenv("PLOBI_AGENDA_DB", str(target))
    assert store.agenda_db_path() == target


def test_db_path_has_no_hardcoded_drive(monkeypatch):
    monkeypatch.delenv("PLOBI_AGENDA_DB", raising=False)
    source = (store.__file__ or "")
    assert source
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    assert "D:/Mind" not in text and "D:\\\\" not in text


def test_daily_plan_roundtrip_and_required_evidence(conn):
    plan = store.upsert_daily_plan(
        conn,
        for_date="2026-09-07",
        status="pending",
        summary="明天两件事",
        event_count=1,
        conflict_count=0,
        evidence=None,
    )
    assert plan.id == "plan_2026-09-07"
    items = store.replace_plan_items(
        conn,
        plan.id,
        [
            {
                "event_id": "evt_a",
                "title": "组会",
                "start_at": "2026-09-07T10:00:00",
                "kind": "meeting",
                "evidence": {"kind": "event", "event_id": "evt_a"},
            }
        ],
    )
    assert len(items) == 1
    assert items[0].evidence["event_id"] == "evt_a"
    fetched = store.get_daily_plan(conn, "2026-09-07")
    assert fetched is not None and fetched.summary == "明天两件事"

    with pytest.raises(store.AgendaValidationError):
        store.replace_plan_items(
            conn,
            plan.id,
            [{"event_id": "evt_b", "title": "无证据", "start_at": "2026-09-07T11:00:00"}],
        )


def test_confirm_seq_shared_with_plans(conn):
    stamp = store.now_iso()
    first = store.next_confirm_seq(conn)
    store.create_event(
        conn,
        title="a",
        start_at="2026-09-07T09:00:00",
        status="pending",
        confirm_seq=first,
        confirm_seq_at=stamp,
    )
    second = store.next_confirm_seq(conn)
    store.upsert_daily_plan(
        conn,
        for_date="2026-09-07",
        status="pending",
        summary="空",
        confirm_seq=second,
        confirm_seq_at=stamp,
    )
    third = store.next_confirm_seq(conn)
    assert third == second + 1
    found = store.find_plan_by_confirm_seq(conn, second)
    assert found is not None and found.for_date == "2026-09-07"


# --------------------------------------------------------------------------- #
# C4 routine template CRUD + validation
# --------------------------------------------------------------------------- #


def test_routine_upsert_roundtrip(conn):
    tpl = store.upsert_routine_template(
        conn, template_id="rt-1", title="晨跑",
        start_time="06:30", end_time="07:10", weekdays=[0, 2, 4], enabled=True,
    )
    assert tpl.id == "rt-1"
    assert tpl.kind == "routine"
    assert tpl.weekdays == (0, 2, 4)
    assert tpl.enabled is True
    again = store.get_routine_template(conn, "rt-1")
    assert again == tpl


def test_routine_upsert_generates_id_and_defaults_disabled(conn):
    tpl = store.upsert_routine_template(
        conn, title="阅读", start_time="21:00", end_time="22:00"
    )
    assert tpl.id
    assert tpl.enabled is False
    assert tpl.weekdays == ()


def test_routine_rejects_bad_time_and_weekday(conn):
    with pytest.raises(store.AgendaValidationError):
        store.upsert_routine_template(conn, title="x", start_time="24:00", end_time="25:00")
    with pytest.raises(store.AgendaValidationError):
        store.upsert_routine_template(conn, title="x", start_time="8:00", end_time="9:00")
    with pytest.raises(store.AgendaValidationError):
        store.upsert_routine_template(conn, title="x", start_time="08:00", end_time="08:00")
    with pytest.raises(store.AgendaValidationError):
        store.upsert_routine_template(
            conn, title="x", start_time="08:00", end_time="09:00", weekdays=[7]
        )
    with pytest.raises(store.AgendaValidationError):
        store.upsert_routine_template(conn, title="", start_time="08:00", end_time="09:00")


def test_routine_list_delete_and_enabled_only(conn):
    store.upsert_routine_template(conn, template_id="a", title="A", start_time="08:00", end_time="09:00", enabled=True)
    store.upsert_routine_template(conn, template_id="b", title="B", start_time="07:00", end_time="07:30")
    all_ids = [t.id for t in store.list_routine_templates(conn)]
    assert all_ids.index("b") < all_ids.index("a")  # by start_time
    assert [t.id for t in store.list_routine_templates(conn, enabled_only=True)] == ["a"]
    assert store.delete_routine_template(conn, "a") is True
    assert store.delete_routine_template(conn, "nope") is False
    assert store.get_routine_template(conn, "a") is None


def test_routine_seeds_are_idempotent_and_editable(conn):
    before = store.get_routine_template(conn, "seed-lunch")
    store.upsert_routine_template(  # user edits the seed
        conn, template_id="seed-lunch", title="午饭",
        start_time="12:30", end_time="13:10", weekdays=[0, 1, 2, 3, 4], enabled=True,
    )
    # simulate a fresh connection (init runs once per path; force re-check)
    assert store.get_routine_template(conn, "seed-lunch").title == "午饭"
    assert store.get_routine_template(conn, "seed-lunch").enabled is True
    assert before.title == "午餐"  # dataclass snapshot unchanged
