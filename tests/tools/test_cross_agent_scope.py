# -*- coding: utf-8 -*-
"""跨 Agent 写守卫的新形状（`WP-PROFILE-GUARD-SCOPE`）。

家目录退休之后，「别人的东西」不再是「另一个 profile 的家」——家没了。共享层下按名册行分的
那三份才是：**身份页**（``souls/<名字>.md``）、**它自己的记忆**（``memory/agents/<键>/``）、
**它那一份排程存储**（``cron/agents/<键>/``）。

这一档钉住四件事：
① 那三份里**别人的**那份要拦；
② **自己的**那份不拦；
③ **共享层**（skills/ plugins/ memory/GLOBAL.md 顶层 cron/）不拦——那是大家的；
④ 认不出是谁的那一份 ⇒ 不判（宁可漏报，不误伤）。
"""
from pathlib import Path

import pytest

from agent import file_safety as fs


@pytest.fixture()
def layered_plobi(tmp_path, monkeypatch):
    """一份新形状的 Plobi：共享层 ＋ 名册两行 ＋ 每 Agent 那三份。"""
    root = tmp_path / "fake-plobi"
    (root / "skills").mkdir(parents=True)
    (root / "memory").mkdir(parents=True)
    (root / "souls").mkdir(parents=True)
    (root / "cron" / "agents").mkdir(parents=True)
    (root / "souls" / "Aura.md").write_text("# Aura\n", encoding="utf-8")
    (root / "souls" / "Sindri.md").write_text("# Sindri\n", encoding="utf-8")
    (root / "memory" / "agents" / "aura").mkdir(parents=True)
    (root / "memory" / "agents" / "sindri").mkdir(parents=True)
    (root / "cron" / "agents" / "aura").mkdir(parents=True)
    (root / "cron" / "agents" / "sindri").mkdir(parents=True)

    roster = root / "plobi" / "projects.yaml"
    roster.parent.mkdir(parents=True, exist_ok=True)
    roster.write_text(
        "agents:\n"
        "  Aura:\n"
        "    role: l2_project\n"
        "    profile: aura\n"
        "  Sindri:\n"
        "    role: l2_project\n"
        "    profile: sindri\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(roster))
    monkeypatch.setenv("PLOBI_AGENT_ID", "Sindri")   # 这个会话是 Sindri
    monkeypatch.setattr(fs, "_plobi_root_path", lambda: root)
    monkeypatch.setattr(fs, "_plobi_home_path", lambda: root)
    return root


# ── ① 别人的那三份要拦 ────────────────────────────────────────────────

@pytest.mark.parametrize("rel", [
    "souls/Aura.md",
    "memory/agents/aura/MEMORY.md",
    "cron/agents/aura/jobs.json",
])
def test_another_agents_own_files_are_guarded(layered_plobi, rel):
    result = fs.classify_cross_profile_target(str(layered_plobi / rel))
    assert result is not None, "别人的那几份要拦"
    assert result["target_agent"] == "Aura"
    assert result["active_agent"] == "Sindri"


# ── ② 自己的那几份不拦 ────────────────────────────────────────────────

@pytest.mark.parametrize("rel", [
    "souls/Sindri.md",
    "memory/agents/sindri/MEMORY.md",
    "cron/agents/sindri/jobs.json",
])
def test_own_files_are_not_guarded(layered_plobi, rel):
    assert fs.classify_cross_profile_target(str(layered_plobi / rel)) is None


# ── ③ 共享层不拦 ──────────────────────────────────────────────────────

@pytest.mark.parametrize("rel", [
    "skills/foo/SKILL.md",
    "plugins/foo/__init__.py",
    "memory/GLOBAL.md",
    "memory/USER.md",
    "cron/jobs.json",
])
def test_shared_layer_is_not_guarded(layered_plobi, rel):
    assert fs.classify_cross_profile_target(str(layered_plobi / rel)) is None


# ── ④ 认不出是谁的那一份 ⇒ 不判 ───────────────────────────────────────

def test_unknown_owner_is_not_classified(layered_plobi):
    """认不出是谁的 ⇒ 不判（宁可漏报，不误伤）。"""
    unknown = layered_plobi / "souls" / "nobody.md"
    unknown.write_text("# ?\n", encoding="utf-8")
    assert fs.classify_cross_profile_target(str(unknown)) is None


# ── ⑤ 身份＝名册那一行（PLOBI_AGENT_ID） ──────────────────────────────

def test_active_agent_comes_from_the_roster_id(layered_plobi, monkeypatch):
    """身份取 ``PLOBI_AGENT_ID``（裁定 180 的身份键），不是家名。"""
    monkeypatch.setenv("PLOBI_AGENT_ID", "Aura")
    assert fs._resolve_active_agent_name() == "Aura"
    # 家名那条路只当老装机兜底
    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)
    assert fs._resolve_active_agent_name() == "default"


def test_warning_names_the_owner_and_the_bypass(layered_plobi):
    warn = fs.get_cross_profile_warning(str(layered_plobi / "souls" / "Aura.md"))
    assert warn is not None
    assert "Aura" in warn
    assert "Sindri" in warn
    assert "cross_agent=True" in warn
    assert "not a security boundary" in warn.lower()
