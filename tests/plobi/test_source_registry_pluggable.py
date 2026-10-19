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
    """假 collector：只登记 + 声明能力位 + 声明分类，不写任何数据、不碰任何公共层。"""
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
            category="website",
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
            registry.SourceSpec("halfdone", {"history_backfill": True}, category="website")

    def test_unknown_capability_name_is_refused(self) -> None:
        with pytest.raises(ValueError, match="不认识的能力位"):
            registry.SourceSpec("typo", {"histroy_backfill": True, **{k: False for k in registry.CAPABILITY_KEYS if k != "histroy_backfill"}}, category="website")

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


class TestCategoriesAreDeclaredNotGuessed:
    """分类与能力位同一套路（`specs/source-layer.md` §2）：必填、闭集、不许猜。

    这几条钉的是**形状**而不是"某条恰好归某类"——后者是数据，变了就该改，不该当判据。
    """

    def test_every_registered_source_declares_a_category(self) -> None:
        for source_id in registry.source_ids():
            assert registry.source_category(source_id) in registry.CATEGORY_SLUGS

    def test_a_source_without_a_category_cannot_even_be_built(self) -> None:
        """缺位在**构造那一刻**就炸，不等注册——默认值会让漏写的那格悄悄过去。"""
        with pytest.raises(TypeError):
            registry.SourceSpec("nocategory", {key: False for key in registry.CAPABILITY_KEYS})

    def test_a_category_outside_the_closed_set_is_refused(self) -> None:
        """闭集不接受"先放着"，也不接受拼错——加第七个值要先改规格 §2。"""
        with pytest.raises(registry.UnknownCategory, match="不在闭集里"):
            registry.SourceSpec("wip", {key: False for key in registry.CAPABILITY_KEYS}, category="important")

        with pytest.raises(registry.UnknownCategory):
            registry.SourceSpec("typo", {key: False for key in registry.CAPABILITY_KEYS}, category="websit")

    def test_the_closed_set_carries_no_ranking(self) -> None:
        """平权约束在这一格是硬的：闭集里没有任何一个值表示"更重要"。

        钉住**形状**——将来有人加一个 `priority`／`tier` 值，这条当场红。那不是这条测试
        在管具体信源归哪一类，是不许分类这一维变回等级表（裁定 151 + 规格 §2.1）。
        """
        for banned in ("tier", "weight", "rank", "priority", "primary", "important", "major", "minor"):
            assert banned not in registry.SOURCE_CATEGORIES

    def test_registry_module_names_no_source_at_all(self) -> None:
        """**解耦的判据就是这一条**：注册表那个文件里不许出现任何信源 id。

        包括**注释里**——注释里提一嘴就等于这个文件仍然知道那个信源，而"这个文件
        知道哪些信源"正是解耦要拆掉的东西（加一条信源要改它 = 没解耦）。所以这里
        `git grep` 式的扫描连注释一起扫。

        为什么不是"扫 `SourceSpec(` 调用点"：那会漏掉一个只写在注释里的名字，而人读
        一个文件时**注释算它知道的东西**。
        """
        registry_source = (REPO / "plobi" / "collectors" / "registry.py").read_text(encoding="utf-8")
        source_ids = registry.source_ids()

        # 先确认这四类真在注册表里（否则下面的循环空转，一条都测不到）
        assert {"dingtalk", "timetable", "wechat", "zju"} <= set(source_ids)

        for source_id in source_ids:
            assert source_id not in registry_source, f"registry.py 里仍然写着 {source_id}"

    def test_every_collector_package_declares_its_own_spec(self) -> None:
        """每个内置采集器包**自己**报自己的形状——这是"加一条＝加一个包"的牙齿。

        反过来也钉：注册表那个目录里的每个包都要么有 ``SPEC``、要么明确是公共层子包。
        这里只钉前一半（后一半是 `:mod:` 那些不属于采集器的子包，见发现器的注释）。
        """
        import plobi.collectors as package

        declared = set()

        for name in ("chatlog", "dingtalk", "timetable", "zju"):
            module = __import__(f"plobi.collectors.{name}", fromlist=["SPEC"])

            assert hasattr(module, "SPEC"), f"{name} 没有报出自己的 SPEC"
            declared.add(module.SPEC.source_id)

        assert declared == set(registry.source_ids())
        assert package.__doc__  # 包自己的说明还在（发现器不认识它时按"公共层子包"放过）

    def test_grouping_keeps_empty_categories(self) -> None:
        """空分类也要出现——"这一类还没接任何信源"也是一句话，不该由界面自己编。

        六个分类逐个在，且分组里的每个 id 都真属于它那一类。
        """
        grouped = registry.sources_by_category()

        assert set(grouped) == set(registry.SOURCE_CATEGORIES)
        for category, members in grouped.items():
            for member in members:
                assert registry.source_category(member) == category


