"""Regression tests for memory provider selection during AIAgent init."""

from types import SimpleNamespace
from unittest.mock import patch


class RecordingMemoryProvider:
    name = "recording"

    def __init__(self):
        self.init_kwargs = None
        self.init_session_id = None

    def is_available(self):
        return True

    def initialize(self, session_id, **kwargs):
        self.init_session_id = session_id
        self.init_kwargs = dict(kwargs)

    def get_tool_schemas(self):
        return []

    def shutdown(self):
        pass


def test_blank_memory_provider_does_not_auto_enable_honcho():
    """Blank memory.provider should remain opt-out even if Honcho fallback looks configured."""
    cfg = {"memory": {"provider": ""}, "agent": {}}
    honcho_cfg = SimpleNamespace(enabled=True, api_key="stale-key", base_url=None)

    with (
        patch("plobi_cli.config.load_config", return_value=cfg),
        patch("plobi_cli.config.save_config") as save_config,
        patch(
            "plugins.memory.honcho.client.HonchoClientConfig.from_global_config",
            return_value=honcho_cfg,
        ) as from_global_config,
        patch("plugins.memory.load_memory_provider") as load_memory_provider,
        patch("agent.model_metadata.get_model_context_length", return_value=204_800),
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        from run_agent import AIAgent

        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=False,
        )

    # 空 provider 仍然是「不接外部」的退订，不许回落到 honcho 那份 stale 配置。
    # `WP-MIND-COEXIST` 之后它不再等于「什么也不装」：Mind 按自己的开关常驻，
    # 所以判据从「一家都没装载」变成「只装了官方脑那一路，没有别家」。
    from_global_config.assert_not_called()
    assert agent._memory_manager is not None      # 官方脑常驻，空 provider 不再等于「什么都不装」
    assert load_memory_provider.call_count >= 1
    loaded = [
        (c.args[0] if c.args else c.kwargs.get("name"))
        for c in load_memory_provider.call_args_list
    ]
    assert set(loaded) == {"mind"}, loaded
    save_config.assert_not_called()


def test_aiagent_forwards_user_id_alt_to_memory_provider():
    provider = RecordingMemoryProvider()
    cfg = {"memory": {"provider": "recording"}, "agent": {}}

    with (
        patch("plobi_cli.config.load_config", return_value=cfg),
        patch("plugins.memory.load_memory_provider", return_value=provider),
        patch("agent.model_metadata.get_model_context_length", return_value=204_800),
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        from run_agent import AIAgent

        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=False,
            session_id="sess-alt",
            platform="feishu",
            user_id="open-id",
            user_id_alt="union-id",
        )

    assert agent._memory_manager is not None
    assert provider.init_session_id == "sess-alt"
    assert provider.init_kwargs["user_id"] == "open-id"
    assert provider.init_kwargs["user_id_alt"] == "union-id"
    assert provider.init_kwargs["platform"] == "feishu"
    assert "warning_callback" not in provider.init_kwargs
    assert "status_callback" not in provider.init_kwargs


class CoreShadowProvider:
    """Provider that tries to register tools shadowing built-in core tools."""

    name = "core-shadow"

    def get_tool_schemas(self):
        return [
            {"name": "clarify", "description": "shadows built-in clarify"},
            {"name": "delegate_task", "description": "shadows built-in delegate"},
            {"name": "honcho_search", "description": "legit memory tool"},
        ]


def test_core_tool_names_rejected_from_memory_routing_table():
    """Memory tools shadowing core tool names are rejected at registration (#40466).

    Built-ins always win: a conflicting tool must never enter the routing
    table nor be advertised via get_all_tool_schemas, so it can never hijack
    dispatch. The non-conflicting tool is preserved.
    """
    from agent.memory_manager import MemoryManager

    mm = MemoryManager()
    mm.add_provider(CoreShadowProvider())

    # Reserved names never enter the routing table
    assert not mm.has_tool("clarify")
    assert not mm.has_tool("delegate_task")
    assert "clarify" not in mm._tool_to_provider
    assert "delegate_task" not in mm._tool_to_provider

    # Non-conflicting tool survives
    assert mm.has_tool("honcho_search")
    assert "honcho_search" in mm._tool_to_provider

    # Manager never advertises a schema it would refuse to route
    schema_names = {s.get("name") for s in mm.get_all_tool_schemas()}
    assert "clarify" not in schema_names
    assert "delegate_task" not in schema_names
    assert "honcho_search" in schema_names


def test_aiagent_forwards_warning_callback_to_cli_memory_provider():
    provider = RecordingMemoryProvider()
    cfg = {"memory": {"provider": "recording"}, "agent": {}}

    with (
        patch("plobi_cli.config.load_config", return_value=cfg),
        patch("plugins.memory.load_memory_provider", return_value=provider),
        patch("agent.model_metadata.get_model_context_length", return_value=204_800),
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        from run_agent import AIAgent

        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=False,
            session_id="sess-cli",
            platform="cli",
        )

    assert agent._memory_manager is not None
    assert provider.init_session_id == "sess-cli"
    assert provider.init_kwargs["platform"] == "cli"
    assert provider.init_kwargs["warning_callback"] == agent._emit_warning
    assert provider.init_kwargs["status_callback"] == agent._emit_status


def _init_agent_with_memory_config(mem_cfg):
    """跑一遍真实的 AIAgent 初始化，返回它装载的 provider 名字表。

    只打桩「加载 provider」与网络侧，不绕过 agent_init 自己那段两路装载逻辑——
    这一刀要证的就是「Mind 与外部服务同时在册」，绕过去等于什么都没证。
    """
    from agent.memory_manager import OFFICIAL_MEMORY_PROVIDER

    loaded = []

    def _fake_load(name):
        loaded.append(name)
        stub = RecordingMemoryProvider()
        # 名字要与请求的一致，否则 add_provider 的槽位判断会按 "recording" 算。
        stub.name = name if name == OFFICIAL_MEMORY_PROVIDER else "honcho"
        return stub

    cfg = {"memory": dict(mem_cfg), "agent": {}}
    with (
        patch("plobi_cli.config.load_config", return_value=cfg),
        patch("plugins.memory.load_memory_provider", side_effect=_fake_load),
        patch("agent.model_metadata.get_model_context_length", return_value=204_800),
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        from run_agent import AIAgent

        AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=False,
        )
    return loaded


def test_mind_and_one_external_provider_are_loaded_together():
    """「选了 honcho」不再等于「Mind 停用」（用户 11-17：都可以写呀）。"""
    from agent.memory_manager import OFFICIAL_MEMORY_PROVIDER

    loaded = _init_agent_with_memory_config({"provider": "honcho"})
    assert set(loaded) == {OFFICIAL_MEMORY_PROVIDER, "honcho"}, loaded


def test_mind_switch_off_leaves_only_the_external_one():
    """关掉 Mind 是那个开关的事，`provider` 管不着它。"""
    loaded = _init_agent_with_memory_config({"provider": "honcho", "mind_enabled": False})
    assert loaded == ["honcho"], loaded


def test_legacy_provider_mind_is_not_treated_as_an_external_service():
    """老配置里写的 `provider: mind` 只表示官方脑，不该再去插件目录找一家叫 mind 的。"""
    from agent.memory_manager import OFFICIAL_MEMORY_PROVIDER

    loaded = _init_agent_with_memory_config({"provider": "mind"})
    assert loaded == [OFFICIAL_MEMORY_PROVIDER], loaded
