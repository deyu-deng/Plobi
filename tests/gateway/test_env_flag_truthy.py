"""Env flags accept 'on' as truthy consistently (salvage of #2863).

Behavior contract: every env-driven enable flag in gateway config coerces
through the shared TRUTHY_STRINGS set, so "on" behaves like "1"/"true"/"yes".
"""

import os
from unittest.mock import patch

from utils import TRUTHY_STRINGS, env_var_enabled


def test_truthy_strings_include_on():
    assert "on" in TRUTHY_STRINGS


def test_env_var_enabled_accepts_on():
    with patch.dict(os.environ, {"WHATSAPP_ENABLED": "on"}):
        assert env_var_enabled("WHATSAPP_ENABLED") is True


def test_env_var_enabled_default_respected():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("SIGNAL_IGNORE_STORIES", None)
        assert env_var_enabled("SIGNAL_IGNORE_STORIES", "true") is True
        assert env_var_enabled("SIGNAL_IGNORE_STORIES") is False


def test_gateway_config_flags_use_shared_helper():
    """Invariant: no env-flag site in gateway/config.py hand-rolls a truthy
    set that omits 'on'."""
    import inspect

    import gateway.config as gc

    src = inspect.getsource(gc)
    for pattern in ('in {"true", "1", "yes"}', 'in ("true", "1", "yes")'):
        assert pattern not in src, f"hand-rolled truthy set without 'on': {pattern}"


def test_desktop_terminal_gates_no_longer_read_the_env_flag():
    """桌面那两把（read_terminal／close_terminal）**不再有 env 闸**。

    以前答的是「这个进程是不是被桌面起的」（``PLOBI_DESKTOP``）⇒ 那是**进程级**的：
    桌面客户端连**远程后端**时后端进程上没有那个变量 ⇒ 工具**从工具表里静默消失**，
    而能力其实可用（renderer 的 WS 桥就在那个连接上）。

    现在：**门类 ``desktop_ui`` 就是那道面闸**（按这一会话的平台折进去），
    check_fn 只答可达性、不答表面 ⇒ ``PLOBI_DESKTOP`` 摆 on／off／没有都一样。
    新的判据在 ``tests/tools/test_desktop_ui_surface.py``。
    """
    from tools.close_terminal_tool import check_close_terminal_requirements
    from tools.read_terminal_tool import check_read_terminal_requirements

    for value in ("on", "off"):
        with patch.dict(os.environ, {"PLOBI_DESKTOP": value}):
            assert check_read_terminal_requirements() is True
            assert check_close_terminal_requirements() is True
