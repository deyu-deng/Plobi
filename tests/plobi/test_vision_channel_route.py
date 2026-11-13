"""WP-VISION-CHANNEL-FIX — 贴进来的图，得由这个会话正在用的那条腿来读。

11-10 实测（桌面截图）：用户贴了一张图，Agent 手上没有可用的看图通路，
回给用户的只有一句 "analysis failed"。真实链条（本机日志 20:01:43-45）：

  1. config.yaml 的 model 段只有 provider、没有 model id（用户 11-10 18:29
     自己清空的，注释写着「空着＝后端自己挑」）。
  2. 图像路由读的就是那一格 ⇒ 判成 "text" ⇒ 走「预描述成文字贴进对话」。
  3. 预描述解析视觉客户端时读的仍是那一格 ⇒ 造出一个 model 为空的客户端。
  4. 请求带着 model:"" 出去 ⇒ 网关 404 ``{"detail": "\\"no route for model ''\\""}``。
  5. 异常被 except 吞掉 ⇒ 屏上只剩 "analysis failed"，真因没了。

同一台机器上实测：会话那条腿（``workbuddy/deepseek-v4.1-flash``）**本身就能
读图**（拿真图真发一发，答对了图上的字）——网关那份 ``capabilities.vision``
全 false 的标注是错的。所以修法不是换模型，是让路由与预描述认这条腿。

这组测试钉住修好的四件事：
  - 会话当前那条腿决定路由与预描述（不再读 config 那格空值）；
  - 空模型名永远不发出去（不再有 ``no route for model ''``）；
  - 失败说真因（不再只剩一句 analysis failed）；
  - 提示只在 ``vision_analyze`` 真在这轮工具表里时才提。
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import tempfile

import pytest


# ---------------------------------------------------------------------------
# Test infrastructure
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_home(monkeypatch):
    """Temp PLOBI_HOME with config + clean credential env vars."""
    test_home = tempfile.mkdtemp(prefix="plobi_test_vision_route_")
    plobi_home = os.path.join(test_home, ".plobi")
    os.makedirs(plobi_home)
    monkeypatch.setenv("PLOBI_HOME", plobi_home)
    for k in list(os.environ.keys()):
        if k.endswith("_API_KEY") or k.endswith("_TOKEN"):
            monkeypatch.delenv(k, raising=False)
    yield plobi_home
    shutil.rmtree(test_home, ignore_errors=True)


def _write_config(home: str, text: str) -> None:
    with open(os.path.join(home, "config.yaml"), "w", encoding="utf-8") as fp:
        fp.write(text)


_DROPPED_PREFIXES = (
    "agent.auxiliary_client",
    "agent.image_routing",
    "tools.vision_tools",
    "plobi_cli.config",
)


def _fresh_modules() -> None:
    """Drop cached modules so each test reloads against the current env.

    Popping ``sys.modules`` alone leaves the *parent package* holding an
    attribute that points at the dropped object. The next
    ``import tools.vision_tools`` then builds a second module object while
    ``getattr(tools, "vision_tools")`` keeps handing out the first one. A
    ``monkeypatch.setattr("tools.vision_tools.vision_analyze_tool", ...)``
    resolves through the import system and lands on one copy, while
    ``tui_gateway.server`` importing the name inside the function can land on
    the other — the patch then does nothing and the *real* vision tool goes out
    to the network. That is why
    ``test_desktop_pre_describe_runs_on_the_session_leg`` only failed in the
    full run: ``tests/plobi/test_delegation_guard.py`` imports this module via
    its plugin before this file drops it, so a stale package attribute existed
    to be picked up. Clear the attribute along with the entry.
    """
    for name in list(sys.modules):
        if not name.startswith(_DROPPED_PREFIXES):
            continue
        dropped = sys.modules.pop(name, None)
        parent_name, _, leaf = name.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is not None and getattr(parent, leaf, None) is dropped:
            try:
                delattr(parent, leaf)
            except AttributeError:  # pragma: no cover - defensive
                pass
    importlib.invalidate_caches()


# ---------------------------------------------------------------------------
# 1. 空模型名永远不发出去
# ---------------------------------------------------------------------------


def test_empty_model_name_never_reaches_the_provider(isolated_home, monkeypatch):
    """provider 自己不填默认模型时，不得造出一个 model 为空的客户端。

    回归：11-10 那条 404 ``no route for model ''`` 就是这么来的——请求带着
    空名字出门，网关路由不了它（网关那类 provider 不替用户挑模型）。
    """
    _write_config(
        isolated_home,
        "model:\n  provider: aigw\n",
    )
    _fresh_modules()

    from unittest.mock import MagicMock

    import agent.auxiliary_client as aux
    from agent.auxiliary_client import resolve_vision_provider_client

    # 这个 provider 自己不填默认模型（模型由用户挑）⇒ 客户端会带着空名字。
    monkeypatch.setattr(
        aux, "resolve_provider_client", lambda *a, **k: (MagicMock(), "")
    )

    _provider, client, model = resolve_vision_provider_client(provider="auto")

    assert client is None, "没有模型名就不该有客户端——空名字只会被网关 404"
    assert not (model or "").strip()


def test_live_leg_model_outranks_the_empty_config_cell(isolated_home, monkeypatch):
    """调用方给的那条腿，比 config 那格空值更该作数。

    回归：config 的 model 段被清空（用户自己清的，"空着＝后端自己挑"），
    而会话真正在用的腿是网关挑出来的那条——路由与预描述都得按它算。
    """
    _write_config(
        isolated_home,
        "model:\n  provider: deepseek\n",
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    # 这条腿的能力未知（models.dev 里没有这条 provider）⇒ 不该被当成只认字。
    monkeypatch.setattr(
        "agent.image_routing._lookup_supports_vision", lambda *a, **k: None
    )
    _fresh_modules()
    monkeypatch.setattr(
        "agent.image_routing._lookup_supports_vision", lambda *a, **k: None
    )

    from agent.auxiliary_client import resolve_vision_provider_client

    _provider, client, model = resolve_vision_provider_client(
        provider="auto", model="workbuddy/deepseek-v4.1-flash"
    )

    assert client is not None
    assert model == "workbuddy/deepseek-v4.1-flash"


def test_known_text_only_leg_is_not_reachable(isolated_home, monkeypatch):
    """已知只认字的腿不算「可达」——守住 31179 那条既有判据。

    回归护栏：把"可达"放宽成"造得出客户端"会把只认字的腿也算进来，
    那条腿收到图只会换来一个看不懂的 provider 错误。
    """
    _write_config(
        isolated_home,
        "model:\n  provider: deepseek\n  default: deepseek-v4-pro\n",
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    _fresh_modules()

    from agent.auxiliary_client import vision_provider_reachable

    assert vision_provider_reachable() is False


# ---------------------------------------------------------------------------
# 2. 桌面那条路：会话腿 → 预描述；失败说真因；提示不提没开的工具
# ---------------------------------------------------------------------------


def _png(tmp_path, name="shot.png") -> str:
    path = tmp_path / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    return str(path)


def test_desktop_pre_describe_runs_on_the_session_leg(tmp_path, monkeypatch):
    """桌面那张图必须由会话当前那条腿来读。"""
    from tui_gateway import server

    seen: dict = {}

    async def fake_vision(image_url, user_prompt, model=None, task_id=None, provider=None):
        seen["provider"] = provider
        seen["model"] = model
        return json.dumps({"success": True, "analysis": "a blue rectangle"})

    monkeypatch.setattr("tools.vision_tools.vision_analyze_tool", fake_vision)

    out = server._enrich_with_attached_images(
        "看这个",
        [_png(tmp_path)],
        provider="aigw",
        model="workbuddy/deepseek-v4.1-flash",
    )

    assert seen["provider"] == "aigw"
    assert seen["model"] == "workbuddy/deepseek-v4.1-flash"
    assert "a blue rectangle" in out


def test_pre_describe_failure_carries_the_real_cause(tmp_path, monkeypatch):
    """失败要说真因，不是一句 analysis failed。"""
    from tui_gateway import server

    async def fake_vision(image_url, user_prompt, model=None, task_id=None, provider=None):
        return json.dumps(
            {
                "success": False,
                "error": "404 no route for model ''",
                "analysis": "There was a problem with the request.",
            }
        )

    monkeypatch.setattr("tools.vision_tools.vision_analyze_tool", fake_vision)

    out = server._enrich_with_attached_images(
        "看这个",
        [_png(tmp_path)],
        provider="aigw",
        model="workbuddy/deepseek-v4.1-flash",
    )

    assert "no route for model" in out
    assert "analysis failed" not in out


def test_pre_describe_exception_carries_the_real_cause(tmp_path, monkeypatch):
    """抛异常时也要说真因（异常类型 + 消息）。"""
    from tui_gateway import server

    async def fake_vision(image_url, user_prompt, model=None, task_id=None, provider=None):
        raise RuntimeError("gateway said no")

    monkeypatch.setattr("tools.vision_tools.vision_analyze_tool", fake_vision)

    out = server._enrich_with_attached_images(
        "看这个",
        [_png(tmp_path)],
        provider="aigw",
        model="workbuddy/deepseek-v4.1-flash",
    )

    assert "gateway said no" in out
    assert "RuntimeError" in out


def test_hint_names_the_tool_only_when_it_is_in_the_table(tmp_path, monkeypatch):
    """提示只在 vision_analyze 真在这轮工具表里时才提它。"""
    from tui_gateway import server

    async def fake_vision(image_url, user_prompt, model=None, task_id=None, provider=None):
        return json.dumps({"success": True, "analysis": "a blue rectangle"})

    monkeypatch.setattr("tools.vision_tools.vision_analyze_tool", fake_vision)

    without_tool = server._enrich_with_attached_images(
        "看这个",
        [_png(tmp_path)],
        provider="aigw",
        model="workbuddy/deepseek-v4.1-flash",
        vision_tool_available=False,
    )
    with_tool = server._enrich_with_attached_images(
        "看这个",
        [_png(tmp_path)],
        provider="aigw",
        model="workbuddy/deepseek-v4.1-flash",
        vision_tool_available=True,
    )

    assert "vision_analyze" not in without_tool
    assert "vision_analyze" in with_tool


def test_session_tool_names_reads_the_agents_own_table():
    """提示的依据是 Agent 自己那张表，不是配置里写着什么。"""

    class _Agent:
        tools = [{"function": {"name": "web_search"}}, {"function": {"name": "vision_analyze"}}]

    class _AgentNoVision:
        tools = [{"function": {"name": "web_search"}}]

    from tui_gateway import server

    assert "vision_analyze" in server._session_tool_names(_Agent())
    assert "vision_analyze" not in server._session_tool_names(_AgentNoVision())


# ---------------------------------------------------------------------------
# 3. 「有路」与「现在能调」是两回事
# ---------------------------------------------------------------------------


def test_vision_tool_available_follows_the_real_table(monkeypatch):
    """``vision_tool_available`` 问的是那张表，不是配置。"""
    import model_tools
    from tools import vision_tools

    monkeypatch.setattr(
        model_tools,
        "get_tool_definitions",
        lambda **k: [{"function": {"name": "vision_analyze"}}],
    )
    assert vision_tools.vision_tool_available() is True

    monkeypatch.setattr(
        model_tools,
        "get_tool_definitions",
        lambda **k: [{"function": {"name": "web_search"}}],
    )
    assert vision_tools.vision_tool_available() is False


def test_check_vision_counts_a_reachable_provider(monkeypatch):
    """名字还不知道、但有路可走 ⇒ 工具算可用（别让 Vision 开关变成假开关）。"""
    import agent.auxiliary_client as aux
    from tools import vision_tools

    monkeypatch.setattr(
        aux, "resolve_vision_provider_client", lambda *a, **k: ("aigw", None, None)
    )
    monkeypatch.setattr(aux, "vision_provider_reachable", lambda: True)
    assert vision_tools.check_vision_requirements() is True

    monkeypatch.setattr(aux, "vision_provider_reachable", lambda: False)
    assert vision_tools.check_vision_requirements() is False
