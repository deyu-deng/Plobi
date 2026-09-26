"""WP-L1-PROMPT-LIVE / 裁定 40：长寿命 L1 会话必须把旧系统提示当作过期。

问题：``_stored_prompt_matches_runtime`` 只比 Model / Provider 两行——
身份段变了（WP-L1-IDENTITY）也照样命中缓存复用。后果：20260905_155453_fb3b77
那个老 session 至今背着 9-5 的 Nous / 通用助手人格，永远进不去新身份。

本刀：判定式新增「L1 default + 持久提示缺「Plobi 总秘书」marker」→ 算
过期。L2 / 非 default profile 不动（不动 L2 的合法标记）。早退路径触
发 ``_build_system_prompt`` 重建——付出一次 prefix-cache miss，付。
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_conversation_loop():
    """``agent/conversation_loop.py`` — direct spec_from_file_location 装载。

    Real ``hermes_constants`` / ``hermes_cli`` live on sys.path in this venv
    (the codebase depends on them); we don't stub either — the import chain
    pulls in real, which has every symbol needed.
    """
    name = "agent.conversation_loop"
    if name in sys.modules:
        return sys.modules[name]

    cl_path = (
        Path(__file__).resolve().parents[2] / "agent" / "conversation_loop.py"
    )
    spec = importlib.util.spec_from_file_location(name, cl_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

    cl_path = (
        Path(__file__).resolve().parents[2] / "agent" / "conversation_loop.py"
    )
    spec = importlib.util.spec_from_file_location(name, cl_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_l1_budget():
    """``plugins/plobi-north-star/l1_budget.py`` —— 拿真实的 ``is_l1_default_profile``。"""
    name = "hermes_plugins.plobi_north_star.l1_budget"
    if name in sys.modules:
        return sys.modules[name]
    if "hermes_plugins" not in sys.modules:
        ns = types.ModuleType("hermes_plugins")
        ns.__path__ = []
        sys.modules["hermes_plugins"] = ns
    parent_name = "hermes_plugins.plobi_north_star"
    if parent_name not in sys.modules:
        parent_mod = types.ModuleType(parent_name)
        parent_mod.__path__ = [
            str(
                Path(__file__).resolve().parents[2]
                / "plugins" / "plobi-north-star"
            )
        ]
        parent_mod.__package__ = "hermes_plugins"
        sys.modules[parent_name] = parent_mod
    l1_path = (
        Path(__file__).resolve().parents[2]
        / "plugins" / "plobi-north-star" / "l1_budget.py"
    )
    spec = importlib.util.spec_from_file_location(name, l1_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = parent_name
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cl():
    return _load_conversation_loop()


@pytest.fixture(scope="module")
def l1_budget():
    return _load_l1_budget()


# ─── helper ────────────────────────────────────────────────────────────────


class _Agent:
    """Bare-bones stand-in for ``AIAgent`` — only the attrs
    ``_stored_prompt_matches_runtime`` reads."""

    def __init__(
        self,
        *,
        model: str = "deepseek-chat",
        provider: str = "aigw",
        profile: str = "default",
    ):
        self.model = model
        self.provider = provider
        self.profile = profile
        self.profile_name = profile
        self.enabled_toolsets = set()


def _make_prompt(model: str, provider: str, body: str) -> str:
    """Build a stored system prompt with the same Model/Provider prefix
    ``_stored_prompt_matches_runtime`` greps for."""
    return f"Model: {model}\nProvider: {provider}\n{body}"


# ─── core: identity drift detection ────────────────────────────────────────


def test_l1_old_prompt_without_marker_is_stale(cl, monkeypatch):
    """L1 + 老提示（缺「Plobi 总秘书」）→ matcher False，触发重建。"""
    # Force the lazy is_l1_default check to return True for the stub agent.
    cl._l1_default_check_cache["impl"] = lambda a: True
    agent = _Agent()
    old_prompt = _make_prompt(
        "deepseek-chat",
        "aigw",
        "You are a helpful assistant from Nous Research.\n"
        "# 你是 Nous Research 的一个 AI 助手。\n",
    )
    assert cl._stored_prompt_matches_runtime(agent, old_prompt) is False


def test_l1_new_prompt_with_marker_and_same_model_matches(cl, monkeypatch):
    """L1 + 含 marker + Model 一致 → matcher True（继续复用，避免每回合 miss cache）。"""
    cl._l1_default_check_cache["impl"] = lambda a: True
    agent = _Agent()
    prompt = _make_prompt(
        "deepseek-chat",
        "aigw",
        "你是本机 Plobi 总秘书（L1）——不是 Nous / MiniMax / 通用 chatbot。",
    )
    assert cl._stored_prompt_matches_runtime(agent, prompt) is True


def test_l2_old_prompt_does_not_force_rebuild(cl, monkeypatch):
    """L2 / 非 default profile 不进身份规则——身份段是 L1 的特权。"""
    cl._l1_default_check_cache["impl"] = lambda a: False
    agent = _Agent(profile="l2-Plobi")
    # 老提示、Model 一致——按旧逻辑（仅 Model/Provider）应该 match；现在
    # 也仍 match（L2 不被身份规则打成 False）。
    prompt = _make_prompt(
        "deepseek-chat",
        "aigw",
        "You are a project L2. No Plobi 总秘书 marker here.",
    )
    assert cl._stored_prompt_matches_runtime(agent, prompt) is True


def test_l2_with_marker_is_also_a_match(cl, monkeypatch):
    """L2 偶然带了 marker 也无妨——matcher 仍 True。"""
    cl._l1_default_check_cache["impl"] = lambda a: False
    agent = _Agent(profile="l2-Plobi")
    prompt = _make_prompt(
        "deepseek-chat",
        "aigw",
        "你说 Plobi 总秘书，因为项目 L2 也强调这个。",
    )
    assert cl._stored_prompt_matches_runtime(agent, prompt) is True


def test_model_rotation_still_trips_for_l1(cl, monkeypatch):
    """身份规则不能废 Model / Provider 检查——rotation 必须独立打 False。"""
    cl._l1_default_check_cache["impl"] = lambda a: True
    agent = _Agent(model="deepseek-chat")
    rotated = _Agent(model="kimi-k3")
    prompt = _make_prompt(
        "deepseek-chat",
        "aigw",
        "你是本机 Plobi 总秘书（L1）——不是 Nous。",
    )
    # Model 一致 → True；Model 旋了 → False（与 marker 无关）。
    assert cl._stored_prompt_matches_runtime(agent, prompt) is True
    assert cl._stored_prompt_matches_runtime(rotated, prompt) is False


def test_provider_rotation_still_trips_for_l1(cl, monkeypatch):
    """Provider 字段单独旋转也必须打 False。"""
    cl._l1_default_check_cache["impl"] = lambda a: True
    prompt = _make_prompt(
        "deepseek-chat",
        "aigw",
        "你是本机 Plobi 总秘书（L1）——不是 Nous。",
    )
    agent_aigw = _Agent(provider="aigw")
    agent_openai = _Agent(provider="openai")
    assert cl._stored_prompt_matches_runtime(agent_aigw, prompt) is True
    assert cl._stored_prompt_matches_runtime(agent_openai, prompt) is False


def test_blank_prompt_returns_truthy_to_skip_rebuild(cl, monkeypatch):
    """空 prompt：调用方已用 ``if stored_prompt and ...`` 守卫，本函数不会
    被空 prompt 触发早退。所以这里只验「空 prompt 不会让本函数自己判 stale」
    ——它返回 True 不造成语义错（因为外层 if 不进）。Production 路径不变。

    关键点是 _l1_identity_is_stale 的 defensive ``if not prompt`` 必须短路——
    不让本函数对空 prompt 抛异常或返回 False 然后被 caller 误判。"""
    cl._l1_default_check_cache["impl"] = lambda a: True
    # Empty prompt: 不调用 is_l1_default_profile（defensive 短路）。
    # Returns True (no model/provider mismatch, no identity drift to detect).
    assert cl._stored_prompt_matches_runtime(_Agent(), "") is True


# ─── marker constant ───────────────────────────────────────────────────────


def test_marker_constant_matches_plobi_total_secretary():
    """``_L1_IDENTITY_MARKER`` 必须与 SOUL 身份段一字不差——这是 token，不是
    regex。SOUL 改了，这边要同步改。"""
    cl = _load_conversation_loop()
    assert cl._L1_IDENTITY_MARKER == "Plobi 总秘书"


def test_marker_present_in_l1_soul_block():
    """SOUL 身份段必须含 marker；测试这一对关系保证两边同步。"""
    cl = _load_conversation_loop()
    from plobi.agents.registry import L1_SOUL_BLOCK

    assert cl._L1_IDENTITY_MARKER in L1_SOUL_BLOCK


# ─── integration: real is_l1_default_profile path ───────────────────────────


def test_real_l1_default_profile_triggers_stale_for_old_prompt(cl, l1_budget):
    """真实 ``is_l1_default_profile``（从 ``l1_budget`` 加载）→ 老提示标
    记 stale；不是 mock。"""
    # 清掉 cache 让真实 is_l1_default_profile 走一遍。
    cl._l1_default_check_cache["impl"] = None

    # Agent with default profile + plobi_north_star toolset → 真 L1。
    agent = _Agent(profile="default")
    agent.enabled_toolsets = {"plobi_north_star"}

    old_prompt = _make_prompt(
        "deepseek-chat",
        "aigw",
        "You are Nous Research's helpful assistant.\n",
    )
    assert cl._stored_prompt_matches_runtime(agent, old_prompt) is False


def test_real_l2_agent_is_not_affected_by_identity_rule(cl, l1_budget):
    """真实 ``is_l1_default_profile``（从 ``l1_budget`` 加载）→ L2 agent
    不被身份规则强迫重建。"""
    cl._l1_default_check_cache["impl"] = None

    # L2 agent — profile 不是 default。
    agent = _Agent(profile="l2-Prism")
    agent.enabled_toolsets = {"plobi_north_star"}  # 不影响 L1 判定

    old_prompt = _make_prompt(
        "deepseek-chat",
        "aigw",
        "You are the L2 assistant for Prism.",
    )
    assert cl._stored_prompt_matches_runtime(agent, old_prompt) is True
