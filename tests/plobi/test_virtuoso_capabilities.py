"""名手（专家）的 **Capabilities**：自己带工具箱、自己有黑名单、不继承 principal
（`WP-EXPERT-CAPABILITIES`，11-15）。

用户 11-15 12:5x 定调：**能力的底永远是设置面那一份全局真源**；名手**另有一份自己
的黑名单**（角色级，减法），并且**不继承 principal 的工具设置**。

这一档钉五件事：

1. **默认工具面逐字不变**（四类角色的门类与 11-15 之前一模一样）——能力表是**派生**，
   不是另写一份。
2. 名手**不与 principal 求交集**（父没有 terminal 时，software 名手照样有 terminal）。
3. 子代理那一支**仍旧继承父的**（两族两套规则）。
4. 名手**自己的黑名单**减得掉；**全局硬压制**照旧生效（它是真源的一部分）。
5. 派出去之前**说人话**：缺哪样能力写出来（派出去的与承诺的不符，是最容易被忽略的坑）。
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture()
def roles_home(tmp_path, monkeypatch):
    """把名手那份角色级设置挪到临时目录（不碰这台机器的真档）。"""
    monkeypatch.setenv("PLOBI_VIRTUOSOS_DIR", str(tmp_path / "virtuosos"))
    from plobi.delegation import virtuoso_roles

    virtuoso_roles._VIRTUOSOS_CACHE.clear() if hasattr(virtuoso_roles, "_VIRTUOSOS_CACHE") else None
    return virtuoso_roles


# ── 1 · 默认工具面逐字不变 ─────────────────────────────────────────────────


def test_the_default_toolsets_are_unchanged(roles_home):
    """能力表是**派生**的 ⇒ 四个角色的门类与旧表逐字一致（顺序也一样）。"""
    assert roles_home.TOOLSETS["file"] == ["file"]
    assert roles_home.TOOLSETS["browser"] == ["browser", "web"]
    assert roles_home.TOOLSETS["software"] == ["terminal", "file"]
    assert roles_home.TOOLSETS["computer"] == ["terminal", "computer_use"]


def test_every_role_capability_maps_to_a_toolset(roles_home):
    """每个角色的每项能力都要落到门类上（不悬空）。"""
    for role, capabilities in roles_home.ROLE_CAPABILITIES.items():
        assert capabilities, f"{role} 没有能力"
        for capability in capabilities:
            assert capability in roles_home.CAPABILITIES, capability
            assert roles_home.CAPABILITY_TOOLSETS.get(capability), capability


def test_the_capability_pool_is_a_closed_list(roles_home):
    """池子里那几项能力，人话标签都在（屏上要念出来的）。"""
    for key, label in roles_home.CAPABILITIES.items():
        assert label and key.isascii() and key.replace("_", "").isalnum()


# ── 2 · 名手不继承 principal ────────────────────────────────────────────────


def test_an_virtuoso_does_not_inherit_the_parents_toolsets(roles_home):
    """**不继承 principal 的工具设置**：父只有 file 时，software 名手照样有 terminal。"""
    from tools.delegate_tool import _child_toolset_selection

    picked = _child_toolset_selection({"file"}, ["terminal", "file"], "software")

    assert picked == ["terminal", "file"]


def test_a_plain_subagent_still_inherits_the_parent(roles_home):
    """子代理那一支**仍旧继承父的**（那是另一族，两套规则别混）。"""
    from tools.delegate_tool import _child_toolset_selection

    picked = _child_toolset_selection({"file"}, ["terminal", "file"], None)

    assert picked == ["file"]


# ── 3 · 名手自己的黑名单 ───────────────────────────────────────────────────


def test_the_virtuoso_own_blacklist_subtracts(roles_home):
    """名手自己关掉的能力 ⇒ 不在它的能力里，也不在它的门类里。"""
    roles_home.save_role_disabled_capabilities("software", ["run_commands"])

    assert "跑命令" not in roles_home.effective_capabilities("software")
    assert "terminal" not in roles_home.effective_toolsets("software")
    assert "跑命令" in roles_home.missing_capabilities("software")


def test_the_global_hard_denylist_still_applies(roles_home, monkeypatch):
    """全局硬压制（设置面那份）是**真源的一部分** ⇒ 名手也受它管。"""
    import plobi_cli.config as cli_config

    monkeypatch.setattr(
        cli_config, "load_config", lambda *a, **k: {"agent": {"disabled_toolsets": ["terminal"]}}
    )

    assert "terminal" not in roles_home.effective_toolsets("software")
    assert "跑命令" in roles_home.missing_capabilities("software")


def test_the_role_file_keeps_the_model_when_the_blacklist_changes(roles_home):
    """角色级那一份文件：改黑名单**不许**抹掉模型，反过来也一样。"""
    roles_home.save_role_model("software", "prov/model-x")
    roles_home.save_role_disabled_capabilities("software", ["see_images"])

    settings = roles_home.load_role_settings()["software"]
    assert settings.get("model") == "prov/model-x"
    assert settings.get("disabled_capabilities") == ["see_images"]

    roles_home.save_role_model("software", None)
    settings = roles_home.load_role_settings()["software"]
    assert "model" not in settings
    assert settings.get("disabled_capabilities") == ["see_images"]


def test_an_unknown_capability_is_refused(roles_home):
    with pytest.raises(ValueError):
        roles_home.save_role_disabled_capabilities("software", ["fly_to_the_moon"])


# ── 4 · 派之前说人话 ───────────────────────────────────────────────────────


def test_the_dispatch_reports_what_the_virtuoso_can_do(roles_home, monkeypatch):
    """派出去的那一趟要带着「它现在有什么、缺什么」。"""
    import tools.delegate_tool as dt

    roles_home.save_role_disabled_capabilities("software", ["run_commands"])

    captured = {}

    def fake_delegate_task(**kwargs):
        captured.update(kwargs)
        return json.dumps({"ok": True, "dispatched": True}, ensure_ascii=False)

    monkeypatch.setattr(dt, "delegate_task", fake_delegate_task)

    out = json.loads(dt.delegate_virtuoso(role="software", goal="跑一遍构建脚本"))

    note = out["virtuoso_capabilities"]
    assert note["role"] == "software"
    assert "跑命令" in note["missing"]
    assert "terminal" not in note["toolsets"]
    assert "写文件" in note["effective"]
