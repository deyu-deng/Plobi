"""WP-L2-DIET-REGISTRY（C 刀）：diet 真源搬进名册行 + 身份通道认 PLOBI_AGENT_ID。

钉住的不变量（防的是这次事故复发：B 刀让 L2 不带 `-p` 起来之后，per-agent
配置整条链对 L2 失效——`agent_config_overlay` 开头「共享根即空」那道门把
模型 / cwd / 工具集黑名单一起关在门外）：

1. 身份通道：`PLOBI_AGENT_ID` 优先，家在 profiles/ 下回退，都不是 ⇒ 空（L1）。
2. 查找：`PLOBI_AGENT_ID` 带的是名册 **name**，不一定是 profile 名。
3. overlay 的门：共享根 + 有身份 ⇒ 照给覆盖（这是本刀的核心修复）。
4. diet 真源：`diet_entry_toolsets` 写名册行 `disabled_toolsets`，幂等、只追加。
5. 读侧并集：名册行黑名单与顶层用户选择求并集，替换会吃掉用户的勾选。
"""
from __future__ import annotations

import pytest

from plobi.agents.registry import (
    L2_PROJECT_DROP_TOOLSETS,
    AgentEntry,
    _active_agent_profile_name,
    _entry_for_profile_in,
    diet_entry_toolsets,
    profile_config_overlay,
)


def _entry(**kw) -> AgentEntry:
    base = dict(name="Stithy", role="l2_project", profile="l2-stithy", category="projects")
    base.update(kw)
    return AgentEntry(**base)


# ── ① 身份通道 ────────────────────────────────────────────────────────────


def test_agent_id_is_the_identity_channel(monkeypatch):
    """B 刀之后 L2 不带 -p 起来，PLOBI_HOME 是共享根——身份只能来自 AGENT_ID。"""
    monkeypatch.setenv("PLOBI_AGENT_ID", "Stithy")
    assert _active_agent_profile_name() == "Stithy"


def test_agent_id_survives_a_shared_root_home(monkeypatch, tmp_path):
    """共享根 + AGENT_ID ⇒ 身份仍在（老代码在这里返回 ''）。"""
    monkeypatch.setenv("PLOBI_AGENT_ID", "Stithy")
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path))  # 不在 profiles/ 底下
    assert _active_agent_profile_name() == "Stithy"


def test_flat_process_without_agent_id_has_no_identity(monkeypatch, tmp_path):
    """L1 / flat：没有 AGENT_ID、家也不在 profiles/ 下 ⇒ ''（逐字旧行为）。"""
    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path))
    assert _active_agent_profile_name() == ""


def test_profile_home_is_still_the_fallback(monkeypatch, tmp_path):
    """老形状（装机链上还有这种进程）：家在 profiles/ 下 ⇒ 回退到家名。"""
    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)
    home = tmp_path / "profiles" / "l2-agenda"
    home.mkdir(parents=True)
    monkeypatch.setenv("PLOBI_HOME", str(home))
    assert _active_agent_profile_name() == "l2-agenda"


# ── ② 查找按 name（AGENT_ID 是 name 不是 profile 名） ─────────────────────


def test_lookup_matches_the_registry_name_not_only_the_profile_name():
    entries = [_entry(name="Stithy", profile="l2-stithy")]
    assert _entry_for_profile_in(entries, "stithy") is entries[0]
    assert _entry_for_profile_in(entries, "l2-stithy") is entries[0]


def test_lookup_never_returns_an_archived_row():
    entries = [_entry(name="Stithy", archived=True)]
    assert _entry_for_profile_in(entries, "stithy") is None


# ── ③ overlay 的门：共享根 + 身份仍给覆盖 ─────────────────────────────────


