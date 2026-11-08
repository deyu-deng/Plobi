"""WP-L3-TICKET-MANDATE：人优先槽位（Super-agent「永远留一个槽给人」）。"""

from __future__ import annotations

import pytest

from plobi.delegation import config, guard as guard_mod
from plobi.delegation.guard import DelegationGuard


@pytest.fixture(autouse=True)
def _reset_default_guard():
    guard_mod.set_guard(None)
    yield
    guard_mod.set_guard(None)


def test_default_zero_reservation_keeps_old_behaviour():
    g = DelegationGuard(cap=6, queue_timeout=0)
    for i in range(6):
        assert g.acquire(session_id=f"s{i}").granted is True
    seventh = g.acquire(session_id="s7")
    assert seventh.granted is False
    assert seventh.reason == "concurrency-cap"
    assert g.stats()["human_reserved"] == 0
    assert g.stats()["automatic_cap"] == 6


def test_human_slot_is_out_of_reach_for_automatic_work():
    g = DelegationGuard(cap=6, queue_timeout=0, human_reserved=1)
    assert g.human_reserved == 1
    assert g.automatic_cap == 5
    assert g.stats()["human_reserved"] == 1

    for i in range(5):
        admission = g.acquire(session_id=f"auto{i}")
        assert admission.granted is True
        assert admission.cap == 5

    blocked = g.acquire(session_id="auto6")
    assert blocked.granted is False
    assert blocked.reason == "concurrency-cap"

    human = g.acquire(session_id="human", for_human=True)
    assert human.granted is True
    assert human.cap == 6
    assert g.active == 6


def test_reserved_is_clamped_and_tracks_cap_change():
    g = DelegationGuard(cap=2, queue_timeout=0, human_reserved=9)
    assert g.human_reserved == 1  # 至少留一个槽给自动派工
    assert g.automatic_cap == 1
    g.set_cap(6)
    assert g.human_reserved == 1
    assert g.automatic_cap == 5


def test_get_guard_reads_human_reserved_from_config(monkeypatch):
    fake = dict(config.DEFAULTS)
    fake["human_reserved_slots"] = 2
    monkeypatch.setattr(config, "load", lambda: fake)
    g = guard_mod.get_guard()
    assert g.cap == fake["global_max_children"]
    assert g.human_reserved == 2
    assert g.automatic_cap == fake["global_max_children"] - 2
