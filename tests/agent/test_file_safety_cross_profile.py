"""Tests for the cross-Plobi-profile write guard in agent/file_safety.

The guard fires when a tool tries to write into another Plobi profile's
skills/plugins/cron/memories directory. It's a soft guard — defense in
depth, NOT a security boundary — but it prevents the agent from silently
corrupting a profile that belongs to a different session.

Reference: May 2026 incident — a plobi-security profile session
accidentally edited skills under both ~/.plobi/profiles/plobi-security/skills/
AND ~/.plobi/skills/ (the default profile's skills), realizing only
afterwards that the second path belonged to a different profile.
"""
from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers — set up a fake Plobi root with two profiles, monkeypatch the
# resolver helpers so the classifier sees the test layout.
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_plobi(tmp_path, monkeypatch):
    """Build a fake Plobi layout:

        <tmp>/
          skills/foo/SKILL.md           # default profile
          plugins/foo/__init__.py
          cron/<state>
          memories/MEMORY.md
          profiles/
            plobi-security/
              skills/foo/SKILL.md       # named profile
              plugins/...
            coder/
              skills/foo/SKILL.md       # another named profile
    """
    root = tmp_path / "fake-plobi"
    (root / "skills" / "foo").mkdir(parents=True)
    (root / "skills" / "foo" / "SKILL.md").write_text("# default skill\n")
    (root / "plugins" / "foo").mkdir(parents=True)
    (root / "memories").mkdir(parents=True)
    (root / "cron").mkdir(parents=True)

    sec_home = root / "profiles" / "plobi-security"
    (sec_home / "skills" / "foo").mkdir(parents=True)
    (sec_home / "skills" / "foo" / "SKILL.md").write_text("# sec skill\n")
    (sec_home / "plugins").mkdir(parents=True)

    coder_home = root / "profiles" / "coder"
    (coder_home / "skills" / "foo").mkdir(parents=True)
    (coder_home / "skills" / "foo" / "SKILL.md").write_text("# coder skill\n")

    # 共享层下按名册行分的那三份（10-27 随 WP-PROFILE-GUARD-SCOPE）：
    # 身份页、它自己的记忆、它那一份排程存储。
    (root / "souls").mkdir(parents=True)
    (root / "souls" / "Aura.md").write_text("# Aura\n", encoding="utf-8")
    (root / "memory" / "agents" / "aura").mkdir(parents=True)
    (root / "cron" / "agents" / "aura").mkdir(parents=True)

    # 名册（身份的真源）：守卫靠它把「键」还原成「名册上的名字」。
    roster = root / "plobi" / "projects.yaml"
    roster.parent.mkdir(parents=True, exist_ok=True)
    roster.write_text(
        "agents:\n"
        "  Aura:\n"
        "    role: l2_project\n"
        "    profile: aura\n"
        "  plobi-security:\n"
        "    role: l2_project\n"
        "    profile: plobi-security\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(roster))

    # Monkeypatch the resolver functions used by file_safety so each test
    # can choose which profile is "active".
    import plobi_constants
    monkeypatch.setattr(plobi_constants, "get_default_plobi_root", lambda: root)

    # The reloads below ensure get_cross_profile_warning/classify see the patched root.
    import agent.file_safety as fs
    monkeypatch.setattr(fs, "_plobi_root_path", lambda: root)

    return {
        "root": root,
        "default_home": root,
        "security_home": sec_home,
        "coder_home": coder_home,
    }


def _set_active_home(monkeypatch, plobi_home: Path):
    """Point file_safety._plobi_home_path at a specific profile dir."""
    import agent.file_safety as fs
    monkeypatch.setattr(fs, "_plobi_home_path", lambda: plobi_home)


# ---------------------------------------------------------------------------
# _resolve_active_profile_name
# ---------------------------------------------------------------------------


class TestResolveActiveProfileName:
    def test_default_when_home_is_root(self, fake_plobi, monkeypatch):
        _set_active_home(monkeypatch, fake_plobi["default_home"])
        from agent.file_safety import _resolve_active_profile_name
        assert _resolve_active_profile_name() == "default"

    def test_named_profile(self, fake_plobi, monkeypatch):
        _set_active_home(monkeypatch, fake_plobi["security_home"])
        from agent.file_safety import _resolve_active_profile_name
        assert _resolve_active_profile_name() == "plobi-security"

    def test_falls_back_to_default_on_resolution_failure(self, fake_plobi, monkeypatch):
        """If PLOBI_HOME resolution raises, return 'default' rather than crashing the tool."""
        import agent.file_safety as fs

        def _boom():
            raise RuntimeError("simulated")

        monkeypatch.setattr(fs, "_plobi_home_path", _boom)
        # Should not raise — falls back to "default"
        assert fs._resolve_active_profile_name() == "default"


# ---------------------------------------------------------------------------
# classify_cross_profile_target
# ---------------------------------------------------------------------------


class TestClassifyCrossProfileTarget:
    def test_same_profile_write_returns_none(self, fake_plobi, monkeypatch):
        _set_active_home(monkeypatch, fake_plobi["security_home"])
        from agent.file_safety import classify_cross_profile_target
        result = classify_cross_profile_target(
            str(fake_plobi["security_home"] / "skills" / "foo" / "SKILL.md")
        )
        assert result is None

    def test_shared_layer_is_not_anyones_private_area(self, fake_plobi, monkeypatch):
        """共享层那几样**不是**谁的私有物 ⇒ 不判（10-27 随 WP-PROFILE-GUARD-SCOPE）。

        旧世界里「默认家的 skills」＝``<root>/skills/``（默认家＝共享根），
        所以那一句是「某个人的」；家退休之后默认家不再独占了——
        ``<root>/skills/`` 是**大家的**。拦它就是不让 Agent 管自己的技能。
        """
        _set_active_home(monkeypatch, fake_plobi["security_home"])
        from agent.file_safety import classify_cross_profile_target
        result = classify_cross_profile_target(
            str(fake_plobi["default_home"] / "skills" / "foo" / "SKILL.md")
        )
        assert result is None

    def test_default_writing_security_skill(self, fake_plobi, monkeypatch):
        """Inverse direction — default-profile session reaching into a named profile."""
        _set_active_home(monkeypatch, fake_plobi["default_home"])
        from agent.file_safety import classify_cross_profile_target
        result = classify_cross_profile_target(
            str(fake_plobi["security_home"] / "skills" / "foo" / "SKILL.md")
        )
        assert result is not None
        assert result["active_profile"] == "default"
        assert result["target_profile"] == "plobi-security"

    def test_named_to_named_cross_profile(self, fake_plobi, monkeypatch):
        _set_active_home(monkeypatch, fake_plobi["security_home"])
        from agent.file_safety import classify_cross_profile_target
        result = classify_cross_profile_target(
            str(fake_plobi["coder_home"] / "skills" / "foo" / "SKILL.md")
        )
        assert result is not None
        assert result["target_profile"] == "coder"

    @pytest.mark.parametrize("area", ["skills", "plugins", "cron"])
    def test_shared_layer_areas_not_classified(self, fake_plobi, monkeypatch, area):
        """共享层那三处一律不判（10-27 随 WP-PROFILE-GUARD-SCOPE）。"""
        _set_active_home(monkeypatch, fake_plobi["security_home"])
        from agent.file_safety import classify_cross_profile_target
        target = fake_plobi["default_home"] / area / "foo.txt"
        assert classify_cross_profile_target(str(target)) is None

    def test_non_plobi_path_returns_none(self, fake_plobi, monkeypatch, tmp_path):
        _set_active_home(monkeypatch, fake_plobi["security_home"])
        from agent.file_safety import classify_cross_profile_target
        # Path outside any Plobi root
        assert classify_cross_profile_target(str(tmp_path / "random.txt")) is None

    def test_plobi_config_not_classified_as_cross_profile(self, fake_plobi, monkeypatch):
        """Files under <root>/config.yaml or <root>/.env are NOT profile-scoped
        (already covered by build_write_denied_paths). Don't double-warn."""
        _set_active_home(monkeypatch, fake_plobi["security_home"])
        from agent.file_safety import classify_cross_profile_target
        # config.yaml at root level is not in PROFILE_SCOPED_AREAS
        result = classify_cross_profile_target(
            str(fake_plobi["default_home"] / "config.yaml")
        )
        assert result is None


# ---------------------------------------------------------------------------
# get_cross_profile_warning
# ---------------------------------------------------------------------------


class TestGetCrossProfileWarning:
    def test_in_profile_returns_none(self, fake_plobi, monkeypatch):
        _set_active_home(monkeypatch, fake_plobi["security_home"])
        from agent.file_safety import get_cross_profile_warning
        assert get_cross_profile_warning(
            str(fake_plobi["security_home"] / "skills" / "foo" / "SKILL.md")
        ) is None

    def test_cross_profile_warning_names_both_agents(self, fake_plobi, monkeypatch):
        """警告要说清**谁的哪一份** ＋ 现在的身份是谁 ＋ 怎么绕（10-27 新形状）。"""
        _set_active_home(monkeypatch, fake_plobi["security_home"])
        from agent.file_safety import get_cross_profile_warning
        warn = get_cross_profile_warning(
            str(fake_plobi["root"] / "souls" / "Aura.md")
        )
        assert warn is not None
        # Must name BOTH agents so the model knows which is which.
        assert "Aura" in warn
        assert "plobi-security" in warn
        # Must name the bypass kwarg.
        assert "cross_agent=True" in warn
        # Must reference the area.
        assert "identity" in warn

    def test_warning_is_defense_in_depth_not_boundary(self, fake_plobi, monkeypatch):
        _set_active_home(monkeypatch, fake_plobi["security_home"])
        from agent.file_safety import get_cross_profile_warning
        warn = get_cross_profile_warning(
            str(fake_plobi["root"] / "souls" / "Aura.md")
        )
        # Must self-document as defense-in-depth so future reviewers
        # don't promote it to a hard block.
        assert "not a security boundary" in warn.lower()


# ---------------------------------------------------------------------------
# memories is NOT a profile-scoped area (裁定 44 — one shared ledger)
# ---------------------------------------------------------------------------


class TestMemoriesIsNotProfileScoped:
    """Long-term memory lives at <root>/memories/ for every profile. Guarding
    it as a per-profile area made the file tools refuse a named-profile agent's
    write to its OWN memory file, because the target resolved to the default
    profile. These tests pin that the guard no longer touches memory while the
    surviving areas stay protected."""

    def test_memories_absent_from_scoped_areas(self):
        from agent.file_safety import PROFILE_SCOPED_AREAS
        assert "memories" not in PROFILE_SCOPED_AREAS
        assert set(PROFILE_SCOPED_AREAS) == {"skills", "plugins", "cron"}

    def test_shared_root_memories_not_classified(self, fake_plobi, monkeypatch):
        _set_active_home(monkeypatch, fake_plobi["security_home"])
        from agent.file_safety import classify_cross_profile_target
        assert classify_cross_profile_target(
            str(fake_plobi["root"] / "memories" / "MEMORY.md")
        ) is None

    def test_named_profile_memories_not_classified(self, fake_plobi, monkeypatch):
        """The measured fault: <root>/profiles/aura/memories/MEMORY.md."""
        _set_active_home(monkeypatch, fake_plobi["security_home"])
        from agent.file_safety import classify_cross_profile_target
        aura_memories = fake_plobi["root"] / "profiles" / "aura" / "memories"
        aura_memories.mkdir(parents=True)
        target = aura_memories / "MEMORY.md"
        target.write_text("# aura ledger\n")
        assert classify_cross_profile_target(str(target)) is None

    def test_surviving_areas_still_guarded(self, fake_plobi, monkeypatch):
        """新形状下「还活着的区域」＝每 Agent 自己的那三份（10-27）。"""
        _set_active_home(monkeypatch, fake_plobi["security_home"])
        from agent.file_safety import classify_cross_profile_target
        result = classify_cross_profile_target(
            str(fake_plobi["root"] / "souls" / "Aura.md")
        )
        assert isinstance(result, dict)
        assert result["area"] == "identity"
        assert result["target_profile"] == "Aura"
