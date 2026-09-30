"""WP-L2-DIET — L1 中收去掉 memory；项目 L2 减肥到没有 terminal / code runner。

裁定 33.2（L1）：``memory`` 工具不再出现在 L1 中收；活动 default 的
toolsets 跑一遍 ``mid_narrow_toolset_names`` 之后只剩 web / file /
skills / todo / clarify / delegation / plobi_north_star。

裁定 33.3（项目 L2）：每个 Mind 项目 L2 的 profile config.yaml 必须没
有 ``terminal`` / ``computer_use`` / ``code_execution`` / ``session_search``，
且任何 ``plobi-*`` 复合工具集都被剥掉。L2-agenda 不走这条——管家自己
有 ``ensure_l2_agenda_toolsets`` 单独管 terminal 准入。
"""

from __future__ import annotations

import yaml
import pytest


# ---------------------------------------------------------------------------
# L1 中收：memory 必须进 L1_DROP_TOOLSETS，不得进 L1_MID_TOOLSETS
# ---------------------------------------------------------------------------


def test_memory_is_dropped_from_l1_mid_toolsets():
    from plobi.agents.registry import L1_MID_TOOLSETS

    assert "memory" not in L1_MID_TOOLSETS


def test_memory_is_in_l1_drop_toolsets():
    from plobi.agents.registry import L1_DROP_TOOLSETS

    assert "memory" in L1_DROP_TOOLSETS


def test_mid_narrow_drops_memory_when_already_in_raw():
    from plobi.agents.registry import mid_narrow_toolset_names

    out = mid_narrow_toolset_names(["memory", "web", "file"])
    assert "memory" not in out
    # 其它 L1_MID_TOOLSETS 应当被补齐（包括 plobi_north_star + clarify）。
    assert "web" in out
    assert "file" in out
    assert "plobi_north_star" in out
    assert "clarify" in out


def test_mid_narrow_still_drops_terminal_session_search_code_execution_computer_use():
    from plobi.agents.registry import mid_narrow_toolset_names

    for forbidden in ("terminal", "session_search", "code_execution", "computer_use"):
        out = mid_narrow_toolset_names([forbidden, "web"])
        assert forbidden not in out, f"{forbidden} should be dropped"
        assert "web" in out


def test_mid_narrow_drops_plobi_cli_composite():
    from plobi.agents.registry import mid_narrow_toolset_names

    out = mid_narrow_toolset_names(["plobi-cli", "web"])
    assert "plobi-cli" not in out
    assert "web" in out
    assert "plobi_north_star" in out


def test_mid_narrow_default_when_input_empty():
    from plobi.agents.registry import mid_narrow_toolset_names, L1_MID_TOOLSETS

    out = mid_narrow_toolset_names(None)
    # 不含 memory 也不再含任何 drop-set。
    for forbidden in ("memory", "terminal", "session_search", "code_execution", "computer_use"):
        assert forbidden not in out
    for allowed in L1_MID_TOOLSETS:
        assert allowed in out


# ---------------------------------------------------------------------------
# 项目 L2 减肥：apply_l2_project_diet
# ---------------------------------------------------------------------------


