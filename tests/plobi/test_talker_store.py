"""TalkerStore: the blacklist-mode admission ledger (A7, ADR-0010 revision)."""

from __future__ import annotations

import pytest

from plobi.collectors.chatlog.state import TalkerStore


@pytest.fixture()
def store(tmp_path):
    return TalkerStore(tmp_path / "state.db")


def test_unknown_talker_has_no_status(store):
    assert store.status_of("新群") is None
    assert store.pending() == set()


def test_status_lifecycle(store):
    store.mark_known("班级群")
    store.mark_excluded("广告群")
    store.mark_pending("新群")

    assert store.status_of("班级群") == "known"
    assert store.status_of("广告群") == "excluded"
    assert store.status_of("新群") == "pending"

    assert store.known() == {"班级群"}
    assert store.excluded() == {"广告群"}
    assert store.pending() == {"新群"}


def test_set_status_overwrites(store):
    store.mark_known("班级群")
    store.mark_excluded("班级群")

    assert store.status_of("班级群") == "excluded"
    assert store.known() == set()


def test_set_status_rejects_unknown_status(store):
    with pytest.raises(ValueError):
        store.set_status("班级群", "maybe")


def test_review_gate_flag(store):
    assert store.review_done() is False
    store.set_review_done(True)
    assert store.review_done() is True
    store.set_review_done(False)
    assert store.review_done() is False


def test_persists_across_reopen(tmp_path):
    path = tmp_path / "state.db"
    first = TalkerStore(path)
    first.mark_known("班级群")
    first.set_review_done(True)

    reopened = TalkerStore(path)
    assert reopened.status_of("班级群") == "known"
    assert reopened.review_done() is True


def test_list_returns_all_rows(store):
    store.mark_known("班级群")
    store.mark_excluded("广告群")
    store.mark_pending("新群")

    rows = store.list()
    by_talker = {row["talker"]: row["status"] for row in rows}
    assert by_talker == {"班级群": "known", "广告群": "excluded", "新群": "pending"}
    # Rows carry the bookkeeping timestamps the board can display.
    assert all(row["first_seen_at"] and row["updated_at"] for row in rows)


def test_known_excluded_are_isolated_from_seen(tmp_path):
    """Talker status and the dedupe ledger live side by side, independently."""
    from plobi.collectors.chatlog.state import SeenStore

    path = tmp_path / "state.db"
    talkers = TalkerStore(path)
    seen = SeenStore(path)

    talkers.mark_known("班级群")
    assert seen.already_seen("m1") is False
    seen.mark_seen("m1")
    assert talkers.status_of("班级群") == "known"
