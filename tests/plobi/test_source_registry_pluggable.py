"""判据 3/4 的钉：**加一个平台不动公共层**，且能力位是声明式的、读不到就报错。

跑法：``bash scripts/run_tests.sh tests/plobi/test_source_registry_pluggable.py -q -j 6``
（唯一入口、不传 ``--basetemp``，裁定 87）。

对照测试是判据 3 的正身：`registry.register(假信源)` 之后**存储层一行未改**就能收这个信源，
撤掉就拒——这才叫可插拔，而不是"改完四处的 tuple 再加一个平台"。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from plobi.agenda import store
from plobi.collectors import registry

REPO = Path(__file__).resolve().parents[2]
FAKE = "fakecampus"


@pytest.fixture
def conn(tmp_path):
    connection = store.connect(tmp_path / "agenda.db")
    store.init_db(connection)
    yield connection
    connection.close()


@pytest.fixture
def fake_platform():
    """假 collector：只登记 + 声明能力位，不写任何数据、不碰任何公共层。"""
    registry.register(
        registry.SourceSpec(
            FAKE,
            {
                "history_backfill": True,
                "incremental_since": True,
                "supports_media": False,
                "requires_manual_setup": False,
                "authoritative_replace": False,
            },
        )
    )
    yield FAKE
    if FAKE in registry.source_ids():  # 用例里自己撤过就不重复撤（`unregister` 保持严格）
        registry.unregister(FAKE)


class TestAddingAPlatformDoesNotTouchTheCommonLayer:
    def test_new_source_is_accepted_with_store_untouched(self, conn, fake_platform) -> None:
        created = store.create_event(
            conn,
            title="刷卡进实验室",
            start_at="2026-10-11T09:00:00",
            kind="task",
            status="pending",
            source=fake_platform,
            evidence={"uid": "fake-1"},
        )
        assert created.source == fake_platform
        assert fake_platform in store.allowed_sources()

    def test_withdrawing_it_is_rejected_again(self, conn, fake_platform) -> None:
        registry.unregister(fake_platform)

        with pytest.raises(store.AgendaValidationError):
            store.create_event(
                conn,
                title="不该再被收下的条目",
                start_at="2026-10-11T09:00:00",
                kind="task",
                status="pending",
                source=fake_platform,
            )

    def test_no_frozen_tuple_left_in_the_store(self) -> None:
        """留一个 import 时算死的 ``SOURCES``＝第二真源（新登记的信源进不去，
        而它看起来还在）——判据 7 那个病的另一种形态，所以直接不要它。"""
        assert hasattr(store, "SOURCES") is False
        assert set(store.allowed_sources()) == {
            *registry.NON_COLLECTOR_SOURCES,
            *registry.source_ids(),
        }


class TestCapabilitiesAreDeclaredNotGuessed:
    def test_partial_declaration_is_refused(self) -> None:
        with pytest.raises(ValueError, match="少了能力位声明"):
            registry.SourceSpec("halfdone", {"history_backfill": True})

    def test_unknown_capability_name_is_refused(self) -> None:
        with pytest.raises(ValueError, match="不认识的能力位"):
            registry.SourceSpec("typo", {"histroy_backfill": True, **{k: False for k in registry.CAPABILITY_KEYS if k != "histroy_backfill"}})

        with pytest.raises(registry.UnknownCapability):
            registry.capability("wechat", "histroy_backfill")

    def test_hand_written_source_is_not_a_collector(self) -> None:
        assert "manual" in registry.NON_COLLECTOR_SOURCES
        assert "manual" not in registry.source_ids()

    def test_unmeasured_bit_is_not_silently_false(self) -> None:
        """sources-shape §3 与网关反着来的那一条：兜底猜一个值＝静默按某种口径跑。

        原先这一格举的是 ``zju.history_backfill``——判据 5 之后它**已经实测过了**（一发就回
        48 条），拿它当"未测"的例子会把这条测试变成一张过期的照片。现在举的这位仍是未测：
        「提交后这条会不会从清单里消失」没测、且"要不要完成即关闭"是产品决定。
        """
        assert registry.capability("zju", "authoritative_replace") is None
        with pytest.raises(registry.CapabilityUnmeasured):
            registry.require("zju", "authoritative_replace")

    def test_registered_but_not_collecting_is_visible_as_such(self) -> None:
        """钉钉在词表里、但没东西在采它——登记归口 ≠ 假数据占位。

        例子里的信源从「钉钉/学在浙大」换成只剩钉钉：教务 10-15 起既有开关也有执行方，
        再拿它当"登记了但没在采"的例子就是假读数（同一条事实不许登两处，这里以钉子里这句为准）。
        """
        assert "dingtalk" in store.allowed_sources()
        assert "dingtalk" not in registry.collecting_sources()
        assert registry.spec("dingtalk").collecting is False
        # 未实测的能力位仍然是 `None`（不许因为"开始采了"就顺手填成 False）：
        assert registry.spec("zju").capabilities["authoritative_replace"] is None

    def test_wechat_and_timetable_declare_their_real_shape(self) -> None:
        assert registry.require("wechat", "history_backfill") is True
        assert registry.require("wechat", "supports_media") is False
        assert registry.require("timetable", "incremental_since") is False
        # ingest 契约：本轮缺的不删 ⇒ 没有任何信源可以**声称**"没出现就当取消"。
        # 读 `capability()` 而不是 `require()`：教务那一格是 `None`（没实测，故意留白），
        # 而 `None` 不是"声称可以"——把它算成违规就是把"未测"读成"反对"。
        assert all(registry.capability(sid, "authoritative_replace") is not True
                   for sid in registry.source_ids())
        # 而这条留白**现在有了消费方**（sweep 每天叫它），所以它必须还是 None，
        # 谁实测出答案谁来改这一格，不许顺手填一个"看起来安全"的值。


class TestCommonLayerCarriesNoSourceNameBranch:
    """判据 4 的牙齿：公共层不许写 `if source == "微信"`，改成读能力表。

    ⚠️ **`manual` 那一档是例外，而且这个例外是设计不是漏洞**（裁定 103 三）：它不是平台，
    是**来源档位**——"人手写的条目不被采集器覆盖"这类规则不该、也没法做成能力位，
    因为不会有 collector 来声明它。所以下面只禁四个真信源名，别"顺手统一"把它也收进来。
    """

    FORBIDDEN = re.compile(r'(==|!=|\bin)\s*\(?\s*"(wechat|timetable|dingtalk|zju)"')

    def scanned(self):
        # 规划层（planning.py）今晚起也算公共层：它新加的"无主 DDL 作业块"与项目关联口径
        # 一律读字段，不许按信源名字分支（判据 4）。
        roots = [
            REPO / "plobi" / "collectors",
            REPO / "plobi" / "agenda" / "store.py",
            REPO / "plobi" / "agenda" / "planning.py",
        ]
        for root in roots:
            if root.is_file():
                yield root
                continue
            for path in root.rglob("*.py"):
                if "__pycache__" in path.parts or path.name == "registry.py":
                    continue
                yield path

    def test_no_source_name_branch_in_the_collection_or_storage_layer(self) -> None:
        hits = [
            f"{path.relative_to(REPO)}:{number}"
            for path in self.scanned()
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            )
            if self.FORBIDDEN.search(line)
        ]
        assert hits == [], f"公共层按信源名字分支了（判据 4 要的是读能力位）：{hits}"
