r"""钉钉那条在本机成不成立（`WP-DINGTALK-LOCAL-STORE`）—— 一只**只做存在性判断**的探针。

跑法：`bash scripts/run_tests.sh tests/plobi/test_dingtalk_localdb_probe.py -q`

它存在的理由是一颗假故障：屏上那一行从前画 `error/internal`（"我们的产品坏了"），
而真话是**这台机器给不给得出那条路的前提**。现在钉钉有三条路（甲 · 官方 dws CLI、
乙 · 企业内部应用、丙 · 桌面客户端那份本机库），甲乙都卡在别人的组织，丙卡在这台登录没登录——
所以探针今天要说的是**三条路各自的状态**，而不是一句"未连接"。

它不解密、不读正文、不碰进程内存、不联网；连一个字节的内容都不去看，只做 `exists`/`is_dir`
与"那两个键在不在"（只看键名，值一个字节都不读出来）。
"""

from __future__ import annotations

import pytest

from plobi.collectors import registry
from plobi.collectors.dingtalk import localdb, store


@pytest.fixture(autouse=True)
def _no_real_machine_reads(tmp_path, monkeypatch) -> None:
    """默认把所有落点钉进 tmp：这一档不许碰这台真 `%APPDATA%`，也不许读真的 `.env`。"""
    monkeypatch.setenv("PLOBI_DINGTALK_DATA_DIR", str(tmp_path / "DingTalk"))
    monkeypatch.delenv("DINGTALK_CLIENT_ID", raising=False)
    monkeypatch.delenv("DINGTALK_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(localdb, "_org_routes", lambda: {"app": False, "cli": False})


class TestTheRoutes:
    """四种现场 —— 每种都要说得出**那一种**话，不许都塌成一句"未连接"。"""

    def test_nothing_there_at_all(self, tmp_path) -> None:
        result = localdb.probe()

        assert result["code"] == "client_not_installed"
        assert result["ready"] is False
        assert "CLI 未装" in result["detail"] and "企业内部应用 未配" in result["detail"]

    def test_installed_but_never_logged_in(self, tmp_path) -> None:
        """这台 10-24 落纸时的真现场：根目录在（安装器建的），但里面没有资料目录。"""
        (tmp_path / "DingTalk" / "log").mkdir(parents=True)

        assert localdb.probe()["code"] == "client_not_logged_in"

    def test_a_logged_in_profile_without_the_database(self, tmp_path) -> None:
        (tmp_path / "DingTalk" / "e34da8455d4779ae5786_v3").mkdir(parents=True)

        result = localdb.probe()

        assert result["code"] == "local_store_missing"

    def test_the_local_store_is_in_place(self, tmp_path) -> None:
        """丙路成立。这句只报**前提**、不报结果——库在位不等于已经采到过一条东西，
        这一层限制必须由探针自己说出来，否则下一位读的人会以为 "ready" 就是可用。

        10-24 准入翻成「默认采、逐扇可关」（Code `03b4510f`）之后，那句「要逐扇会话点名」
        会把人指回一个已经删掉的入口 ⇒ 老话一并钉成"不许回来"。
        """
        profile = tmp_path / "DingTalk" / "e34da8455d4779ae5786_v3" / "DBFiles"
        profile.mkdir(parents=True)
        (profile / "dingtalk.db").write_bytes(b"\x00" * 4096)

        result = localdb.probe()

        assert result["ready"] is True and result["code"] == "local_store_found"
        assert "默认全采" in result["detail"] and "不想要哪一扇" in result["detail"]
        assert "点名" not in result["detail"]

    def test_an_unreadable_disk_says_so_instead_of_throwing(self, monkeypatch) -> None:
        """探针**永不抛**：读不到盘也是"前提不在"，那一屏不该因此变红或白屏。"""
        def boom(roots):
            raise OSError("盘掉了")

        monkeypatch.setattr(store, "profiles", boom)

        assert localdb.probe()["code"] == "local_store_missing"


class TestTheOrgRoutesAreReportedNotGuessed:
    def test_both_configured_routes_are_named(self, monkeypatch) -> None:
        monkeypatch.setattr(localdb, "_org_routes", lambda: {"app": True, "cli": True})

        detail = localdb.probe()["detail"]

        assert "CLI 已装" in detail and "企业内部应用 已配" in detail

    def test_the_org_route_alone_does_not_make_the_source_ready(self, monkeypatch) -> None:
        """今天**没有**一条采集器走甲/乙那两路（丙才有实现体），所以组织侧配了也不许把
        `ready` 翻真——那是一颗画在屏上的假绿。"""
        monkeypatch.setattr(localdb, "_org_routes", lambda: {"app": True, "cli": True})

        assert localdb.probe()["ready"] is False


class TestItIsDeclaredNotBranchedListed:
    """公共层不许按信源名字判"钉钉特殊"（判据 3）：这一格是**声明**。"""

    def test_the_dingtalk_spec_declares_a_probe(self) -> None:
        registry.ensure_discovered()

        assert registry.spec("dingtalk").probe is localdb.probe

    def test_sources_that_declare_none_carry_no_such_row(self) -> None:
        registry.ensure_discovered()

        # 名单照现状，不照印象：微信 10-25 起也声明了探针（四种缺法要分得开，裁定 203 第 2 条），
        # 所以这里只剩"确实没有那一只手"的三家。判据本身没松：**没声明就不许画那一行**。
        assert all(registry.spec(sid).probe is None for sid in ("timetable", "zdbk", "zju"))
        assert registry.spec("wechat").probe is not None

    def test_the_probe_shape_is_the_three_keys_the_screen_reads(self) -> None:
        spec = registry.SourceSpec("probeshape", capabilities=dict.fromkeys(registry.CAPABILITY_KEYS, False),
                                   category="system", probe=localdb.probe)
        registry.register(spec, replace=True)

        try:
            value = registry.spec("probeshape").probe()
        finally:
            registry.unregister("probeshape")

        assert set(value) == {"ready", "code", "detail"}
