"""``--self-check``：装了到底能不能用，用一条命令问出来。

0.3.4 的事故是侧车没读根 ``.env``：主进程有凭据、侧车没有，单测全绿，用户一点引擎就
「变量未设置」。这里锁死四件事——

* 凭据到不了本进程的 ``os.environ`` 必须 FAIL；
* 表读不出来必须 FAIL；
* ``main`` 里 ``_load_root_env`` 必须早于 ``serve``（0.3.4 的原始回归）；
* 自检绝不能顺手把侧车拉起来。
"""

from __future__ import annotations

import ast
import inspect
import os
import textwrap

import pytest

from agent.egress import helper
from agent.egress.rules import EgressConfigError, Node, Rule, Table

KEY = "PLOBI_TEST_SELFCHECK_NODE_PASSWORD"


def _table(*, secret_env: dict[str, str] | None = None, unreadable: str = "") -> Table:
    node = Node(
        name="n1",
        type="ss",
        server="example.invalid",
        port=8388,
        secret_env={"password": KEY} if secret_env is None else dict(secret_env),
    )
    rule = Rule(match="example.invalid", via="n1", kind="exact", value="example.invalid")
    return Table(rules=(rule,), nodes={"n1": node}, unreadable=unreadable)


@pytest.fixture()
def wired(monkeypatch, tmp_path):
    """把自检要看见的世界接成可摆布的：home、表、编译器、TUN 全打桩。"""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text(f"{KEY}=from-root-env\n", encoding="utf-8")
    monkeypatch.setattr(helper.helper_client, "home_dir", lambda h=None: home)
    monkeypatch.setattr("agent.egress.rules.load_table", lambda **kw: _table())
    monkeypatch.setattr(
        "agent.egress.mihomo_config.compile_config", lambda *a, **kw: "mixed-port: 7890\n"
    )
    monkeypatch.setattr(helper._tun, "preflight", lambda *a, **kw: {})
    monkeypatch.setattr(helper._tun, "status_lines", lambda info: [])
    return home


def test_self_check_passes_as_a_whole(wired, monkeypatch, capsys):
    monkeypatch.setenv(KEY, "from-root-env")
    assert helper._self_check(wired) == helper.EXIT_OK
    out = capsys.readouterr().out
    assert "[PASS] env-file" in out
    assert "[PASS] credentials" in out
    assert "[PASS] compile" in out
    assert "SELF-CHECK: ok" in out


def test_self_check_fails_when_a_secret_never_reaches_os_environ(wired, monkeypatch, capsys):
    monkeypatch.delenv(KEY, raising=False)
    assert helper._self_check(wired) == helper.EXIT_FAIL
    out = capsys.readouterr().out
    assert "[FAIL] credentials" in out
    assert KEY in out
    assert "SELF-CHECK: failed (1)" in out


def test_self_check_fails_when_the_env_file_is_missing(wired, monkeypatch, capsys):
    (wired / ".env").unlink()
    monkeypatch.setenv(KEY, "from-shell")
    assert helper._self_check(wired) == helper.EXIT_FAIL
    assert "[FAIL] env-file" in capsys.readouterr().out


def test_self_check_fails_when_the_table_is_unreadable(wired, monkeypatch, capsys):
    monkeypatch.setattr(
        "agent.egress.rules.load_table", lambda **kw: _table(unreadable="config.yaml 坏了")
    )
    monkeypatch.setenv(KEY, "from-root-env")
    assert helper._self_check(wired) == helper.EXIT_FAIL
    out = capsys.readouterr().out
    assert "[FAIL] table" in out
    assert "config.yaml 坏了" in out


def test_self_check_surfaces_a_compile_refusal(wired, monkeypatch, capsys):
    def _boom(*_a, **_kw):
        raise EgressConfigError(f"names '{KEY}' but that variable is not set")

    monkeypatch.setattr("agent.egress.mihomo_config.compile_config", _boom)
    monkeypatch.setenv(KEY, "from-root-env")
    assert helper._self_check(wired) == helper.EXIT_FAIL
    assert "[FAIL] compile" in capsys.readouterr().out


def test_main_self_check_never_starts_the_sidecar(wired, monkeypatch, capsys):
    monkeypatch.setenv(KEY, "from-root-env")
    started: list[object] = []
    monkeypatch.setattr(helper, "serve", lambda *a, **kw: started.append(a) or helper.EXIT_OK)
    assert helper.main(["--self-check", "--home", str(wired)]) == helper.EXIT_OK
    assert started == []
    assert "SELF-CHECK: ok" in capsys.readouterr().out


def test_main_feeds_the_env_before_checking(wired, monkeypatch):
    """走真实链路：shell 里没有这个变量，根 ``.env`` 也得把它补进 os.environ。"""
    monkeypatch.delenv(KEY, raising=False)
    assert helper.main(["--self-check", "--home", str(wired)]) == helper.EXIT_OK
    assert os.environ.get(KEY) == "from-root-env"


def test_main_reports_fail_when_nothing_supplies_the_secret(wired, monkeypatch):
    monkeypatch.delenv(KEY, raising=False)
    (wired / ".env").write_text("SOMETHING_ELSE=1\n", encoding="utf-8")
    assert helper.main(["--self-check", "--home", str(wired)]) == helper.EXIT_FAIL


def test_main_loads_the_root_env_before_serving():
    """0.3.4 的原始回归：侧车活着、有权限、管道也在，就是没凭据。"""
    tree = ast.parse(textwrap.dedent(inspect.getsource(helper.main)))
    calls: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            calls.append((node.lineno, node.func.id))
    order = [name for _, name in sorted(calls) if name in {"_load_root_env", "serve", "_self_check"}]
    assert order[0] == "_load_root_env", order