class TestDiscoveryCannotFailSilently:
    """三条施工约束里最要命的那条（`specs/source-layer.md` §3.1）：

    发现没跑 ⇒ `source_ids()` 回空元组 ⇒ sweep「成功」地跑完、零条进账、
    `/api/collect/sources` 回一个空名单。**那是最危险的静默失败**，形状和
    `no_builder` 当年被藏起来的那一次一模一样。这几条钉的是它不会那样。
    """

    def test_a_cold_process_first_read_is_never_empty(self) -> None:
        """**冷进程第一次读**就必须拿到名单，不靠"谁记得先调一次"。

        为什么必须开子进程：这条性质只在"从没被发现过"的状态下成立，而本档的其余用例
        跑在同一个已经被别的用例发现过的进程里。`importlib.reload(registry)` **测不到
        它**——reload 只重置注册表自己的模块级状态，那四个采集器包仍在 `sys.modules`
        里（Python 不重跑已缓存模块的顶层代码），所以它们不会重新 `register()`，
        reload 之后读到的必然是空名单，与生产无关。

        判据是**这一条命令在真冷进程上回什么**，而不是在同进程里清一次标志。
        """
        import subprocess
        import sys

        script = (
            "import sys; sys.path.insert(0, '.');"
            "from plobi.collectors import registry;"
            "print(len(registry.source_ids()))"
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            cwd=REPO,
            text=True,
            timeout=120,
        )

        assert completed.returncode == 0, completed.stderr
        # 0 = 空名单 = 那条静默失败；4 = 现在注册表里的四条。
        assert int(completed.stdout.strip()) == 4, completed.stdout

    def test_a_broken_package_is_reported_not_swallowed(self) -> None:
        """某个包没载入必须**出声**，不许吞成"这条不存在"。

        吞掉的形状：一条信源因为一个 import 错误消失了，用户看到的是"今天没东西
        进来"，永远问不到"某个包没载入"。所以失败要带包名与原始异常抛出来。
        """
        assert issubclass(registry.CollectorDiscoveryError, RuntimeError)

        failure = registry.CollectorDiscoveryError([("plobi.collectors.broken", "ImportError: no such thing")])

        assert "plobi.collectors.broken" in str(failure)
        assert "ImportError" in str(failure)
        # 一次全抛，不是抛第一条：三条包没载入与一条包没载入是两种不同的排障。
        two = registry.CollectorDiscoveryError([("a", "x"), ("b", "y")])

        assert set(two.failures) == {"a", "b"}

    def test_discovery_is_idempotent(self) -> None:
        """反复调不会重复登记、不会抛"已登记"。

        `register()` 对重复登记本来就会抛，所以这条钉的是**发现**自己只干活一次
        （哪怕某个包在导入时回头读这张表，递归进来也不能把同一个包导两次）。
        """
        before = registry.source_ids()

        for _ in range(3):
            registry.ensure_discovered()

        assert registry.source_ids() == before

    def test_the_external_group_is_the_documented_one(self) -> None:
        """第三方装信源走的那一组名不许漂——漂了等于第三方那条路静默失效。

        这钉的是**组名这一个字符串**（它同时出现在本仓 `pyproject.toml` 的
        `[project.entry-points]` 与第三方包自己的声明里，两边必须逐字相同）。
        """
        assert registry.EXTERNAL_ENTRY_POINT_GROUP == "plobi.collectors"


class TestRegisteredSourcesDeclareTheirRealShape:
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

    def test_wechat_and_zju_sit_in_different_categories(self) -> None:
        """这两条的分属是**连接器形状**那一维，不是数据形状也不是重要性：

        微信骑本机已登录的客户端（对话流），教务只有一个 Web 系统（地址 + 账密）。
        钉钉与微信同族；课表归「本地」是因为注册表注记写的是"用户往某目录放 .ics"。
        """
        assert registry.source_category("wechat") == "message"
        assert registry.source_category("dingtalk") == "message"
        assert registry.source_category("zju") == "website"
        assert registry.source_category("timetable") == "local"


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
