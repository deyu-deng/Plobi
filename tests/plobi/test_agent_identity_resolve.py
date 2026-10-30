"""身份解析只有一处：profile 家名 / 名册 name → 名册 name；认不出就是没绑定。

用户 2026-10-30 那刀的接线口：`sessions.agent_id` 全空是因为"家名→名册"那一跳在 profile
退休后断了。这条钉三点：家名能翻、名册名原样过、认不出必须回空串（**不许猜**——
猜错等于把 A 的记忆写到 B 名下）。
"""

from __future__ import annotations

from types import SimpleNamespace

import plobi.agents.registry as R

from plobi.mind import identity as I


def _fake(monkeypatch):
    rows = [
        SimpleNamespace(name="l2-course-theomech-a", profile="l2-course-theomech-a"),
        SimpleNamespace(name="Aura", profile=""),
    ]
    monkeypatch.setattr(R, "load_registry", lambda *a, **k: SimpleNamespace(entries=lambda: rows))
    monkeypatch.setattr(
        R,
        "entry_for_profile",
        lambda value: next((r for r in rows if r.profile == value), None),
    )


def test_profile_home_roster_name_and_unknown(monkeypatch):
    _fake(monkeypatch)

    assert I.resolve_agent_name("l2-course-theomech-a") == "l2-course-theomech-a"
    assert I.resolve_agent_name("Aura") == "Aura"
    assert I.resolve_agent_name("AURA") == "", "大小写不同不是同一格：不许猜"
    assert I.resolve_agent_name("") == ""
    assert I.resolve_agent_name("完全不存在的名字") == ""
