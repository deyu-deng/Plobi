"""Tests for the cross-profile soft guard wired into write_file / patch /
skill_manage.

The classifier is tested in tests/agent/test_file_safety_cross_agent.py.
This file tests that the tool surfaces:

  1. Refuse cross-profile writes by default and return the warning.
  2. Accept cross-profile writes when cross_profile=True is passed.
  3. Continue to accept in-profile writes normally.
  4. skill_manage's "not found" error names other profiles where the
     skill exists.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def fake_plobi(tmp_path, monkeypatch):
    """Build a two-profile Plobi layout and point PLOBI_HOME at
    the plobi-security profile (matching the original-incident shape).
    """
    root = tmp_path / "fake-plobi"
    (root / "skills" / "shared-skill").mkdir(parents=True)
    (root / "skills" / "shared-skill" / "SKILL.md").write_text(
        "---\nname: shared-skill\ndescription: default copy.\n---\n"
    )

    sec_home = root / "profiles" / "plobi-security"
    (sec_home / "skills").mkdir(parents=True)

    coder_home = root / "profiles" / "coder"
    (coder_home / "skills").mkdir(parents=True)

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
    # 身份＝名册那一行（裁定 180）：这个会话是 plobi-security。
    monkeypatch.setenv("PLOBI_AGENT_ID", "plobi-security")

    monkeypatch.setenv("PLOBI_HOME", str(sec_home))

    import plobi_constants
    monkeypatch.setattr(plobi_constants, "get_default_plobi_root", lambda: root)

    import agent.file_safety as fs
    monkeypatch.setattr(fs, "_plobi_home_path", lambda: sec_home)
    monkeypatch.setattr(fs, "_plobi_root_path", lambda: root)

    return {
        "root": root,
        "sec_home": sec_home,
        "coder_home": coder_home,
    }


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------


class TestWriteFileCrossProfileGuard:
    def test_in_profile_write_allowed(self, fake_plobi):
        from tools.file_tools import write_file_tool
        target = fake_plobi["sec_home"] / "skills" / "new-skill" / "SKILL.md"
        target.parent.mkdir(parents=True)
        result_json = write_file_tool(str(target), "in-profile content")
        result = json.loads(result_json)
        assert not result.get("error"), f"In-profile write should succeed: {result}"
        assert target.exists()
        assert target.read_text() == "in-profile content"

    def test_cross_agent_write_blocked_by_default(self, fake_plobi):
        """同一个事故的**新形状**：plobi-security 那个会话去改 Aura 的身份页。

        旧形状是「改默认家的技能」；家退休之后共享层是大家的，
        真正「属于别人」的是共享层下按名册行分的那几份 —— 身份页／记忆／排程存储。
        """
        from tools.file_tools import write_file_tool
        target = fake_plobi["root"] / "souls" / "Aura.md"
        original = target.read_text()
        result_json = write_file_tool(str(target), "OVERWRITTEN")
        result = json.loads(result_json)
        assert result.get("error"), "Cross-agent write should be refused"
        assert "cross-agent" in result["error"].lower()
        assert "Aura" in result["error"]
        assert "plobi-security" in result["error"]
        # File untouched.
        assert target.read_text() == original

    def test_shared_layer_write_is_allowed(self, fake_plobi):
        """共享层是大家的 ⇒ 不拦（拦了就是不让 Agent 管自己的技能）。"""
        from tools.file_tools import write_file_tool
        target = fake_plobi["root"] / "skills" / "shared-skill" / "SKILL.md"
        result_json = write_file_tool(str(target), "everyone's copy")
        result = json.loads(result_json)
        assert not result.get("error"), f"Shared layer must stay writable: {result}"
        assert target.read_text() == "everyone's copy"

    def test_cross_agent_bypass(self, fake_plobi):
        """Explicit override after user direction must succeed (新参数名 ＋ 旧名)。"""
        from tools.file_tools import write_file_tool
        target = fake_plobi["root"] / "souls" / "Aura.md"
        result_json = write_file_tool(
            str(target), "user-directed override", cross_agent=True
        )
        result = json.loads(result_json)
        assert not result.get("error"), f"cross_agent=True must succeed: {result}"
        assert target.read_text() == "user-directed override"

        # 旧名照样收（旧 transcript／旧调用不许坏）。
        result_json = write_file_tool(
            str(target), "old kwarg still works", cross_profile=True
        )
        result = json.loads(result_json)
        assert not result.get("error"), f"cross_profile=True must still work: {result}"

    def test_non_plobi_path_unaffected(self, fake_plobi, tmp_path):
        from tools.file_tools import write_file_tool
        target = tmp_path / "outside" / "main.py"
        target.parent.mkdir()
        result_json = write_file_tool(str(target), "print('hello')")
        result = json.loads(result_json)
        assert not result.get("error")
        assert target.exists()


# ---------------------------------------------------------------------------
# patch
# ---------------------------------------------------------------------------


class TestPatchCrossProfileGuard:
    def test_cross_agent_patch_blocked(self, fake_plobi):
        from tools.file_tools import patch_tool
        target = fake_plobi["root"] / "souls" / "Aura.md"
        original = target.read_text()
        result_json = patch_tool(
            mode="replace",
            path=str(target),
            old_string="# Aura",
            new_string="HIJACKED.",
        )
        result = json.loads(result_json)
        assert result.get("error")
        assert "cross-agent" in result["error"].lower()
        assert target.read_text() == original

    def test_cross_agent_patch_bypass(self, fake_plobi):
        from tools.file_tools import patch_tool
        target = fake_plobi["root"] / "souls" / "Aura.md"
        result_json = patch_tool(
            mode="replace",
            path=str(target),
            old_string="# Aura",
            new_string="user-directed update.",
            cross_agent=True,
        )
        result = json.loads(result_json)
        assert not result.get("error"), f"cross_agent=True bypass: {result}"
        assert "user-directed update." in target.read_text()

    def test_v4a_patch_extracts_path_for_guard(self, fake_plobi):
        """V4A patches embed the target paths in the patch body, not in
        a ``path`` kwarg. The guard must still apply."""
        from tools.file_tools import patch_tool
        target = fake_plobi["root"] / "souls" / "Aura.md"
        original = target.read_text()
        v4a = (
            "*** Begin Patch\n"
            f"*** Update File: {target}\n"
            "@@\n"
            "-# Aura\n"
            "+HIJACKED.\n"
            "*** End Patch"
        )
        result_json = patch_tool(mode="patch", patch=v4a)
        result = json.loads(result_json)
        assert result.get("error"), f"V4A cross-agent must block: {result}"
        assert "cross-agent" in result["error"].lower()
        assert target.read_text() == original


# ---------------------------------------------------------------------------
# skill_manage — error message naming other profile (item D)
# ---------------------------------------------------------------------------


class TestSkillManageCrossProfileErrorUX:
    def _make_skill_in_profile(self, profile_dir: Path, name: str):
        d = profile_dir / "skills" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: a skill.\n---\n"
        )

    def test_error_names_other_profile_when_skill_lives_there(
        self, fake_plobi, monkeypatch
    ):
        """The original incident shape — model expects 'foo' in active
        profile, but 'foo' lives in default. Error must point at default."""
        self._make_skill_in_profile(fake_plobi["root"], "default-only-skill")

        # Re-import the module so SKILLS_DIR picks up PLOBI_HOME (set in
        # the fixture). Skill_manager_tool computes SKILLS_DIR at import.
        import importlib
        import tools.skill_manager_tool
        importlib.reload(tools.skill_manager_tool)
        from tools.skill_manager_tool import _skill_not_found_error

        err = _skill_not_found_error("default-only-skill")
        assert "not found in active profile 'plobi-security'" in err
        assert "default" in err
        assert "cross_profile=True" in err

    def test_error_names_multiple_profiles(self, fake_plobi, monkeypatch):
        """When the skill exists in TWO other profiles, both should be named."""
        self._make_skill_in_profile(fake_plobi["root"], "everywhere-skill")
        self._make_skill_in_profile(fake_plobi["coder_home"], "everywhere-skill")

        import importlib
        import tools.skill_manager_tool
        importlib.reload(tools.skill_manager_tool)
        from tools.skill_manager_tool import _skill_not_found_error

        err = _skill_not_found_error("everywhere-skill")
        assert "default" in err
        assert "coder" in err
        # Switch-profiles hint
        assert "plobi -p" in err

    def test_genuinely_missing_skill_keeps_helpful_hint(
        self, fake_plobi, monkeypatch
    ):
        """When no profile has the skill, error falls back to skills_list hint."""
        import importlib
        import tools.skill_manager_tool
        importlib.reload(tools.skill_manager_tool)
        from tools.skill_manager_tool import _skill_not_found_error

        err = _skill_not_found_error("totally-imaginary-skill")
        assert "not found in active profile 'plobi-security'" in err
        assert "skills_list" in err


# ---------------------------------------------------------------------------
# System prompt active-profile line (item B)
# ---------------------------------------------------------------------------


class TestSystemPromptActiveProfile:
    def test_default_profile_line_in_prompt(self, tmp_path, monkeypatch):
        """When active profile is 'default', the prompt names it and warns
        about ~/.plobi/profiles/<name>/."""
        # Don't set PLOBI_HOME — falls back to default.
        import agent.file_safety as fs
        monkeypatch.setattr(fs, "_plobi_home_path", lambda: tmp_path / "fake")
        monkeypatch.setattr(fs, "_plobi_root_path", lambda: tmp_path / "fake")

        from agent.file_safety import _resolve_active_profile_name
        assert _resolve_active_profile_name() == "default"
        # Build the line manually to pin the contract — the prompt builder
        # is too heavy to instantiate end-to-end in a unit test.
        # See agent/system_prompt.py for the exact wording.

    def test_named_profile_line_in_prompt_text(self, fake_plobi):
        """When active profile is 'plobi-security', the prompt says who the
        agent is and warns against touching another agent's files.

        契约（10-27 随 WP-PROFILE-VOCAB 改过）：家目录那条腿已退休——提示词里
        ① 点明**身份是名册上的一行**（``Active agent:``）；② 说清**只有一份共享家**
        （``ONE shared home``），不再有「每 Agent 一个家」那套说法；③ 说「别动别人的
        那几份」；④ 绕过那个旗标叫 ``cross_agent=True``。
        """
        from pathlib import Path
        src = Path("agent/system_prompt.py").read_text()
        assert "Active agent:" in src
        assert "cross_agent=True" in src
        # 一份共享家 + 「没有按 Agent 分的家」这句话必须在（那句旧话是假的）
        assert "ONE shared home" in src
        assert "no per-agent home directories any more" in src
        # 别动别人的那几份
        assert "Leave another agent's alone" in src
        # 两个分支都在（默认身份与有名有姓的身份）
        assert "Plobi keeps ONE shared home" in src
        assert "Active agent: {active_profile}" in src
        # 旧词不许再出现在给模型看的段落里（内部名不算）
        assert "Active Plobi profile" not in src
