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


def test_db_path_resolves_to_shared_root_in_profile_mode(tmp_path, monkeypatch):
    """裁定 42 / WP-PROFILE-SHARED-LAYER（R-096）：profile 活跃时日程库也是 root 那一份。

    「课程分身问『我明天有什么』读到自己的空库」的守位测试；路径同一性断言
    （判据④/③），分身侧那份空库必须被无视。
    """
    root = tmp_path / "plobiroot"
    profile = root / "profiles" / "l2-course-x"
    (profile / "plobi").mkdir(parents=True)
    (root / "plobi").mkdir(parents=True)
    (profile / "plobi" / "agenda.db").write_bytes(b"")

    monkeypatch.setenv("PLOBI_HOME", str(profile))
    monkeypatch.delenv("PLOBI_AGENDA_DB", raising=False)

    resolved = store.agenda_db_path()
    assert resolved == root / "plobi" / "agenda.db"
    assert resolved != profile / "plobi" / "agenda.db"


def test_db_path_has_no_hardcoded_drive(monkeypatch):
    monkeypatch.delenv("PLOBI_AGENDA_DB", raising=False)
    source = (store.__file__ or "")
    assert source
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    assert "D:/Mind" not in text and "D:\\\\" not in text
