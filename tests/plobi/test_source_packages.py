"""信源包那三把（WP-SOURCE-PACKAGES）：形状、闸门留在 CLI 里、守卫名单。

跑法：`bash scripts/run_tests.sh tests/plobi/test_source_packages.py -q`（唯一入口）。
**零真 pip、零真网络、零真安装**：这一档只验「工具这一层做了什么」——
spawn 的 argv 逐字、pip 一次都没被碰、名字闸门的话原样回来。

这一档要钉住的是五件事：
① **形状**（裁定 185）：三把，一件原子事一件工具，参数里**没有** action/intent/mode/purpose；
② **闸门仍在 CLI 里**：这一层只 spawn `plobi collectors …`，**不碰 pip、不预校验包名**
   （用户输入离执行最近的一跳不该只隔一次函数调用——`external.py` 模块头第 1 条）；
③ **CLI 自己说的话原样回来**（不转述成「装不上」）；
④ **装完回它带来的信源 id**；
⑤ **两把改动工具在循环守卫的改动名单里**，只读那把不在；三把都在默认束里。
"""

from __future__ import annotations

import json
import subprocess

import pytest

from agent.tool_guardrails import MUTATING_TOOL_NAMES
from tools import source_tools
from tools.registry import registry as tool_registry
from toolsets import _PLOBI_CORE_TOOLS

PACKAGE_TOOLS = ("source_package_list", "source_package_install", "source_package_remove")

WRITERS = ("source_package_install", "source_package_remove")

MODE_LIKE = ("action", "intent", "mode", "purpose")


class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeSubprocess:
    """只替掉**本模块**用的那一个 `subprocess`（不碰全局那个，别影响 pytest 自己）。"""

    #: except 子句要引用 `subprocess.TimeoutExpired`——真模块上有，假模块也得有。
    TimeoutExpired = subprocess.TimeoutExpired

    def __init__(self):
        self.calls = []
        self.returncode = 0
        self.stdout = ""
        self.stderr = ""
        self.raises = None

    def run(self, cmd, **kwargs):
        self.calls.append((list(cmd), kwargs))
        if self.raises is not None:
            raise self.raises
        return _FakeProc(self.returncode, self.stdout, self.stderr)

    @property
    def argv(self):
        return self.calls[-1][0]


@pytest.fixture()
def fake_cli(monkeypatch):
    fake = _FakeSubprocess()
    monkeypatch.setattr(source_tools, "subprocess", fake)
    return fake


@pytest.fixture()
def no_pip(monkeypatch):
    """这一层**一次都不许碰 pip**——碰了就当场炸（不是静默放过）。"""

    def _boom(*args, **kwargs):
        raise AssertionError("工具这一层不许碰 pip（pip 只在 CLI 那一层跑）")

    import plobi_cli.tools_config as tools_config

    monkeypatch.setattr(tools_config, "_pip_install", _boom)


def _call(name: str, **args) -> dict:
    """调一把工具，回**解过码**的那一包（工具回的是 JSON 字符串）。"""
    handler = tool_registry.get_entry(name).handler
    return json.loads(handler(args))


class TestShape:
    """三把，一个门类，一件原子事一件工具（裁定 185）。"""

    def test_three_tools_are_registered_under_the_sources_toolset(self) -> None:
        for name in PACKAGE_TOOLS:
            entry = tool_registry.get_entry(name)
            assert entry is not None, f"{name} 没注册"
            assert entry.toolset == "sources", f"{name} 的门类不是 sources"

    def test_no_mode_like_parameter(self) -> None:
        """参数里不许有选分支的那一类（裁 185 判据③）。"""
        for name in PACKAGE_TOOLS:
            props = tool_registry.get_schema(name)["parameters"]["properties"]
            for key in props:
                assert key not in MODE_LIKE, f"{name} 的参数里有 {key}"

    def test_install_and_remove_take_exactly_one_argument(self) -> None:
        for name in WRITERS:
            schema = tool_registry.get_schema(name)
            assert list(schema["parameters"]["properties"]) == ["package"], name
            assert schema["parameters"]["required"] == ["package"], name

    def test_list_takes_no_argument(self) -> None:
        schema = tool_registry.get_schema("source_package_list")
        assert schema["parameters"]["properties"] == {}
        assert not schema["parameters"].get("required")

    def test_all_three_are_in_the_default_bundle(self) -> None:
        for name in PACKAGE_TOOLS:
            assert name in _PLOBI_CORE_TOOLS, f"{name} 不在默认束里"

    def test_writers_are_in_the_loop_guard_mutation_list(self) -> None:
        for name in WRITERS:
            assert name in MUTATING_TOOL_NAMES, f"{name} 不在改动名单里"
        assert "source_package_list" not in MUTATING_TOOL_NAMES, "只读那把不该进改动名单"


