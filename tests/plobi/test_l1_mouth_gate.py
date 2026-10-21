"""裁定 183 之后嘴的门：两枚原子 + 深工具各归各的门（WP-L1-MOUTH-SWITCH 的续命）。

这个文件原本钉的是旧世界（WP-L1-MOUTH-SWITCH，裁定 78/49/P7）：五张派工嘴由产品
开关决定、秘书两嘴不受影响、裁定 33.4「handler/schema 一律不删」。裁定 183 把
带 intent/action 枚举的那 7 枚全删了（用户 10-19 原话：「问话和派工或者别的不都是
一件事情吗？就是一个 Agent 给另一个 Agent 发了一段提示词吗？」），嘴收成两枚原子
``say``／``ask``。所以本文件跟着换世界，但**三条契约原样保留**：

* 门不许是恒 False——「为了先关一下而 return False」不是策略的合法载体（裁定 78）；
* 门只回答「用户要不要」，「这个 profile 能不能」仍归服务判据（toolset 没启用、
  dispatcher 派出去的 worker）——开关不许绕过它；
* 出厂默认必须是关的那一格仍然钉着（深工具 ``plobi`` 现在吃这个开关）。

新世界的门分成两种，互不冒充：

* ``say``／``ask`` **不挂任何开关**——它们的门是名册判据
  ``channel.check_channel_available``：一台机器上一个可发的人都没有时，两个 schema
  根本不下发（Footprint Ladder rung 3，零足迹）；
* 深工具 ``plobi`` 仍吃产品开关 ``agent.l1_master_tools_enabled``（默认 False）。
"""

from __future__ import annotations

import copy
import importlib.util
import inspect
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO / "plugins" / "plobi-north-star"
MASTER_TOOLS_PATH = PLUGIN_DIR / "master_tools.py"
PLUGIN_INIT_PATH = PLUGIN_DIR / "__init__.py"
CHANNEL_PATH = PLUGIN_DIR / "channel.py"

#: 裁定 183 之后，Agent 之间的嘴只剩这两枚原子。
CHANNEL_MOUTHS = ("say", "ask")
#: 仍吃产品开关的那一格（深工具，不是嘴）。
SWITCHED_TOOLS = ("plobi",)
SWITCH_PATH = ("agent", "l1_master_tools_enabled")


# ─── plugin loader (hyphenated dir) ────────────────────────────────────────


def _load_by_path(attr_name: str, module_name: str, path: Path):
    """Load a plugin module by file path.

    ``plobi-north-star`` is a hyphenated directory, so it cannot be imported by
    package name; ``spec_from_file_location`` also keeps the plugin system's
    registration side effects out of the test process.
    """
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def MT():
    return _load_by_path("master_tools", "plobi_test_l1_master_tools", MASTER_TOOLS_PATH)


@pytest.fixture(scope="module")
def PI():
    try:
        return _load_by_path("plugin_init", "plobi_test_l1_plugin_init", PLUGIN_INIT_PATH)
    except Exception as exc:  # pragma: no cover - 容错
        pytest.skip(f"plugin __init__ load failed (non-fatal): {exc}")


@pytest.fixture(scope="module")
def CH(PI):
    """channel 与 register() 用的必须是**同一个实例**——monkeypatch 掉包才能作用到
    已收集的 check_fn 上。PI 那个伪包（``__init__.py`` 按 spec_from_file_location
    加载时 importlib 会自动给出 submodule_search_locations）里已经装好了它。"""
    import importlib

    return importlib.import_module("plobi_test_l1_plugin_init.channel")


class _FakePluginContext:
    """``PluginContext.register_tool`` 的最小 stub —— 只收集注册参数。"""

    def __init__(self):
        self.calls = []

    def register_tool(self, **kwargs):
        self.calls.append(kwargs)

    def register_hook(self, *_args, **_kwargs):
        pass


def _build_registered_tools(PI):
    ctx = _FakePluginContext()
    try:
        PI.register(ctx)
    except Exception:
        # register() 后面还会碰 plobi.agents.registry / 配置；本测试只关心
        # register_tool 已经收集到的那几笔，工具名与 check_fn 在这儿已经定完。
        pass
    return ctx.calls


def _find_tool(calls, name):
    for c in calls:
        if c.get("name") == name:
            return c
    return None


