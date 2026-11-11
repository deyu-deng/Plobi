"""Tests for the profile.yaml metadata layer (description + description_auto)
and the profile_describer LLM module.
"""

from __future__ import annotations

import json as jsonlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from plobi_cli import agent_identity as profiles_mod
from plobi_cli import profile_describer as describer


@pytest.fixture
def profile_env(tmp_path, monkeypatch):
    """Set up an isolated PLOBI_HOME with a default profile dir."""
    home = tmp_path / ".plobi"
    home.mkdir()
    (home / "plobi").mkdir()
    monkeypatch.setenv("PLOBI_HOME", str(home))
    # 名册在共享层那一份（家退休之后只有一份家，名册也只这一份）。
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(home / "plobi" / "projects.yaml"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return home


def _seed_identity(home, name, **extra):
    """名册上落一行 Agent —— 身份＝名册那一行（裁 192），描述的真源也是它。

    家退休之后描述不再住在 ``<家>/profile.yaml``，而是住在**名册那一行**
    （``read_identity_description`` / ``write_identity_description``）——
    以前那套「monkeypatch ``profile_exists`` ＋ ``get_profile_dir``」钉的是已退役的老世界，
    11-10 起这一档 4 条红的根因就是它。
    """
    from plobi.agents.registry import AgentEntry, AgentRegistry

    registry = AgentRegistry(home / "plobi" / "projects.yaml")
    registry.upsert(AgentEntry(name=name, role="l2_project", **extra))
    registry.save()


def _identity_description(home, name):
    from plobi.agents.registry import AgentRegistry

    entry = AgentRegistry.load(home / "plobi" / "projects.yaml").get(name)
    return (entry.description, entry.description_auto)


def test_read_profile_meta_empty_when_missing(profile_env):
    meta = profiles_mod.read_profile_meta(profile_env)
    assert meta == {"description": "", "description_auto": False}


def test_write_and_read_profile_meta(profile_env):
    profiles_mod.write_profile_meta(
        profile_env,
        description="a useful researcher",
        description_auto=False,
    )
    meta = profiles_mod.read_profile_meta(profile_env)
    assert meta["description"] == "a useful researcher"
    assert meta["description_auto"] is False


def test_write_profile_meta_preserves_other_fields(profile_env):
    # First write sets description_auto=True; second write only updates
    # description and leaves description_auto unchanged.
    profiles_mod.write_profile_meta(
        profile_env,
        description="auto-gen",
        description_auto=True,
    )
    profiles_mod.write_profile_meta(profile_env, description="edited by hand")
    meta = profiles_mod.read_profile_meta(profile_env)
    assert meta["description"] == "edited by hand"
    assert meta["description_auto"] is True


def test_write_profile_meta_rejects_missing_dir(tmp_path):
    bogus = tmp_path / "does_not_exist"
    with pytest.raises(FileNotFoundError):
        profiles_mod.write_profile_meta(bogus, description="x")


def test_read_profile_meta_tolerates_corrupt_yaml(profile_env):
    (profile_env / "profile.yaml").write_text("not: valid: yaml: [unclosed")
    meta = profiles_mod.read_profile_meta(profile_env)
    assert meta == {"description": "", "description_auto": False}


# ---------------------------------------------------------------------------
# profile_describer module
# ---------------------------------------------------------------------------


def _fake_aux_response(content: str):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    return resp


def _patch_aux_client(content: str):
    client = MagicMock()
    client.chat.completions.create = MagicMock(return_value=_fake_aux_response(content))
    return patch(
        "agent.auxiliary_client.get_text_auxiliary_client",
        return_value=(client, "test-model"),
    )


def test_describer_writes_description_with_auto_true(profile_env, monkeypatch):
    _seed_identity(profile_env, "myprof")
    payload = jsonlib.dumps({"description": "writes Python codebases"})
    with _patch_aux_client(payload), patch(
        "agent.auxiliary_client.get_auxiliary_extra_body", return_value={}
    ):
        outcome = describer.describe_profile("myprof")

    assert outcome.ok, outcome.reason
    assert outcome.description == "writes Python codebases"
    desc, auto = _identity_description(profile_env, "myprof")
    assert desc == "writes Python codebases"
    assert auto is True


def test_describer_refuses_to_overwrite_user_authored(profile_env, monkeypatch):
    _seed_identity(
        profile_env, "myprof", description="curated", description_auto=False
    )
    outcome = describer.describe_profile("myprof")
    assert outcome.ok is False
    assert "already has a user-authored description" in outcome.reason
    # Description unchanged
    desc, auto = _identity_description(profile_env, "myprof")
    assert desc == "curated"
    assert auto is False


def test_describer_overwrite_flag_replaces_user_authored(profile_env, monkeypatch):
    _seed_identity(
        profile_env, "myprof", description="curated", description_auto=False
    )
    payload = jsonlib.dumps({"description": "new auto-gen"})
    with _patch_aux_client(payload), patch(
        "agent.auxiliary_client.get_auxiliary_extra_body", return_value={}
    ):
        outcome = describer.describe_profile("myprof", overwrite=True)
    assert outcome.ok, outcome.reason
    desc, auto = _identity_description(profile_env, "myprof")
    assert desc == "new auto-gen"
    assert auto is True


def test_describer_handles_malformed_llm_response(profile_env, monkeypatch):
    _seed_identity(profile_env, "myprof")

    # Non-JSON: describer falls back to taking the first paragraph as the description.
    with _patch_aux_client("Plain text description that sneaks in"), patch(
        "agent.auxiliary_client.get_auxiliary_extra_body", return_value={}
    ):
        outcome = describer.describe_profile("myprof")
    assert outcome.ok
    assert "Plain text description" in (outcome.description or "")


def test_describer_returns_false_when_profile_missing(profile_env, monkeypatch):
    outcome = describer.describe_profile("ghost")
    assert outcome.ok is False
    assert "not found" in outcome.reason
