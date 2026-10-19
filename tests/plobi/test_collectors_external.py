"""「接一个自己的」那一格：包名闸门 · 装完刷新发现 · 外部那一档从注册表里真的消失。

跑法：``bash scripts/run_tests.sh tests/plobi/test_collectors_external.py -q -j 6``
（唯一入口、不传 ``--basetemp``，裁定 87）。

这一档测的是**产品面上唯一一个会执行任意第三方代码**的地方，所以闸门那组用例
是**反着钉**的：不只测"该拒的拒了"，还逐条说明**为什么拒**——只说"名字不合法"
的话，用户会一个一个试到装上为止，而每试一次就是一次任意代码执行。
"""

from __future__ import annotations

import pytest

from plobi.collectors import external, registry
from plobi.collectors.external import InvalidPackageName, validate_package_name


class TestTheNameGate:
    """只收**一个包名本身**。见 `plobi/collectors/external.py` 模块头第 2 条。"""

    @pytest.mark.parametrize("raw,expected", [
        ("my-pkg", "my_pkg"),
        ("my_pkg", "my_pkg"),
        ("My.Package", "my_package"),
        ("zju", "zju"),
        ("a", "a"),
        ("x1", "x1"),
        ("  padded  ", "padded"),
    ])
    def test_a_real_name_is_accepted_and_normalised(self, raw, expected):
        # PEP 503：`-` 与 `_` 与 `.` 互认、大小写不敏感。所以用户敲 `My-Package`
        # 拿到的必须是同一个东西，而不是"另一个叫这个的名字"。
        assert validate_package_name(raw) == expected

    @pytest.mark.parametrize("raw,why", [
        ("", "空"),
        ("   ", "空"),
        ("-e foo", "命令行开关"),
        ("--index-url=http://x", "命令行开关"),
        ("-pkg", "命令行开关"),
        ("git+https://x/y", "URL"),
        ("https://x/y.whl", "URL"),
        ("./local", "路径"),
        ("/abs/path", "路径"),
        ("../up", "路径"),
        ("C:\\pkg", "路径"),
        ("pkg @ file:///x", "URL"),
        ("pkg==1.2", "版本约束"),
        ("pkg>=1", "版本约束"),
        ("pkg~=1.0", "版本约束"),
        ("pkg[cli]", "extras"),
        ("pkg#os", "环境标记"),
        ("pkg;rm -rf /", "URL"),
        ("pkg|cat", "不是一个包名"),
        ("pkg&&whoami", "不是一个包名"),
        ("pkg?x=1", "不是一个包名"),
        ("pkg\n--index-url=http://evil", "URL"),
        ("..", "不是一个包名"),
        (".", "不是一个包名"),
        ("a" * 101, "比 100 长"),
    ])
    def test_everything_that_is_not_a_bare_name_is_refused(self, raw, why):
        with pytest.raises(InvalidPackageName) as raised:
            validate_package_name(raw)

        # **为什么**也要说清 —— 只说"名字不合法"的话，用户会一个一个试过去。
        assert why in str(raised.value), f"{raw!r} 的理由里没有「{why}」：{raised.value}"

    def test_the_gate_runs_before_pip_is_ever_reached(self, monkeypatch):
        """闸门在**调 pip 之前**，不是"装完发现装错了东西"。"""
        called = []
        monkeypatch.setattr(
            "plobi_cli.tools_config._pip_install", lambda args, **kw: called.append(args),
        )

        with pytest.raises(InvalidPackageName):
            external.install("evil==1.0")

        assert called == [], "闸门没拦住，pip 已经被叫了"


class TestRefreshAfterInstall:
    """装完 / 卸完**立刻**生效。不刷新的话用户看到的是"装成功了但那一条没出现"。"""

    def test_discovery_is_refreshable(self):
        before = set(registry.source_ids())

        report = external.refresh()

        assert set(report["sources"]) == before
        assert report["added"] == []
        assert report["removed"] == []

    def test_an_external_source_leaves_the_table_when_it_is_gone(self):
        """卸掉的那个要从表里**真的消失**，不然界面上留一条再也装不回来的僵尸行。"""
        registry.register(
            registry.SourceSpec(
                "ghostthirdparty",
                capabilities={key: None for key in registry.CAPABILITY_KEYS},
                category="custom",
            ),
        )
        registry._EXTERNAL_SOURCE_IDS.add("ghostthirdparty")

        assert "ghostthirdparty" in registry.source_ids()

        external.refresh()

        assert "ghostthirdparty" not in registry.source_ids()
        assert "ghostthirdparty" not in registry.external_source_ids()

    def test_the_builtin_ones_survive_a_refresh(self):
        """内置那几条**不清** —— 它们不在任何可卸载的包里，清了就没了。"""
        before = set(registry.source_ids())

        external.refresh()

        assert set(registry.source_ids()) == before
        assert registry.external_source_ids() == frozenset()

    def test_a_three_tables_way_refresh_is_idempotent(self):
        """反复刷不会重复登记、不会把内置的那些弄丢。"""
        for _ in range(3):
            external.refresh()

        assert "wechat" in registry.source_ids()
        assert "zju" in registry.source_ids()


class TestTheEntryPointGroup:
    def test_it_is_the_documented_group(self):
        # 漂了等于第三方那条路静默失效：第三方包声明的是这一组字符串。
        assert registry.EXTERNAL_ENTRY_POINT_GROUP == "plobi.collectors"

    def test_an_empty_installation_is_a_normal_reading_not_a_failure(self):
        """没装过不是错 —— 那是「零外部信源」这一配置的正常读数。"""
        assert external.installed_external_packages() == []


class TestTheCliSeam:
    """HTTP 那一层只 spawn，闸门在 CLI。这两条钉的是**分工**，不是实现细节。"""

    def test_the_http_layer_does_not_validate_the_name_itself(self):
        """如果 HTTP 那一层就拦了，就等于把闸门又挪回最近的那一跳。

        所以这里钉的是**它不拦**——它 spawn 出去，让 CLI 去说那句话。理由在
        `plobi/collectors/external.py` 模块头第 1 条。
        """
        source = open(sources_api_path(), encoding="utf-8").read()

        assert "validate_package_name" not in source
        # 也不许直接碰 pip。
        assert "_pip_install" not in source
        assert "subprocess" not in source

    def test_the_gate_lives_in_the_module_the_cli_imports(self):
        assert callable(validate_package_name)
        assert validate_package_name.__module__.startswith("plobi.collectors")


def sources_api_path() -> str:
    import plobi.collectors.sources_api as module

    return module.__file__