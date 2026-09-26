"""WP-L2-DIET — L1 中收去掉 memory；项目 L2 减肥到没有 terminal / code runner。

裁定 33.2（L1）：``memory`` 工具不再出现在 L1 中收；活动 default 的
toolsets 跑一遍 ``mid_narrow_toolset_names`` 之后只剩 web / file /
skills / todo / clarify / delegation / plobi_north_star。

裁定 33.3（项目 L2）：每个 Mind 项目 L2 的 profile config.yaml 必须没
有 ``terminal`` / ``computer_use`` / ``code_execution`` / ``session_search``，
且任何 ``hermes-*`` 复合工具集都被剥掉。L2-agenda 不走这条——管家自己
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


def test_mid_narrow_drops_hermes_cli_composite():
    from plobi.agents.registry import mid_narrow_toolset_names

    out = mid_narrow_toolset_names(["hermes-cli", "web"])
    assert "hermes-cli" not in out
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

    profile_dir = tmp_path / "l2-Plobi"
    _write_config(
        profile_dir,
        top=["terminal", "computer_use", "web", "skills", "code_execution", "session_search", "clarify"],
        platforms={"cli": ["terminal", "web", "session_search"], "gateway": ["hermes-cli", "code_execution"]},
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
    assert "hermes-cli" not in cfg["platform_toolsets"]["gateway"]
    assert "code_execution" not in cfg["platform_toolsets"]["gateway"]


def test_apply_l2_project_diet_strips_hermes_composite_toolsets(tmp_path):
    from plobi.agents.registry import apply_l2_project_diet

    profile_dir = tmp_path / "l2-Animation"
    _write_config(
        profile_dir,
        top=["hermes-cli", "hermes-cli-gateway", "web"],
    )

    assert apply_l2_project_diet(profile_dir) is True

    cfg = _read_toolsets(profile_dir)
    assert "hermes-cli" not in cfg["toolsets"]
    assert "hermes-cli-gateway" not in cfg["toolsets"]
    assert "web" in cfg["toolsets"]


def test_apply_l2_project_diet_preserves_allowed_toolsets(tmp_path):
    from plobi.agents.registry import apply_l2_project_diet

    profile_dir = tmp_path / "l2-Nuclide"
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

    profile_dir = tmp_path / "l2-Prism"
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

    profile_dir = tmp_path / "l2-Stithy"
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
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    monkeypatch.setenv("HERMES_HOME", str(home))
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

    # 先准备两个「l2-Plobi / l2-Animation」假 profile，配置里塞 terminal，
    # 让 diet 必须干活才能清理。
    for name in ("Plobi", "Animation"):
        profile_dir = home / ".hermes" / "profiles" / f"l2-{name}"
        profile_dir.mkdir(parents=True)
        (profile_dir / "config.yaml").write_text(
            "toolsets:\n  - terminal\n  - web\n  - skills\n",
            encoding="utf-8",
        )
        (profile_dir / "plobi").mkdir(exist_ok=True)
        (profile_dir / "plobi" / "models.json").write_text("{}", encoding="utf-8")

    # stub hermes_cli.profiles（让 reg.spawn 走通；不真克隆）
    import sys
    import types

    fake_profiles = types.ModuleType("hermes_cli.profiles")
    fake_profiles.create_profile = lambda name, **kw: home / ".hermes" / "profiles" / name
    fake_profiles.get_profile_dir = lambda name: home / ".hermes" / "profiles" / name
    fake_profiles.profile_exists = lambda name: (home / ".hermes" / "profiles" / name).is_dir()
    monkeypatch.setitem(sys.modules, "hermes_cli.profiles", fake_profiles)

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
            "command": f"hermes -p l2-{name} chat",
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
        profile_dir = home / ".hermes" / "profiles" / f"l2-{name}"
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


def test_soul_block_mentions_no_memory_tool_and_no_master_dispatch():
    from plobi.agents.registry import L1_SOUL_BLOCK

    assert "派工不是工人" in L1_SOUL_BLOCK
    # memory 工具被禁。
    assert "memory" in L1_SOUL_BLOCK
    # plobi_master_dispatch / preview / status / approve 都被点名禁止。
    for forbidden in (
        "plobi_master_dispatch",
        "plobi_master_preview",
        "plobi_master_status",
        "plobi_master_approve",
    ):
        assert forbidden in L1_SOUL_BLOCK, forbidden
    # 不准对项目主树开 terminal。
    assert "D:\\Projects\\Plobi\\Code" in L1_SOUL_BLOCK
    assert "terminal" in L1_SOUL_BLOCK


def test_soul_block_first_action_is_plobi_secretary_ask():
    from plobi.agents.registry import L1_SOUL_BLOCK

    # 派工不是工人段：硬钉第一动作只有 plobi_secretary_ask。
    idx = L1_SOUL_BLOCK.index("派工不是工人")
    section = L1_SOUL_BLOCK[idx:]
    assert "plobi_secretary_ask" in section
    assert "第一动作" in section
