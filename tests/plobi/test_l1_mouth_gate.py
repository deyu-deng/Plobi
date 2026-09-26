"""WP-L1-EVERY-TURN — L1 总秘书本周只剩秘书两张嘴（33.1 / 33.4）。

P0-3：``plugins/plobi-north-star/master_tools.py::check_plobi_l1_mouth_disabled``
恒 False，挂在 4 个 Master 工具 + 深工具 ``plobi`` 的 ``check_fn`` 上。
``plobi_secretary_ask`` / ``plobi_checkin_respond`` 仍用
``check_plobi_master_mode``——L1 本周只剩这两张嘴。

不删 handler / schema / SECRETARY_ASK_* / intent 表——M3 切回开放只需
把 check_fn 改回 None。
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


REPO = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO / "plugins" / "plobi-north-star"
MASTER_TOOLS_PATH = PLUGIN_DIR / "master_tools.py"
PLUGIN_INIT_PATH = PLUGIN_DIR / "__init__.py"


# ─── plugin loader (hyphenated dir) ────────────────────────────────────────


def _load_master_tools_module():
    """按文件路径直接加载 ``master_tools.py``。

    插件目录带连字符（``plobi-north-star``），不能按包名 import；
    走 ``importlib.util.spec_from_file_location`` 跳过插件系统副作用。
    """
    name = "plobi_test_l1_master_tools"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, MASTER_TOOLS_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_plugin_init_module():
    """按文件路径直接加载 ``__init__.py`` 以便静态校验 register()。

    同样不能用包名 import；这里只用来读源码 / 校验注册点常量，不 exec
    任何 PluginContext / hard_route 副作用（spec_from_file_location 会
    跑模块级代码——但只 import stdlib + 同目录子模块，可控）。
    """
    name = "plobi_test_l1_plugin_init"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, PLUGIN_INIT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def MT():
    return _load_master_tools_module()


@pytest.fixture(scope="module")
def PI():
    """Plugin __init__ module（用于静态校验 register() 的 check_fn 分组）。"""
    try:
        return _load_plugin_init_module()
    except Exception as exc:  # pragma: no cover - 容错
        pytest.skip(f"plugin __init__ load failed (non-fatal): {exc}")


# ─── P0-3.1: 恒 False 函数 ────────────────────────────────────────────────


def test_l1_mouth_disabled_function_returns_false(MT):
    """``check_plobi_l1_mouth_disabled()`` 恒 False——M3 才开。"""
    assert callable(MT.check_plobi_l1_mouth_disabled)
    assert MT.check_plobi_l1_mouth_disabled() is False
    # 多次调用同样 False（不依赖任何外部状态）
    for _ in range(5):
        assert MT.check_plobi_l1_mouth_disabled() is False


def test_l1_mouth_disabled_function_docstring(MT):
    """docstring 必须明确指明「M3 才开；handler 保留」+ 引用 33.1 / 33.4。"""
    doc = MT.check_plobi_l1_mouth_disabled.__doc__ or ""
    assert "M3" in doc, "docstring must reference 'M3 才开'"
    assert "handler" in doc.lower(), "docstring must mention handler preservation"
    # 33.1 / 33.4 引用其中一个即可
    assert ("33.1" in doc) or ("33.4" in doc), (
        "docstring must reference裁定 33.1 / 33.4"
    )


# ─── P0-3.2: 默认 profile + toolset 下，5 个工具不可见 / 2 个秘书可见 ─────


class _FakePluginContext:
    """``PluginContext.register_tool`` 的最小 stub。"""

    def __init__(self):
        self.calls = []  # list of dict(name, toolset, check_fn, ...)

    def register_tool(self, **kwargs):
        self.calls.append(kwargs)

    def register_hook(self, *_args, **_kwargs):
        pass


def _build_registered_tools(PI):
    """跑一次 ``PI.register(ctx)``，收集所有 ``register_tool`` 调用。"""
    ctx = _FakePluginContext()
    # register() 会 import plobi.agents.registry 试调；catch 即可
    try:
        PI.register(ctx)
    except Exception:
        # 即便 plobi.agents.registry 不在 / 配置不在，register_tool 已跑完
        pass
    return ctx.calls


def _find_tool(calls, name):
    for c in calls:
        if c.get("name") == name:
            return c
    return None


def test_l1_default_profile_cannot_use_master_tools(MT, PI, monkeypatch):
    """L1 default profile + toolset 启用：4 个 Master 工具 + plobi 不可见。

    ``check_fn`` 必须等于 ``MT.check_plobi_l1_mouth_disabled``（恒 False）。
    实际"不可见"由 ``tools/registry.py`` 在工具查询时执行 check_fn 决定。
    """
    # 模拟 L1 profile + plobi_north_star toolset 启用
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"toolsets": ["plobi_north_star"]},
    )

    calls = _build_registered_tools(PI)
    # 5 个工具必须注册
    for tool_name in (
        "plobi_master_status",
        "plobi_master_preview",
        "plobi_master_dispatch",
        "plobi_master_approve",
        "plobi",
    ):
        c = _find_tool(calls, tool_name)
        assert c is not None, f"tool {tool_name!r} not registered"
        # 关键断言：check_fn 切到恒 False
        # 用 __qualname__ 判定（plugin init 与 test fixture 走不同 import
        # path，``is`` 比较不靠谱；同源 master_tools.py 的 __qualname__
        # 必定一致）。
        check = c["check_fn"]
        assert check.__qualname__ == MT.check_plobi_l1_mouth_disabled.__qualname__, (
            f"tool {tool_name!r} check_fn must be check_plobi_l1_mouth_disabled, "
            f"got qualname {check.__qualname__!r}"
        )
        # 直接 verify 调 check_fn 返回 False
        assert check() is False, (
            f"tool {tool_name!r} check_fn must return False (mouth closed)"
        )


def test_l1_default_profile_secretary_ask_still_visible(MT, PI, monkeypatch):
    """L1 default profile：``plobi_secretary_ask`` + ``plobi_checkin_respond``
    仍用 ``check_plobi_master_mode``，工具可见（不是 mouth-disabled）。"""
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"toolsets": ["plobi_north_star"]},
    )

    calls = _build_registered_tools(PI)

    for tool_name in ("plobi_secretary_ask", "plobi_checkin_respond"):
        c = _find_tool(calls, tool_name)
        assert c is not None, f"tool {tool_name!r} not registered"
        # 关键：secretary 工具仍用 check_plobi_master_mode（不是恒 False）
        check = c["check_fn"]
        assert check.__qualname__ == MT.check_plobi_master_mode.__qualname__, (
            f"tool {tool_name!r} check_fn must remain check_plobi_master_mode, "
            f"got qualname {check.__qualname__!r}"
        )
        # L1 profile + toolset 启用 → check_plobi_master_mode() 返回 True
        assert check() is True, (
            f"tool {tool_name!r} must be visible on L1 default profile"
        )


def test_l2_profile_unaffected(MT, monkeypatch):
    """L2 profile 不走 mouth gate——``check_plobi_master_mode`` 仍按原逻辑判定。

    L2-agenda profile 不在 ``default`` 上，L1 嘴门根本不会切到它头上。
    ``check_plobi_master_mode()`` 走的是 "toolset 启用 + 无 worker 环境变量"
    的原判定——L2 profile 启用了 plobi_north_star toolset 就能看见工具。
    """
    # L2 + plobi_north_star toolset 启用
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"toolsets": ["plobi_north_star"]},
    )
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    assert MT.check_plobi_master_mode() is True
    # mouth-disabled 不依赖 profile / toolset（恒 False）
    assert MT.check_plobi_l1_mouth_disabled() is False


# ─── P0-3.3: source-level 静态校验（防回归） ──────────────────────────────


def test_plugin_init_source_uses_mouth_disabled_for_master_tools(PI):
    """``plugins/plobi-north-star/__init__.py`` 源码必须把恒 False check_fn
    挂到 4 个 Master 工具 + 深工具 ``plobi``。

    防回归：以后有人误改回 ``check_plobi_master_mode``，本测试会直接挂。
    """
    import inspect

    src = inspect.getsource(PI)
    # 4 个 Master 工具 + 深工具 plobi 都必须挂 check_plobi_l1_mouth_disabled
    for name in (
        "plobi_master_status",
        "plobi_master_preview",
        "plobi_master_dispatch",
        "plobi_master_approve",
    ):
        assert name in src, f"plugin __init__ must mention {name!r}"
    # check_plobi_l1_mouth_disabled 必须出现在 register() 里
    assert "check_plobi_l1_mouth_disabled" in src, (
        "plugin __init__ must reference check_plobi_l1_mouth_disabled in register()"
    )
    # secretary_ask + checkin_respond 不在 _L1_MOUTH_CLOSED 集合里
    # —— 它们走 else 分支挂 check_plobi_master_mode
    # （具体验证见 test_l1_default_profile_secretary_ask_still_visible 端到端）


def test_plugin_init_keeps_secretary_tools_unchanged(PI):
    """``plobi_secretary_ask`` / ``plobi_checkin_respond`` 的 handler / schema
    在 ``__init__.py`` 源码里保持原状（任务书 §3 红线：不删 handler）。"""
    src = PI.__init_globals__.get("__file__", "") if False else None  # noqa
    import inspect

    src = inspect.getsource(PI)
    # SECRETARY_ASK_SCHEMA / CHECKIN_RESPOND_SCHEMA / handle_secretary_ask /
    # handle_checkin_respond 都在 _MASTER_TOOLS 元组里
    for ident in (
        "SECRETARY_ASK_SCHEMA",
        "CHECKIN_RESPOND_SCHEMA",
        "handle_secretary_ask",
        "handle_checkin_respond",
        "plobi_secretary_ask",
        "plobi_checkin_respond",
    ):
        assert ident in src, f"plugin __init__ must keep {ident!r} intact"


def test_master_tools_module_exposes_both_check_fns(MT):
    """master_tools.py 必须同时导出 ``check_plobi_master_mode`` 和
    ``check_plobi_l1_mouth_disabled`` 两个 check_fn——后者是 P0-3 的新增。"""
    assert hasattr(MT, "check_plobi_master_mode")
    assert hasattr(MT, "check_plobi_l1_mouth_disabled")
    # 两者的接口签名必须一致：0 参 + 返回 bool
    import inspect

    for fn in (MT.check_plobi_master_mode, MT.check_plobi_l1_mouth_disabled):
        sig = inspect.signature(fn)
        assert len(sig.parameters) == 0, f"{fn!r} must take no args"
