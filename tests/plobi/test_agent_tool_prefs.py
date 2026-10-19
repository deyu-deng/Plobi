"""全局真源 + per-agent 工具开关（2026-10-19 用户裁定，WP-AGENT-CONFIG-SINGLE-SOURCE 续刀）。

判据三条：
1. 技能 / 工具集 / MCP 服务器只有一张全局表；名册行三份黑名单只记
   「这个 Agent 关掉了哪些」——是偏好不是拷贝。
2. 消费点做并集减法：全局 disabled ∪ 当前行黑名单。分身会话（PLOBI_HOME
   钉在 profile 家）才生效；顶层家（L1 / flat）名册查无此行 = 原样。
3. Agent 会话里选模型持久化到**名册行覆盖**（这个 Agent 记住自己的选择），
   绝不写共享 config 的 ``model.default``（那会把一个 Agent 的选择刷成全员默认）。
"""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import plobi_cli.config as cfg


@pytest.fixture
def root(tmp_path, monkeypatch):
    """临时顶层家：一份 config + 一份名册 + 两个分身家。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    base = tmp_path / ".plobi"
    (base / "plobi").mkdir(parents=True)
    for name in ("l2-aura", "l2-kit"):
        (base / "profiles" / name).mkdir(parents=True)
    (base / "config.yaml").write_text(
        "model:\n"
        "  provider: aigw\n"
        "  default: workbuddy/configured-once\n"
        "skills:\n"
        "  disabled: [global-off-skill]\n"
        "agent:\n"
        "  disabled_toolsets: [global-off-toolset]\n"
        "mcp_servers:\n"
        "  shared-server:\n"
        "    command: echo\n"
        "    args: []\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PLOBI_HOME", str(base))
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(base / "plobi" / "projects.yaml"))
    _write_roster(
        base,
        {
            "aura": _row(
                "l2-aura",
                disabled_skills=["aura-no-skill"],
                disabled_toolsets=["browser"],
                disabled_mcp_servers=["shared-server"],
            ),
            "kit": _row("l2-kit"),
        },
    )
    _clear()
    yield base
    _clear()


def _clear() -> None:
    cfg._LOAD_CONFIG_CACHE.clear()
    cfg._RAW_CONFIG_CACHE.clear()
    cfg._LAST_EXPANDED_CONFIG_BY_PATH.clear()


def _write_roster(base: Path, rows: dict) -> None:
    from plobi.agents.registry import REGISTRY_VERSION

    Path(os.environ["PLOBI_PROJECTS_CONFIG"]).write_text(
        yaml.safe_dump(
            {"version": REGISTRY_VERSION, "agents": rows}, sort_keys=False, allow_unicode=True
        ),
        encoding="utf-8",
    )


def _row(profile: str, **extra) -> dict:
    base = {"name": profile, "role": "l2_project", "profile": profile, "category": "projects"}
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# 1. 名册行三份黑名单的存取
# ---------------------------------------------------------------------------


def test_agent_entry_roundtrips_tool_blacklists(root):
    from plobi.agents.registry import AgentRegistry

    registry = AgentRegistry.load()
    entry = registry.get("aura")
    assert entry.disabled_skills == ("aura-no-skill",)
    assert entry.disabled_toolsets == ("browser",)
    assert entry.disabled_mcp_servers == ("shared-server",)

    data = entry.to_dict()
    assert data["disabled_skills"] == ["aura-no-skill"]
    assert data["disabled_toolsets"] == ["browser"]
    assert data["disabled_mcp_servers"] == ["shared-server"]

    # 一行没声明 = 空元组（不是 None），旧名册零迁移可读。
    kit = registry.get("kit")
    assert kit.disabled_skills == ()
    assert "disabled_skills" not in kit.to_dict()


# ---------------------------------------------------------------------------
# 2. 消费点并集减法：分身生效、顶层不生效
# ---------------------------------------------------------------------------


def test_agent_disabled_sets_resolves_by_active_home(root):
    from plobi.agents.registry import agent_disabled_sets

    # 分身会话：PLOBI_HOME 钉在 profile 家。
    os.environ["PLOBI_HOME"] = str(root / "profiles" / "l2-aura")
    _clear()
    sets = agent_disabled_sets()
    assert sets["skills"] == {"aura-no-skill"}
    assert sets["toolsets"] == {"browser"}
    assert sets["mcp_servers"] == {"shared-server"}

    # 顶层家（L1 / flat）：没有 per-agent 偏好。
    os.environ["PLOBI_HOME"] = str(root)
    _clear()
    assert agent_disabled_sets() == {"skills": set(), "toolsets": set(), "mcp_servers": set()}

    # 分身家但名册查无此行：全空，不猜。
    os.environ["PLOBI_HOME"] = str(root / "profiles" / "l2-kit")
    _clear()
    assert agent_disabled_sets() == {"skills": set(), "toolsets": set(), "mcp_servers": set()}


def test_skill_disabled_names_union_agent_row(root):
    from agent.skill_utils import get_disabled_skill_names

    os.environ["PLOBI_HOME"] = str(root / "profiles" / "l2-aura")
    _clear()
    # 全局表仍在（global-off-skill），名册行只做加法（aura-no-skill）。
    assert get_disabled_skill_names() == {"global-off-skill", "aura-no-skill"}

    os.environ["PLOBI_HOME"] = str(root)
    _clear()
    assert get_disabled_skill_names() == {"global-off-skill"}


def test_mcp_config_load_filters_agent_disabled_servers(root, monkeypatch):
    from tools.mcp_tool import _load_mcp_config

    monkeypatch.setenv("PLOBI_SAFE_MODE", "")
    os.environ["PLOBI_HOME"] = str(root / "profiles" / "l2-aura")
    _clear()
    servers = _load_mcp_config()
    assert "shared-server" not in servers

    os.environ["PLOBI_HOME"] = str(root)
    _clear()
    assert "shared-server" in _load_mcp_config()


# ---------------------------------------------------------------------------
# 3. 模型持久化改道：Agent 会话写名册行，全局会话写共享 config
# ---------------------------------------------------------------------------


def _switch_result(provider="aigw", model="workbuddy/picked"):
    return SimpleNamespace(target_provider=provider, new_model=model, base_url="")


def test_gateway_model_persist_routes_to_roster_for_agent_session(root, monkeypatch):
    import tui_gateway.server as server

    written = {}
    monkeypatch.setattr(
        "plobi.agents.registry.set_profile_model_override",
        lambda profile, provider, model: written.setdefault(
            "roster", (profile, provider, model)
        ),
    )
    monkeypatch.setattr(
        "cli.save_config_value",
        lambda key, value: written.setdefault("global", (key, value)),
    )

    session = {"profile_home": str(root / "profiles" / "l2-aura")}
    server._persist_model_switch(_switch_result(), session)

    # 记在名册行：这个 Agent 记住自己的选择。
    assert written.get("roster") == ("l2-aura", "aigw", "workbuddy/picked")
    # 共享 config 的全局默认没被一个分身的选择刷掉。
    assert "global" not in written


def test_gateway_model_persist_keeps_global_write_for_flat_session(root, monkeypatch):
    import tui_gateway.server as server

    written = {}
    monkeypatch.setattr(
        "plobi.agents.registry.set_profile_model_override",
        lambda profile, provider, model: written.setdefault(
            "roster", (profile, provider, model)
        ),
    )
    monkeypatch.setattr(
        "cli.save_config_value",
        lambda key, value: written.setdefault("global", (key, value)),
    )

    server._persist_model_switch(_switch_result(), {"profile_home": None})
    assert "roster" not in written
    assert written["global"] == ("model.default", "workbuddy/picked")


def test_gateway_model_persist_failure_never_falls_back_to_global(root, monkeypatch):
    """名册写失败只降级为会话级（model_override 仍钉着），不回落写全局。"""
    import tui_gateway.server as server

    from plobi.agents.registry import RegistryError

    def refuse(profile, provider, model):
        raise RegistryError("registry read-only")

    monkeypatch.setattr("plobi.agents.registry.set_profile_model_override", refuse)
    written = {}
    monkeypatch.setattr(
        "cli.save_config_value",
        lambda key, value: written.setdefault("global", (key, value)),
    )

    session = {"profile_home": str(root / "profiles" / "l2-aura")}
    server._persist_model_switch(_switch_result(), session)  # 不应抛出
    assert "global" not in written