def _use_config(monkeypatch, *, toolset_enabled: bool, switch):
    """Point the plugin's config read at an in-memory config.

    ``switch`` 传 None 表示"用户什么都没写"——那才是出厂状态的默认路径。
    Nothing here touches a real ``PLOBI_HOME``: the config never leaves memory.

    每次调用都发一份**深拷贝**：``load_config()`` 的真实实现会把 DEFAULT_CONFIG
    往返回的字典里就地合并，早先版本这里直接把同一个 dict 交出去，结果第一个测试
    跑完， fixture 的字典已经被合并成"toolsets 里有 plobi_north_star"——判据读到
    的就不再是声明的配置了（"toolset 没勾也不许出现"那条就是这么假通过的）。
    """
    cfg: dict = {"toolsets": ["plobi_north_star"] if toolset_enabled else []}
    if switch is not None:
        cfg["agent"] = {"l1_master_tools_enabled": switch}
    monkeypatch.setattr("plobi_cli.config.load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.delenv("PLOBI_KANBAN_TASK", raising=False)
    return cfg


# ─── 默认：没勾就是没有（深工具仍吃这个开关）────────────────────────────


def test_default_config_leaves_the_switch_off():
    """出厂默认必须是关——裁定 33.4 的原意在深工具这一格继续有效。"""
    from plobi_cli.config import DEFAULT_CONFIG

    section = DEFAULT_CONFIG.get(SWITCH_PATH[0])
    assert isinstance(section, dict), f"config section {SWITCH_PATH[0]!r} missing"
    assert section.get(SWITCH_PATH[1]) is False, (
        f"{'.'.join(SWITCH_PATH)} must default to False, got {section.get(SWITCH_PATH[1])!r}"
    )


def test_unwritten_switch_behaves_like_off(MT, PI, monkeypatch):
    """「用户没写过这个键」与「写了 False」必须同一条路：深工具都不出现。

    这条防的是把缺省当真的实现成 ``cfg.get(...) or True`` 之类的反向逻辑。
    """
    for switch in (None, False):
        _use_config(monkeypatch, toolset_enabled=True, switch=switch)
        calls = _build_registered_tools(PI)
        for name in SWITCHED_TOOLS:
            c = _find_tool(calls, name)
            assert c is not None, f"{name!r} must stay registered"
            assert c["check_fn"]() is False, (
                f"{name!r} must be invisible when the switch is unwritten/off "
                f"(switch={switch!r})"
            )


def test_switch_off_hides_the_deep_tool(MT, PI, monkeypatch):
    """零 schema 足迹：开关关时，深工具的 check_fn 是 False。"""
    _use_config(monkeypatch, toolset_enabled=True, switch=False)
    calls = _build_registered_tools(PI)
    for name in SWITCHED_TOOLS:
        c = _find_tool(calls, name)
        check = c["check_fn"]
        assert check.__qualname__ == MT.check_plobi_l1_master_tools.__qualname__, (
            f"{name!r} must ride the product switch, got {check.__qualname__!r}"
        )
        assert check() is False, f"{name!r} must be hidden while the switch is off"


def test_switch_on_exposes_the_deep_tool(MT, PI, monkeypatch):
    """勾上 ⇒ 深工具可见。恒 False 的门做不到这件事，所以它不合法。"""
    _use_config(monkeypatch, toolset_enabled=True, switch=True)
    calls = _build_registered_tools(PI)
    for name in SWITCHED_TOOLS:
        c = _find_tool(calls, name)
        assert c["check_fn"]() is True, (
            f"{name!r} must become visible the moment the user ticks the switch"
        )


def test_switch_never_bypasses_the_service_gate(MT, PI, monkeypatch):
    """开关只回答"用户要不要"，"这个 profile 能不能"仍归 check_plobi_master_mode。

    两个坏结果各挡一半：toolset 没启用却出现工具（越权），以及 dispatcher 派出去的
    worker 拿到它（自派工循环）。
    """
    cfg = _use_config(monkeypatch, toolset_enabled=False, switch=True)
    calls = _build_registered_tools(PI)
    for name in SWITCHED_TOOLS:
        assert _find_tool(calls, name)["check_fn"]() is False, (
            f"{name!r} must not appear when the profile has no plobi_north_star toolset "
            f"(cfg={cfg!r}, toolset_judged={MT._toolset_enabled_in_config(cfg)!r}, "
            f"master_mode={MT.check_plobi_master_mode()!r})"
        )

    _use_config(monkeypatch, toolset_enabled=True, switch=True)
    monkeypatch.setenv("PLOBI_KANBAN_TASK", "t-under-test")
    for name in SWITCHED_TOOLS:
        assert MT.check_plobi_l1_master_tools() is False, (
            f"{name!r} must stay hidden for dispatcher-spawned workers"
        )


# ─── 两枚原子的门：名册判据，不挂开关 ─────────────────────────────────────


def test_channel_mouths_ride_the_roster_gate_not_the_switch(PI, monkeypatch):
    """say/ask 的门是 ``check_channel_available``——与产品开关无关。

    旧世界秘书两嘴「不受开关影响」那条契约，在这两枚上变成「压根不挂开关」：
    开关三种位置都看得见（只要名册里有人），也都不因为它们变成可见。
    """
    for switch in (False, True, None):
        _use_config(monkeypatch, toolset_enabled=True, switch=switch)
        calls = _build_registered_tools(PI)
        for name in CHANNEL_MOUTHS:
            c = _find_tool(calls, name)
            assert c is not None, f"{name!r} not registered"
            check = c["check_fn"]
            assert check.__qualname__ == "check_channel_available", (
                f"{name!r} must ride the roster gate, got {check.__qualname__!r}"
            )


def test_channel_gate_is_false_when_nobody_is_reachable(PI, CH, monkeypatch):
    """名册里一条可发的人都没有 ⇒ 两枚的 check_fn 都是 False（零足迹）。"""
    monkeypatch.setattr(CH, "live_names", lambda: [])
    calls = _build_registered_tools(PI)
    for name in CHANNEL_MOUTHS:
        assert _find_tool(calls, name)["check_fn"]() is False, (
            f"{name!r} must leave zero schema footprint when no agent is reachable"
        )


def test_channel_gate_is_true_when_somebody_is_reachable(PI, CH, monkeypatch):
    """名册里有人 ⇒ 看得见。恒 False 的门过不了这条。"""
    monkeypatch.setattr(CH, "live_names", lambda: ["Nymo"])
    calls = _build_registered_tools(PI)
    for name in CHANNEL_MOUTHS:
        assert _find_tool(calls, name)["check_fn"]() is True, (
            f"{name!r} must be visible when the roster has somebody to talk to"
        )


# ─── 防回归：不许再把策略写成恒假门 / 不许删实现 ──────────────────────────


def test_no_hardcoded_mouth_gate_survives(MT):
    """「为了先关一下而 return False」不是策略的合法载体（裁定 78）。

    这里钉的是实现方式，不是具体名字——名字改回去、或新加一个恒 False 的嘴门，都会红。
    """
    src = inspect.getsource(MT)
    assert "check_plobi_l1_mouth_disabled" not in src, (
        "the hardcoded always-False mouth gate must not come back"
    )
    assert "恒 False" not in src, "a predicate must not describe itself as 恒 False"
    body_src = inspect.getsource(MT.check_plobi_l1_master_tools)
    assert "l1_master_tools_enabled" in body_src, (
        "the switch must actually be read from config, not hardcoded"
    )


def test_new_world_identifiers_stay_intact(PI, CH):
    """裁定 183 这一半的「不删」：两枚原子的 schema/handler 与深工具的开关判据都在。

    旧版这条钉的是被删掉的 7 枚（SECRETARY_ASK_SCHEMA 等）——它们按裁定 183 没了，
    钉它们就是钉倒退。现在钉新世界的四样：两个 schema、两个 handler、名册判据本体，
    以及深工具还在吃的那枚开关判据。
    """
    init_src = inspect.getsource(PI)
    for ident in ("_CHANNEL_TOOLS", "check_channel_available", "check_plobi_l1_master_tools"):
        assert ident in init_src, f"plugin __init__ must keep {ident!r} intact"
    channel_src = inspect.getsource(CH)
    for ident in ("SAY_SCHEMA", "ASK_SCHEMA", "handle_say", "handle_ask", "check_channel_available"):
        assert ident in channel_src, f"channel.py must keep {ident!r} intact"


def test_registration_rides_gates_not_a_list_of_closed_names(PI):
    """门按判据挂，不许再靠「名单 + 恒 False」表达策略。"""
    src = inspect.getsource(PI)
    assert "check_plobi_l1_master_tools" in src, (
        "the deep tool must be registered behind the product-switch predicate"
    )
    assert "_L1_MOUTH_CLOSED" not in src, (
        "a set of 'closed mouths' re-implements the policy the gates own"
    )