class TestTheGateStaysInTheCli:
    """这一层只 spawn；名字闸门与 pip 都在 CLI 那一层。"""

    def test_install_spawns_the_cli_verbatim(self, fake_cli, no_pip) -> None:
        fake_cli.stdout = json.dumps({"ok": True, "package": "pkg-a", "summary": "装上了 pkg-a。",
                                      "added": ["src_a"], "sources": ["src_a"]})
        out = _call("source_package_install", package="pkg-a")
        assert out["ok"] is True
        argv = fake_cli.argv
        assert argv[1:] == ["-m", "plobi_cli.main", "collectors", "install", "pkg-a", "--json", "--yes"]
        assert argv[0].endswith("python.exe") or argv[0].endswith("python")

    def test_remove_spawns_the_remove_verb(self, fake_cli, no_pip) -> None:
        fake_cli.stdout = json.dumps({"ok": True, "package": "pkg-a", "summary": "卸掉了 pkg-a。",
                                      "removed": ["src_a"], "sources": []})
        out = _call("source_package_remove", package="pkg-a")
        assert out["ok"] is True
        assert fake_cli.argv[1:6] == ["-m", "plobi_cli.main", "collectors", "remove", "pkg-a"]
        assert fake_cli.argv[-2:] == ["--json", "--yes"]

    def test_the_tool_does_not_prevalidate_the_package_name(self, fake_cli, no_pip) -> None:
        """不合的名字**原样交给 CLI**——这一层不解释它（闸门在 CLI 那一层）。"""
        fake_cli.returncode = 1
        fake_cli.stderr = "这一层不收版本约束：`pkg==1.2` 不是一个包名。"
        out = _call("source_package_install", package="pkg==1.2")
        assert fake_cli.argv[5] == "pkg==1.2", "名字被这一层改过了"
        assert out["ok"] is False
        assert "不收版本约束" in out["error"], "CLI 自己那句话没原样回来"

    def test_cli_rejection_comes_back_verbatim(self, fake_cli, no_pip) -> None:
        fake_cli.returncode = 1
        fake_cli.stderr = "空的是不行的：敲一个包名。"
        out = _call("source_package_install", package="")
        assert out["ok"] is False
        assert out["error"] == "空的是不行的：敲一个包名。"

    def test_the_spawn_is_non_interactive(self, fake_cli, no_pip) -> None:
        fake_cli.stdout = json.dumps({"ok": True, "package": "pkg-a", "summary": "装上了 pkg-a。"})
        _call("source_package_install", package="pkg-a")
        env = fake_cli.calls[-1][1]["env"]
        assert env.get("PLOBI_NONINTERACTIVE") == "1"

    def test_a_broken_spawn_says_so_instead_of_crashing(self, fake_cli, no_pip) -> None:
        fake_cli.raises = OSError("没有那个文件")
        out = _call("source_package_install", package="pkg-a")
        assert out["ok"] is False
        assert "起不来这一轮" in out["error"]


class TestWhatComesBack:
    """回包里的话要说清：装上没、带回来哪些信源、CLI 自己说了什么。"""

    def test_install_reports_the_sources_it_brought(self, fake_cli, no_pip) -> None:
        fake_cli.stdout = json.dumps({"ok": True, "package": "pkg-a",
                                      "summary": "装上了 pkg-a，多了一条信源：src_a。",
                                      "added": ["src_a"], "removed": [], "sources": ["src_a"],
                                      "output": ["Successfully installed pkg-a"]})
        out = _call("source_package_install", package="pkg-a")
        assert out["sources_added"] == ["src_a"]
        assert out["sources"] == ["src_a"]
        assert "多了一条信源" in out["summary"]
        assert out["output"] == ["Successfully installed pkg-a"]

    def test_a_failed_install_keeps_pips_own_words(self, fake_cli, no_pip) -> None:
        fake_cli.returncode = 1
        fake_cli.stdout = json.dumps({"ok": False, "package": "pkg-a",
                                      "summary": "装 pkg-a 失败（退出码 1）。上面是 pip 自己说的话。",
                                      "output": "ERROR: Could not find a version that satisfies pkg-a"})
        out = _call("source_package_install", package="pkg-a")
        assert out["ok"] is False
        assert "pip 自己说的话" in out["error"]
        assert "Could not find a version" in out["output"]

    def test_list_reports_what_is_installed(self, monkeypatch) -> None:
        from plobi.collectors import external

        rows = [{"distribution": "pkg_a", "entry_point": "pkg_a", "load_error": None, "source": "src_a"}]
        monkeypatch.setattr(external, "installed_external_packages", lambda: rows)
        out = _call("source_package_list")
        assert out["ok"] is True
        assert out["count"] == 1
        assert out["packages"][0]["distribution"] == "pkg_a"
        assert out["packages"][0]["source"] == "src_a"

    def test_list_says_so_when_it_cannot_read(self, monkeypatch) -> None:
        from plobi.collectors import external

        def _boom():
            raise RuntimeError("entry_points 读不出来")

        monkeypatch.setattr(external, "installed_external_packages", _boom)
        out = _call("source_package_list")
        assert out["ok"] is False
        assert "读外面装着的信源包失败" in out["error"]
