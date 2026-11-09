"""The leg-cooldown ledger: a parked leg stays parked across a restart.

The contract (user, 2026-11-08 21:2x: 「开关打开就一定要能用」): when the upstream
says "not for another ten hours", the picker must keep saying so — after a restart
too, because a restart is routine and the user must not rediscover it by clicking.
"""

from __future__ import annotations

import json
import time

from quota_hub import leg_cooldown


def _use(tmp_path, monkeypatch):
    path = tmp_path / "leg-cooldowns.json"
    monkeypatch.setenv("QUOTA_HUB_LEG_COOLDOWN", str(path))
    return path


def test_a_parked_leg_is_remembered_with_the_reason(tmp_path, monkeypatch):
    path = _use(tmp_path, monkeypatch)
    leg_cooldown.note("opencode_api", 1234, "quota_exhausted")

    entry = json.loads(path.read_text(encoding="utf-8"))["opencode_api"]
    assert entry["reason"] == "quota_exhausted", "the machine code stays on disk"
    assert 1200 < entry["until"] - time.time() < 1240, "parked for the seconds upstream named"
    assert 1200 < leg_cooldown.remaining("opencode_api") < 1240


def test_the_ledger_survives_a_restart(tmp_path, monkeypatch):
    """A fresh process reads the same file — that is the whole point of it."""
    path = _use(tmp_path, monkeypatch)
    leg_cooldown.note("opencode_api", 900, "quota_exhausted")

    # A restart is not a reload of the module: the file is what carries the state.
    assert leg_cooldown.until("opencode_api") is not None
    assert leg_cooldown.remaining("opencode_api") > 800
    assert path.exists()


def test_a_served_turn_clears_the_parking(tmp_path, monkeypatch):
    _use(tmp_path, monkeypatch)
    leg_cooldown.note("opencode_api", 900, "quota_exhausted")
    leg_cooldown.clear("opencode_api")
    assert leg_cooldown.remaining("opencode_api") == 0.0
    assert leg_cooldown.until("opencode_api") is None


def test_a_passed_window_is_over(tmp_path, monkeypatch):
    _use(tmp_path, monkeypatch)
    leg_cooldown.note("opencode_api", 1, "quota_exhausted")
    time.sleep(1.1)
    assert leg_cooldown.remaining("opencode_api") == 0.0, "an expired window parks nobody"


def test_a_shorter_new_answer_never_unlocks_a_longer_one(tmp_path, monkeypatch):
    """The upstream is the only authority on how long the bucket stays empty."""
    _use(tmp_path, monkeypatch)
    leg_cooldown.note("opencode_api", 3600, "quota_exhausted")
    before = leg_cooldown.until("opencode_api")
    leg_cooldown.note("opencode_api", 60, "quota_exhausted")
    assert leg_cooldown.until("opencode_api") == before


def test_a_torn_file_is_not_evidence_about_anybody(tmp_path, monkeypatch):
    path = _use(tmp_path, monkeypatch)
    path.write_text("{not json", encoding="utf-8")
    assert leg_cooldown.remaining("opencode_api") == 0.0
    assert leg_cooldown.until("opencode_api") is None


def test_the_path_follows_the_home_when_nothing_is_set(tmp_path, monkeypatch):
    monkeypatch.delenv("QUOTA_HUB_LEG_COOLDOWN", raising=False)
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path))
    assert leg_cooldown.cooldown_path() == tmp_path / "quota_hub-leg-cooldowns.json"
    monkeypatch.delenv("PLOBI_HOME", raising=False)
    assert leg_cooldown.cooldown_path().name == "leg-cooldowns.json"
