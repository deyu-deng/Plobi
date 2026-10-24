r"""钉钉那条在本机成不成立（`WP-SOURCE-LASTMILE` 规则 7–8）—— 一只**只做存在性判断**的探针。

跑法：`bash scripts/run_tests.sh tests/plobi/test_dingtalk_localdb_probe.py -q`

它存在的理由是一颗假故障：屏上那一行今天画 `error/internal`（「本机采集组件暂未暂未就绪」那种
"我们的产品坏了"），而真话是**这台机器给不出它要的前提**。这一档因此只钉两句：
①四种现场各给出**对的哪一种说法**；②它不许把自己变成第二套判据——探针说"成立"而仍然没人
构造得出执行方，那一颗红点**继续是红**（次序在 `sources_api.status_of`，测在
`test_sources_api_surface.py::TestTheProbeReplacesAFalseFault`）。

不解密、不读正文、不碰进程内存、不联网：这里连一个字节的内容都不去看，只做 `exists` / `is_dir`。
"""

from __future__ import annotations

import pytest

from plobi.collectors import registry
from plobi.collectors.dingtalk import localdb


def make_profile(root, name, *, db=False, user_config=False):
    profile = root / name
    profile.mkdir(parents=True)
    if db:
        (profile / localdb.DB_NAME).write_bytes(b"")
    if user_config:
        (profile / localdb.USER_CONFIG_NAME).write_text("", encoding="utf-8")
    return profile


class TestTheFourSites:
    """四种现场 —— 每种都要说得出**那一种**话，不许都塌成一句"未连接"。"""

    def test_nothing_installed_and_no_data_root(self, tmp_path) -> None:
        result = localdb.probe(roots=[tmp_path / "nope"], installed=lambda: False)

        assert result["code"] == "client_not_installed"
        assert result["ready"] is False

    def test_installed_but_never_logged_in(self, tmp_path) -> None:
        """这台 10-24 的真现场：客户端在 `D:\Software\DingDing`，而数据根下面只有安装器的 `log`。

        "装了但没登录"与"没装"是两句不同的话：前者用户自己做一件事就解开了，后者要找管理员。
        """
        root = tmp_path / "DingTalk"
        (root / "log").mkdir(parents=True)

        result = localdb.probe(roots=[root], installed=lambda: False)

        assert result["code"] == "client_not_logged_in"

    def test_a_profile_that_never_got_a_database(self, tmp_path) -> None:
        root = tmp_path / "DingTalk"
        make_profile(root, "8f3a2b1c0d9e8f7a6b5c_v3")

        result = localdb.probe(roots=[root], installed=lambda: True)

        assert result["code"] == "local_store_missing"

    def test_the_local_store_is_there(self, tmp_path) -> None:
        """这一支今天在这台机器上**没人能触发**（客户端没登录过）——它是下一刀（真解密 + 落池）的入口条件。"""
        root = tmp_path / "DingTalk"
        profile = make_profile(root, "8f3a2b1c0d9e8f7a6b5c_v3", db=True, user_config=True)

        result = localdb.probe(roots=[root], installed=lambda: False)

        assert result["ready"] is True
        assert result["code"] == "local_store_found"
        assert str(profile.name) in result["detail"]
        assert "v3" in result["detail"], "第几代加密是下一刀开工前必须先知道的那一格（V2/V3 密钥推导不同）"

    def test_an_unreadable_disk_says_so_instead_of_throwing(self, monkeypatch) -> None:
        """探针**永不抛**：读不到盘也是"前提不在"，那一屏不该因此变红或变白屏。"""
        def boom(root):
            raise OSError("盘掉了")

        monkeypatch.setattr(localdb, "_profile_dirs", boom)

        assert localdb.probe(roots=["x"], installed=lambda: True)["code"] == "local_store_missing"


class TestTheShape:
    def test_the_return_is_the_three_keys_the_screen_reads(self, tmp_path) -> None:
        result = localdb.probe(roots=[tmp_path], installed=lambda: False)

        assert set(result) == {"ready", "code", "detail"}
        assert isinstance(result["ready"], bool)

    def test_a_v2_directory_is_recognised_as_v2(self, tmp_path) -> None:
        """V2 的目录名**就是** UID，V3 的不是（调研件 §4.2）——版本判据不能靠目录名长度猜。"""
        root = tmp_path / "DingTalk"
        make_profile(root, "666165872_v2", db=True)

        assert localdb.probe(roots=[root], installed=lambda: False)["detail"].endswith("v2）")

    def test_a_log_directory_is_not_a_profile(self, tmp_path) -> None:
        """`log` 那一格长得也像目录名，但它不是资料目录——把它认成"登录过"就是假读数。"""
        root = tmp_path / "DingTalk"
        (root / "log").mkdir(parents=True)

        assert localdb._profile_dirs(root) == []


class TestItIsDeclaredNotBranchedList:
    """公共层不许按信源名字判"钉钉特殊"（判据 3）：这一格是**声明**。"""

    def test_the_dingtalk_spec_declares_a_probe(self) -> None:
        registry.ensure_discovered()

        assert registry.spec("dingtalk").probe is localdb.probe

    def test_sources_that_declare_none_carry_no_such_row(self) -> None:
        """不是给每条源都补一个说法：没声明就是 `None`，界面一个字都不多画。"""
        registry.ensure_discovered()

        assert all(registry.spec(sid).probe is None
                   for sid in ("zju", "zdbk", "wechat", "timetable"))

    def test_a_probe_is_a_no_arg_callable(self) -> None:
        """注册表那一条通用契约：`probe()` 无参可调用、回那三个键（第三方包同样能声明）。"""
        spec = registry.SourceSpec("probeshape", capabilities=dict.fromkeys(registry.CAPABILITY_KEYS, False),
                                   category="system", probe=localdb.probe)
        registry.register(spec, replace=True)

        try:
            value = registry.spec("probeshape").probe()
        finally:
            registry.unregister("probeshape")

        assert set(value) == {"ready", "code", "detail"}