def _write_config(profile_dir, *, top=None, platforms=None, extra=None):
    """写一份 config.yaml，可选 toolsets + platform_toolsets。"""
    cfg: dict = {}
    if top is not None:
        cfg["toolsets"] = list(top)
    if platforms is not None:
        cfg["platform_toolsets"] = {k: list(v) for k, v in platforms.items()}
    if extra:
        cfg.update(extra)
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "config.yaml").write_text(
        yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return profile_dir / "config.yaml"


def _read_toolsets(profile_dir):
    cfg = yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8"))
    return cfg or {}


def test_apply_l2_project_diet_strips_terminal_computer_use_code_execution_session_search(tmp_path):
    from plobi.agents.registry import apply_l2_project_diet

    profile_dir = tmp_path / "l2-plobi"
    _write_config(
        profile_dir,
        top=["terminal", "computer_use", "web", "skills", "code_execution", "session_search", "clarify"],
        platforms={"cli": ["terminal", "web", "session_search"], "gateway": ["plobi-cli", "code_execution"]},
    )

    assert apply_l2_project_diet(profile_dir) is True

    cfg = _read_toolsets(profile_dir)
    assert "terminal" not in cfg["toolsets"]
    assert "computer_use" not in cfg["toolsets"]
    assert "code_execution" not in cfg["toolsets"]
    assert "session_search" not in cfg["toolsets"]
    # 保留：file / web / skills / todo / clarify。
    assert "web" in cfg["toolsets"]
    assert "skills" in cfg["toolsets"]
    assert "clarify" in cfg["toolsets"]
    # 平台层。
    assert "terminal" not in cfg["platform_toolsets"]["cli"]
    assert "session_search" not in cfg["platform_toolsets"]["cli"]
    assert "web" in cfg["platform_toolsets"]["cli"]
    assert "plobi-cli" not in cfg["platform_toolsets"]["gateway"]
    assert "code_execution" not in cfg["platform_toolsets"]["gateway"]


def test_apply_l2_project_diet_strips_plobi_composite_toolsets(tmp_path):
    from plobi.agents.registry import apply_l2_project_diet

    profile_dir = tmp_path / "l2-animation"
    _write_config(
        profile_dir,
        top=["plobi-cli", "plobi-cli-gateway", "web"],
    )

    assert apply_l2_project_diet(profile_dir) is True

    cfg = _read_toolsets(profile_dir)
    assert "plobi-cli" not in cfg["toolsets"]
    assert "plobi-cli-gateway" not in cfg["toolsets"]
    assert "web" in cfg["toolsets"]


def test_apply_l2_project_diet_preserves_allowed_toolsets(tmp_path):
    from plobi.agents.registry import apply_l2_project_diet

    profile_dir = tmp_path / "l2-nuclide"
    _write_config(
        profile_dir,
        top=["file", "web", "skills", "todo", "clarify", "delegation", "plobi_north_star"],
    )

    # 没东西要删 → 第二次返回 False（idempotent）。
    assert apply_l2_project_diet(profile_dir) is False

    cfg = _read_toolsets(profile_dir)
    for allowed in ("file", "web", "skills", "todo", "clarify", "delegation", "plobi_north_star"):
        assert allowed in cfg["toolsets"]


def test_apply_l2_project_diet_is_idempotent(tmp_path):
    from plobi.agents.registry import apply_l2_project_diet

    profile_dir = tmp_path / "l2-prism"
    _write_config(
        profile_dir,
        top=["terminal", "web"],
    )

    assert apply_l2_project_diet(profile_dir) is True
    # 第二次已经干净 → 返回 False、不重写。
    assert apply_l2_project_diet(profile_dir) is False

    cfg = _read_toolsets(profile_dir)
    assert "terminal" not in cfg["toolsets"]
    assert "web" in cfg["toolsets"]


def test_apply_l2_project_diet_returns_false_for_missing_config(tmp_path):
    from plobi.agents.registry import apply_l2_project_diet

    assert apply_l2_project_diet(tmp_path / "no-such-profile") is False
    assert apply_l2_project_diet(None) is False


def test_apply_l2_project_diet_survives_garbage_yaml(tmp_path):
    from plobi.agents.registry import apply_l2_project_diet

    profile_dir = tmp_path / "broken"
    profile_dir.mkdir()
    (profile_dir / "config.yaml").write_text("not: valid: yaml: ::", encoding="utf-8")
    # 容错：解析失败 = 静默 False，不抛。
    assert apply_l2_project_diet(profile_dir) is False


def test_apply_l2_project_diet_preserves_other_keys(tmp_path):
    from plobi.agents.registry import apply_l2_project_diet

    profile_dir = tmp_path / "l2-stithy"
    _write_config(
        profile_dir,
        top=["terminal", "web"],
        platforms={"cli": ["terminal", "web"]},
        extra={"model": {"provider": "deepseek", "name": "deepseek-chat"}, "plobi": {"something": 1}},
    )

    assert apply_l2_project_diet(profile_dir) is True
    cfg = _read_toolsets(profile_dir)
    # 业务字段保持原样。
    assert cfg["model"] == {"provider": "deepseek", "name": "deepseek-chat"}
    assert cfg["plobi"] == {"something": 1}


# ---------------------------------------------------------------------------
# 用户显式开启的工具不得被下一次 diet 悄悄收回（裁定 33.3 的机制诚实性）
# ---------------------------------------------------------------------------


def _simulate_ui_toggle(profile_dir, platform, name):
    """Reproduce the exact write shape of ``_save_platform_tools`` — the helper
    behind both ``plobi tools`` and ``PUT /api/tools/toolsets/{name}`` (the
    desktop Toolsets panel): a *sorted* list for that ONE platform, and never
    the top-level ``toolsets``.
    """
    cfg = _read_toolsets(profile_dir)
    listed = set(cfg.setdefault("platform_toolsets", {}).get(platform, []))
    listed.add(name)
    cfg["platform_toolsets"][platform] = sorted(listed)
    (profile_dir / "config.yaml").write_text(
        yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


def test_diet_strips_a_freshly_materialised_profile_everywhere(tmp_path):
    """首落无回归：clone 来的 profile 顶层 + cli + gateway 三处同时带
    terminal（``apply_l1_mid_toolsets`` / ``create_profile(clone_config=True)``
    就是这么三份一起写的），一次 diet 必须三处全剥掉。"""
    from plobi.agents.registry import apply_l2_project_diet

    profile_dir = tmp_path / "l2-framelet"
    _write_config(
        profile_dir,
        top=["terminal", "web", "skills"],
        platforms={
            "cli": ["terminal", "web", "skills"],
            "gateway": ["terminal", "web", "skills"],
        },
    )

    assert apply_l2_project_diet(profile_dir) is True

    cfg = _read_toolsets(profile_dir)
    for key in ("toolsets",):
        assert "terminal" not in cfg[key]
    for platform in ("cli", "gateway"):
        assert "terminal" not in cfg["platform_toolsets"][platform], platform
        assert "web" in cfg["platform_toolsets"][platform], platform


def test_user_enabled_toolset_survives_the_next_diet_pass(tmp_path):
    """The toggle must stop being a lie: enable ``terminal`` after the diet ran,
    then re-run it (registration re-applies the diet on every backend start) —
    the user's choice stays put and nothing is rewritten.
    """
    from plobi.agents.registry import apply_l2_project_diet

    profile_dir = tmp_path / "l2-aura"
    _write_config(
        profile_dir,
        top=["terminal", "web", "skills"],
        platforms={"cli": ["terminal", "web", "skills"]},
    )
    assert apply_l2_project_diet(profile_dir) is True
    assert "terminal" not in _read_toolsets(profile_dir)["platform_toolsets"]["cli"]

    _simulate_ui_toggle(profile_dir, "cli", "terminal")
    assert apply_l2_project_diet(profile_dir) is False

    cfg = _read_toolsets(profile_dir)
    assert "terminal" in cfg["platform_toolsets"]["cli"]
    # 顶层基线仍是 diet 的产物——opt-in 只覆盖那一个平台列表。
    assert "terminal" not in cfg["toolsets"]


def test_user_enabled_toolset_survives_repeated_passes_and_other_strips(tmp_path):
    """The opt-in is not a one-shot exemption: it survives any number of later
    passes, while the diet keeps policing names the profile actually inherited.
    """
    from plobi.agents.registry import apply_l2_project_diet

    profile_dir = tmp_path / "l2-aura"
    _write_config(
        profile_dir,
        top=["terminal", "web", "skills"],
        platforms={"cli": ["terminal", "web", "skills"]},
    )
    assert apply_l2_project_diet(profile_dir) is True

    _simulate_ui_toggle(profile_dir, "cli", "terminal")
    _simulate_ui_toggle(profile_dir, "gateway", "terminal")
    # 顶层基线里仍然声明着的名字 = 这个 profile 继承来的，照剥；只出现在
    # 单个平台列表里的 terminal = 用户勾选，保留。
    cfg = _read_toolsets(profile_dir)
    for name in ("code_execution", "session_search"):
        cfg["toolsets"].append(name)
        cfg["platform_toolsets"]["cli"].append(name)
        cfg["platform_toolsets"]["gateway"].append(name)
    (profile_dir / "config.yaml").write_text(
        yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )

    assert apply_l2_project_diet(profile_dir) is True
    cfg = _read_toolsets(profile_dir)
    assert "terminal" in cfg["platform_toolsets"]["cli"]
    assert "terminal" in cfg["platform_toolsets"]["gateway"]
    for name in ("code_execution", "session_search"):
        assert name not in cfg["toolsets"], name
        for platform in ("cli", "gateway"):
            assert name not in cfg["platform_toolsets"][platform], (name, platform)
    # 第三遍无事可做 = idempotent，不再重写文件。
    before = (profile_dir / "config.yaml").read_text(encoding="utf-8")
    assert apply_l2_project_diet(profile_dir) is False
    assert (profile_dir / "config.yaml").read_text(encoding="utf-8") == before


def test_plobi_composite_is_never_treated_as_user_opt_in(tmp_path):
    """``plobi-*`` 复合工具集把 L2 变回工人，且面板根本写不出它
    （``_save_platform_tools`` 先丢掉 platform default toolsets），所以即便
    只出现在单个平台列表里也照剥。"""
    from plobi.agents.registry import apply_l2_project_diet

    profile_dir = tmp_path / "l2-prism"
    _write_config(
        profile_dir,
        top=["web", "skills"],
        platforms={"cli": ["plobi-cli", "web"]},
    )

    assert apply_l2_project_diet(profile_dir) is True
    cfg = _read_toolsets(profile_dir)
    assert "plobi-cli" not in cfg["platform_toolsets"]["cli"]
    assert "web" in cfg["platform_toolsets"]["cli"]


def test_diet_leaves_an_untouched_profile_file_byte_identical(tmp_path):
    """用户没碰过的干净 profile：返回 False，且文件一个字节都不动。"""
    from plobi.agents.registry import apply_l2_project_diet

    profile_dir = tmp_path / "l2-lean"
    _write_config(
        profile_dir,
        top=["web", "file", "skills", "todo", "clarify"],
        platforms={"cli": ["web", "file"], "gateway": ["web"]},
    )
    before = (profile_dir / "config.yaml").read_text(encoding="utf-8")

    assert apply_l2_project_diet(profile_dir) is False
    assert (profile_dir / "config.yaml").read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# ensure_mind_project_agents 集成
# ---------------------------------------------------------------------------


def test_ensure_mind_project_agents_diet_calls_apply_for_each_project(
    tmp_path, monkeypatch
):
    """ensure_mind_project_agents 内部对每个非 agenda / 非 butler 的 l2_project
    调一次 apply_l2_project_diet。验证：seed 一个 Plobi + Animation 项目 +
    假 profile（带 terminal），跑完两个 profile 的 config.yaml 都被剥掉。"""
    from plobi.agents import registry as registry_mod

    # 临时 home + Mind 根目录。
    home = tmp_path / "plobi_home"
    home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    monkeypatch.setenv("PLOBI_HOME", str(home))
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(home / "plobi" / "projects.yaml"))
    monkeypatch.delenv("PLOBI_MODELS_CONFIG", raising=False)

    mind_root = tmp_path / "mind"
    mind_root.mkdir()
    (mind_root / "AGENTS.md").write_text("# mind", encoding="utf-8")
    (mind_root / "Vault" / "projects").mkdir(parents=True)

    for name in ("Plobi", "Animation"):
        proj = mind_root / "Vault" / "projects" / name
        proj.mkdir()
        (proj / "plan.md").write_text(
            "---\n"
            f"project: {name}\n"
            "group: product\n"
            f"cloud: D:\\Cloud\\Projects\\{name}\n"
            "status: active\n"
            "---\n\n"
            f"# {name}\n",
            encoding="utf-8",
        )

    # 先准备两个「l2-plobi / l2-animation」假 profile，配置里塞 terminal，
    # 让 diet 必须干活才能清理。
    for name in ("Plobi", "Animation"):
        profile_dir = home / ".plobi" / "profiles" / f"l2-{name}"
        profile_dir.mkdir(parents=True)
        (profile_dir / "config.yaml").write_text(
            "toolsets:\n  - terminal\n  - web\n  - skills\n",
            encoding="utf-8",
        )
        (profile_dir / "plobi").mkdir(exist_ok=True)
        (profile_dir / "plobi" / "models.json").write_text("{}", encoding="utf-8")

    # stub plobi_cli.profiles（让 reg.spawn 走通；不真克隆）
    import sys
    import types

    fake_profiles = types.ModuleType("plobi_cli.profiles")
    fake_profiles.create_profile = lambda name, **kw: home / ".plobi" / "profiles" / name
    fake_profiles.get_profile_dir = lambda name: home / ".plobi" / "profiles" / name
    fake_profiles.profile_exists = lambda name: (home / ".plobi" / "profiles" / name).is_dir()
    monkeypatch.setitem(sys.modules, "plobi_cli.profiles", fake_profiles)

    # stub AgentRegistry.spawn 走通到返回 profile_dir。
    original_spawn = registry_mod.AgentRegistry.spawn
    def _spawn(self, name, **kw):
        # 模拟 spawn 成功，并假装 models.json 写入。
        profile_dir = fake_profiles.get_profile_dir(f"l2-{name}")
        profile_dir.mkdir(parents=True, exist_ok=True)
        (profile_dir / "plobi").mkdir(exist_ok=True)
        (profile_dir / "plobi" / "models.json").write_text("{}", encoding="utf-8")
        return {
            "name": name,
            "role": "l2_project",
            "profile": f"l2-{name}",
            "profile_dir": str(profile_dir),
            "command": f"plobi -p l2-{name} chat",
            "routing_ok": True,
        }
    monkeypatch.setattr(registry_mod.AgentRegistry, "spawn", _spawn)

    # stub mind.paths.resolve_root 指向我们的临时 Mind。
    from plobi.mind import paths as paths_mod
    monkeypatch.setattr(paths_mod, "resolve_root", lambda _=None: mind_root)

    # 跑
    reg, seeded = registry_mod.ensure_mind_project_agents()

    assert set(seeded) == {"Plobi", "Animation"}
    for name in ("Plobi", "Animation"):
        profile_dir = home / ".plobi" / "profiles" / f"l2-{name}"
        cfg = yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8"))
        assert "terminal" not in cfg.get("toolsets", []), name
        assert "web" in cfg["toolsets"], name
        assert "skills" in cfg["toolsets"], name


# ---------------------------------------------------------------------------
# agenda profile 不被误伤
# ---------------------------------------------------------------------------


def test_ensure_l2_agenda_toolsets_still_keeps_terminal(tmp_path):
    """L2-agenda 走的是 ensure_l2_agenda_toolsets——diet 不该影响它。"""
    from plobi.agents.registry import ensure_l2_agenda_toolsets

    profile_dir = tmp_path / "l2-agenda-secretary"
    _write_config(
        profile_dir,
        top=["web", "skills"],
        platforms={"cli": ["web"]},
    )

    assert ensure_l2_agenda_toolsets(profile_dir) is True
    cfg = _read_toolsets(profile_dir)
    assert "terminal" in cfg["toolsets"]


# ---------------------------------------------------------------------------
# SOUL：派工不是工人（裁定 33.2 / 33.3 在 prompt 层的硬规则）
# ---------------------------------------------------------------------------


def test_soul_block_bans_master_tools_as_entry_but_not_any_tool():
    """裁定 49 推翻 33.2 / 33.3 的**收工具**部分：SOUL 仍点名 `plobi_master_*`
    不是日程 / 项目入口，但不再宣布 L1 没有 `terminal` 或不准调用 `memory`。
    机制层（``L1_DROP_TOOLSETS`` / ``L1_MID_TOOLSETS``）是另一刀，本测试仍按
    原样钉住，见文件头。"""
    import re

    from plobi.agents.registry import L1_SOUL_BLOCK

    assert "派工不是工人" in L1_SOUL_BLOCK
    for not_an_entry_point in (
        "plobi_master_dispatch",
        "plobi_master_preview",
        "plobi_master_status",
        "plobi_master_approve",
    ):
        assert not_an_entry_point in L1_SOUL_BLOCK, not_an_entry_point
    # 主树治理（人批）留着；「L1 同样没有 terminal」那句死了。
    assert "D:\\Projects\\Plobi\\Code" in L1_SOUL_BLOCK
    assert "人批" in L1_SOUL_BLOCK
    denial_shaped = [
        clause
        for clause in re.split(r"[。；，、\n]", L1_SOUL_BLOCK)
        if any(t in clause for t in ("terminal", "memory", "computer_use"))
        and any(m in clause for m in ("没有", "不准调用", "禁止调用", "剥掉", "不可用"))
    ]
    assert denial_shaped == [], denial_shaped
    assert "工具由用户在产品里给" in L1_SOUL_BLOCK


def test_soul_block_first_action_is_plobi_secretary_ask():
    from plobi.agents.registry import L1_SOUL_BLOCK

    # 派工不是工人段：硬钉第一动作只有 plobi_secretary_ask。
    idx = L1_SOUL_BLOCK.index("派工不是工人")
    section = L1_SOUL_BLOCK[idx:]
    assert "plobi_secretary_ask" in section
    assert "第一动作" in section
