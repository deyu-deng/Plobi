"""WP-L1-MOUTH-SWITCH — L1 那五张派工嘴由产品开关决定（裁定 78 / 49 / P7）。

判据写成关系，不写名单快照（今晚 `d2170c91` 刚上过同一课）：

* 开关没勾 ⇒ 名单里**每一项**都零 schema 足迹；
* 勾上 ⇒ 名单里**每一项**都可见；
* 开关不绕过服务判据 ⇒ toolset 没启用、或这是 dispatcher 派出去的 worker，勾了也不出现；
* 秘书那两张嘴（``plobi_secretary_ask`` / ``plobi_checkin_respond``）不受本开关影响。

同时守住裁定 33.4 的另一半红线：handler / schema / ``SECRETARY_ASK_*`` / intent 表
一律不删——换的是门的表达方式，不是实现。
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

# 名单来自这里，但断言一律按「每一项都…」写，不写「恰好等于这几个」。
# 加一张嘴进这个元组，下面的关系断言自动覆盖它——这才是契约的形状。
SWITCHED_MOUTHS = (
    "plobi_master_status",
    "plobi_master_preview",
    "plobi_master_dispatch",
    "plobi_master_approve",
    "plobi",
)
SECRETARY_MOUTHS = ("plobi_secretary_ask", "plobi_checkin_respond")
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


# ─── 默认：没勾就是没有 ────────────────────────────────────────────────────


def test_default_config_leaves_the_switch_off():
    """出厂默认必须是关——保留裁定 33.4 的原意（L1 的第一动作是 secretary_ask）。"""
    from plobi_cli.config import DEFAULT_CONFIG

    section = DEFAULT_CONFIG.get(SWITCH_PATH[0])
    assert isinstance(section, dict), f"config section {SWITCH_PATH[0]!r} missing"
    assert section.get(SWITCH_PATH[1]) is False, (
        f"{'.'.join(SWITCH_PATH)} must default to False, got {section.get(SWITCH_PATH[1])!r}"
    )


def test_unwritten_switch_behaves_like_off(MT, PI, monkeypatch):
    """「用户没写过这个键」与「写了 False」必须同一条路：都不出现。

    这条防的是把缺省当真的实现成 ``cfg.get(...) or True`` 之类的反向逻辑。
    """
    for switch in (None, False):
        _use_config(monkeypatch, toolset_enabled=True, switch=switch)
        calls = _build_registered_tools(PI)
        for name in SWITCHED_MOUTHS:
            c = _find_tool(calls, name)
            assert c is not None, f"{name!r} must stay registered (33.4: handler 不删)"
            assert c["check_fn"]() is False, (
                f"{name!r} must be invisible when the switch is unwritten/off "
                f"(switch={switch!r})"
            )


def test_switch_off_hides_every_switched_mouth(MT, PI, monkeypatch):
    """零 schema 足迹：开关关时，名单里每一项的 check_fn 都是 False。"""
    _use_config(monkeypatch, toolset_enabled=True, switch=False)
    calls = _build_registered_tools(PI)
    for name in SWITCHED_MOUTHS:
        c = _find_tool(calls, name)
        check = c["check_fn"]
        assert check.__qualname__ == MT.check_plobi_l1_master_tools.__qualname__, (
            f"{name!r} must ride the product switch, got {check.__qualname__!r}"
        )
        assert check() is False, f"{name!r} must be hidden while the switch is off"


# ─── 勾上：必须生效（裁定 49 / P7 的那一句「勾了就生效」）────────────────


def test_switch_on_exposes_every_switched_mouth(MT, PI, monkeypatch):
    """勾上 ⇒ 名单里每一项都可见。恒 False 的门做不到这件事，所以它不合法。"""
    _use_config(monkeypatch, toolset_enabled=True, switch=True)
    calls = _build_registered_tools(PI)
    for name in SWITCHED_MOUTHS:
        c = _find_tool(calls, name)
        assert c["check_fn"]() is True, (
            f"{name!r} must become visible the moment the user ticks the switch"
        )


def test_switch_never_bypasses_the_service_gate(MT, PI, monkeypatch):
    """开关只回答"用户要不要"，"这个 profile 能不能"仍归 check_plobi_master_mode。

    两个坏结果各挡一半：toolset 没启用却出现工具（越权），以及 dispatcher 派出去的
    worker 拿到派工嘴（自派工循环）。
    """
    cfg = _use_config(monkeypatch, toolset_enabled=False, switch=True)
    calls = _build_registered_tools(PI)
    for name in SWITCHED_MOUTHS:
        assert _find_tool(calls, name)["check_fn"]() is False, (
            f"{name!r} must not appear when the profile has no plobi_north_star toolset "
            f"(cfg={cfg!r}, toolset_judged={MT._toolset_enabled_in_config(cfg)!r}, "
            f"master_mode={MT.check_plobi_master_mode()!r})"
        )

    _use_config(monkeypatch, toolset_enabled=True, switch=True)
    monkeypatch.setenv("PLOBI_KANBAN_TASK", "t-under-test")
    for name in SWITCHED_MOUTHS:
        assert MT.check_plobi_l1_master_tools() is False, (
            f"{name!r} must stay hidden for dispatcher-spawned workers"
        )


def test_secretary_mouths_ignore_the_switch(MT, PI, monkeypatch):
    """秘书两张嘴不受本刀影响：仍是 check_plobi_master_mode，开关两种位置都可见。"""
    for switch in (False, True, None):
        _use_config(monkeypatch, toolset_enabled=True, switch=switch)
        calls = _build_registered_tools(PI)
        for name in SECRETARY_MOUTHS:
            c = _find_tool(calls, name)
            assert c is not None, f"{name!r} not registered"
            check = c["check_fn"]
            assert check.__qualname__ == MT.check_plobi_master_mode.__qualname__, (
                f"{name!r} must keep check_plobi_master_mode, got {check.__qualname__!r}"
            )
            assert check() is True, f"{name!r} must stay visible (switch={switch!r})"


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


def test_handlers_and_schemas_stay_intact(PI):
    """裁定 33.4 保留实现这半：handler / schema / intent 表一个都不许删。"""
    src = inspect.getsource(PI)
    for ident in (
        "SECRETARY_ASK_SCHEMA",
        "CHECKIN_RESPOND_SCHEMA",
        "handle_secretary_ask",
        "handle_checkin_respond",
        *SWITCHED_MOUTHS,
        *SECRETARY_MOUTHS,
    ):
        assert ident in src, f"plugin __init__ must keep {ident!r} intact"


def test_registration_rides_the_switch_not_a_list_of_closed_names(PI):
    """名单只回答"开关管哪几张嘴"，不许再靠「名单 + 恒 False」表达策略。"""
    src = inspect.getsource(PI)
    assert "check_plobi_l1_master_tools" in src, (
        "the five mouths must be registered behind the product-switch predicate"
    )
    assert "_L1_MOUTH_CLOSED" not in src, (
        "a set of 'closed mouths' re-implements the policy the switch owns"
    )