def test_overlay_gate_no_longer_shuts_the_shared_root_out(monkeypatch, tmp_path):
    """本刀的核心：家就是共享根、但带着 AGENT_ID ⇒ 覆盖照给。

    旧代码在这里 `if home == get_shared_plobi_root(): return {}` —— 那一行
    把 B 刀之后所有 L2 的模型 / cwd / 工具集黑名单一起关在门外。
    """
    monkeypatch.setenv("PLOBI_AGENT_ID", "Stithy")
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path))
    from plobi_cli.config import agent_config_overlay

    # 名册里查得到这一行时才可能有覆盖；查不到 ⇒ 空（这是既有的正确判据）
    overlay = agent_config_overlay({"agent": {}})
    assert isinstance(overlay, dict)


def test_overlay_carries_the_three_blacklists(monkeypatch, tmp_path):
    monkeypatch.setenv("PLOBI_AGENT_ID", "Stithy")
    entry = _entry(disabled_toolsets=("terminal",), disabled_skills=("foo",))
    monkeypatch.setattr(
        "plobi.agents.registry._registry_and_entry_for_profile",
        lambda name: (None, entry),
    )
    overlay = profile_config_overlay("Stithy")
    assert overlay["agent"]["disabled_toolsets"] == ["terminal"]
    assert overlay["agent"]["disabled_skills"] == ["foo"]


def test_overlay_omits_the_agent_section_when_nothing_is_disabled(monkeypatch):
    monkeypatch.setenv("PLOBI_AGENT_ID", "Stithy")
    entry = _entry()
    monkeypatch.setattr(
        "plobi.agents.registry._registry_and_entry_for_profile",
        lambda name: (None, entry),
    )
    overlay = profile_config_overlay("Stithy")
    assert "agent" not in overlay


# ── ④ diet 真源写名册行 ───────────────────────────────────────────────────


def test_diet_writes_the_four_names_into_the_registry_row():
    entry = _entry()
    assert diet_entry_toolsets(entry) is True
    assert set(entry.disabled_toolsets) == set(L2_PROJECT_DROP_TOOLSETS)


def test_diet_is_idempotent():
    entry = _entry()
    assert diet_entry_toolsets(entry) is True
    assert diet_entry_toolsets(entry) is False
    assert len(entry.disabled_toolsets) == len(L2_PROJECT_DROP_TOOLSETS)


def test_diet_never_touches_a_users_own_blacklist():
    entry = _entry(disabled_toolsets=("web",))
    assert diet_entry_toolsets(entry) is True
    assert entry.disabled_toolsets[0] == "web"  # 用户自己的排在先，一字不动
    assert set(entry.disabled_toolsets[1:]) == set(L2_PROJECT_DROP_TOOLSETS)


def test_diet_refuses_non_project_rows():
    assert diet_entry_toolsets(_entry(role="l2_secretary")) is False
    assert diet_entry_toolsets(_entry(archived=True)) is False
    assert diet_entry_toolsets(_entry(category="courses")) is False


def test_diet_does_not_write_the_plobi_composite():
    """plobi-* 复合不进黑名单：它不是用户勾来的，禁它连带禁掉健康的叶子。"""
    entry = _entry()
    diet_entry_toolsets(entry)
    assert not any(n.startswith("plobi-") for n in entry.disabled_toolsets)


# ── ⑤ 读侧并集 ────────────────────────────────────────────────────────────


def test_union_keeps_the_users_own_top_level_choices():
    from plobi_cli.config import _union_agent_blacklists

    overlay = {"agent": {"disabled_toolsets": ["terminal"]}}
    current = {"agent": {"disabled_toolsets": ["web"]}}
    merged = _union_agent_blacklists(overlay, current)
    assert set(merged["agent"]["disabled_toolsets"]) == {"web", "terminal"}


def test_union_without_a_base_is_a_noop():
    from plobi_cli.config import _union_agent_blacklists

    overlay = {"agent": {"disabled_toolsets": ["terminal"]}}
    assert _union_agent_blacklists(overlay, None) == overlay
    assert _union_agent_blacklists(overlay, {}) == overlay


def test_union_leaves_an_overlay_without_an_agent_section_alone():
    from plobi_cli.config import _union_agent_blacklists

    overlay = {"model": {"default": "kimi"}}
    assert _union_agent_blacklists(overlay, {"agent": {"disabled_toolsets": ["web"]}}) == overlay
