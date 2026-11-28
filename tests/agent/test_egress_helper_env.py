"""侧车启动时必须把根 ``.env`` 读进来。

回归的场景（0.3.4 现场）：主进程里点「启动引擎」，侧车 ``engine.ensure`` 报
``EgressConfigError: ... password_env names 'X' but that variable is not set``。配置没错、
``.env`` 也没错——错在侧车是个计划任务拉起的独立进程，从来没人给它读过 ``.env``。

这一组测试钉住三件事：

1. ``_load_root_env`` 真的把 ``$PLOBI_HOME/.env`` 灌进了 ``os.environ``；
2. loader 不可用时兜底路径照样能读出凭据（否则故障会从「凭据缺失」变成「侧车起不来」）；
3. ``_table`` 不再往 ``load_table()`` 里塞它不认识的 ``home`` 实参。
"""

from __future__ import annotations

import builtins
import os
from pathlib import Path

import pytest

from agent.egress import helper

#: 测试专用变量名：不撞 ``_CREDENTIAL_SUFFIXES`` 里的任何真实键，也不会被
#: managed scope / 外源密钥覆盖。
_KEY = "PLOBI_TEST_EGRESS_NODE_PASSWORD"


def _write_env(home: Path, name: str, value: str) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    env_file = home / ".env"
    env_file.write_text(f"{name}={value}\n", encoding="utf-8")
    return env_file


def test_load_root_env_feeds_os_environ(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """根 .env 里的凭据必须出现在本进程的 os.environ 里。"""
    monkeypatch.delenv(_KEY, raising=False)
    _write_env(tmp_path, _KEY, "s3cret-from-root-env")

    helper._load_root_env(tmp_path)

    assert os.environ.get(_KEY) == "s3cret-from-root-env"


def test_load_root_env_overrides_a_stale_shell_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """shell 里过期的同名导出不该压过 .env —— 与主进程的 override 语义保持一致。"""
    monkeypatch.setenv(_KEY, "stale-from-shell")
    _write_env(tmp_path, _KEY, "fresh-from-root-env")

    helper._load_root_env(tmp_path)

    assert os.environ.get(_KEY) == "fresh-from-root-env"


def test_load_root_env_is_quiet_when_there_is_no_env_file(tmp_path: Path):
    """没有 .env 是常见情况，不许抛。"""
    helper._load_root_env(tmp_path / "not-there")


def test_fallback_reads_env_when_the_loader_cannot_be_imported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """``plobi_cli`` 不在 import 路径上时，兜底仍要能读到凭据。"""
    monkeypatch.delenv(_KEY, raising=False)
    _write_env(tmp_path, _KEY, "s3cret-via-fallback")

    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "plobi_cli.env_loader" or name.startswith("plobi_cli.env_loader."):
            raise ImportError("simulated: no plobi_cli in this build")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    helper._load_root_env(tmp_path)

    assert os.environ.get(_KEY) == "s3cret-via-fallback"


def test_main_loads_the_env_before_serving(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """``main()`` 里加载与 serve 的先后顺序不能反：第一条命令进来前就得好。"""
    monkeypatch.delenv(_KEY, raising=False)
    _write_env(tmp_path, _KEY, "s3cret-before-serve")

    seen: dict[str, object] = {}

    def fake_serve(home, *, pipe=None, ready=None, idle_s=0.0, max_requests=None):  # noqa: ANN001
        seen["value"] = os.environ.get(_KEY)
        return 0

    monkeypatch.setattr(helper, "serve", fake_serve)

    assert helper.main(["--home", str(tmp_path)]) == 0
    assert seen["value"] == "s3cret-before-serve"


def test_table_never_passes_home_to_load_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """``load_table()`` 不认 ``home``：再传就是假参数，报错还会被吞掉。"""
    from agent.egress import rules

    seen: dict[str, object] = {}

    def fake_load_table(**kwargs):
        seen.update(kwargs)
        return "TABLE"

    monkeypatch.setattr(rules, "load_table", fake_load_table)

    assert helper._table(tmp_path) == "TABLE"
    assert seen == {}
