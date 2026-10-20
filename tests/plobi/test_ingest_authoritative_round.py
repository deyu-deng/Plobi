"""`authoritative_replace` 那一格**终于有人执行了**（裁定 191 一）。

跑法：`bash scripts/run_tests.sh tests/plobi/test_ingest_authoritative_round.py -q`（唯一入口）。
零真网络；日程库钉在 `tmp_path`。

这一档钉的是"公共层按声明办事"的形状，所以它**按能力位取源、不按名字取源**：
测试里那三条假信源（权威 / 附加式 / 未实测）就是全部输入。真正要防的是两件事：

1. 又长出一个 zju 专用的对账器（裁定 191 判据 5 专门拦这个）—— 所以这一档只在 `ingest` 里，
   而 `git diff` 里不许出现 `*recon*` / `*submitted*` 那种新模块；
2. 把"本轮没出现"当成"可以删行"。裁定 42.2 第 5 项：归档只标失效。所以每一断言都在数**行还在不在**，
   而"不再被排"靠的是 `completed_at` —— 排程那侧本来就 `include_completed=False`
   （`planning.py:382` 那句注释写的病根就是"写完的作业第二天又被排进来"）。
"""

from __future__ import annotations

import pytest

from plobi.agenda.service import AgendaService
from plobi.collectors import registry
from plobi.collectors.base import Occurrence
from plobi.collectors.ingest import authoritative_round
from plobi.collectors.registry import SourceSpec

AUTH_CAPS = {
    "history_backfill": True, "incremental_since": False, "supports_media": False,
    "requires_manual_setup": False, "authoritative_replace": True,
}
ADDITIVE_CAPS = {**AUTH_CAPS, "authoritative_replace": False}
UNMEASURED_CAPS = {**AUTH_CAPS, "authoritative_replace": None}


@pytest.fixture()
def sources():
    registered = []

    def _add(*specs: SourceSpec) -> None:
        for spec in specs:
            registry.register(spec, replace=True)
            registered.append(spec.source_id)

    yield _add
    for source_id in registered:
        registry.unregister(source_id)


@pytest.fixture()
def service(tmp_path) -> AgendaService:
    return AgendaService(tmp_path / "agenda.db")


def occurrence(source: str, uid: str, *, kind: str = "ddl", title: str = "作业") -> Occurrence:
    return Occurrence(source=source, external_id=uid, title=f"{title}-{uid}",
                      start_at="2026-10-22T08:00:00", kind=kind, evidence={"uid": uid})


def seed(service: AgendaService, source: str, uid: str, *, kind: str = "ddl") -> str:
    return service.create_from_source(title=f"作业-{uid}", start_at="2026-10-22T08:00:00",
                                      kind=kind, source=source, evidence={"uid": uid}).id


def rows(service: AgendaService, source: str):
    from plobi.agenda import store

    conn = store.connect(service.db_path)
    try:
        return {event.id: event for event in store.list_events(conn, source=source,
                                                               include_cancelled=True,
                                                               include_plan_artifacts=True)}
    finally:
        conn.close()


class TestCloseAbsentOnlyWhenTheSourceDeclaredIt:
    def test_absent_row_is_marked_done_and_the_row_survives(self, sources, service) -> None:
        sources(SourceSpec("authsrc", AUTH_CAPS, category="website"))
        kept = seed(service, "authsrc", "1")
        gone = seed(service, "authsrc", "2")

        report = authoritative_round(service, [occurrence("authsrc", "1")])

        assert report["closed"] == [gone] and report["reopened"] == []
        events = rows(service, "authsrc")
        assert len(events) == 2, "**不删行**：判据 3 数的就是这个"
        assert events[gone].completed_at, "本轮不在清单 ⇒ 标失效"
        assert not events[kept].completed_at
        assert events[gone].status == events[kept].status, "status 一格不动，改的是完成态"

    def test_a_reappearing_row_is_un_marked(self, sources, service) -> None:
        """反悔路径：缺席是**推断**，一次抖动不该把作业判死。"""
        sources(SourceSpec("authsrc", AUTH_CAPS, category="website"))
        uid = seed(service, "authsrc", "7")

        authoritative_round(service, [])  # 空轮不关（见下面那条断言），这里先造一个已关的状态
        authoritative_round(service, [occurrence("authsrc", "ignored")])
        assert rows(service, "authsrc")[uid].completed_at

        report = authoritative_round(service, [occurrence("authsrc", "7")])

        assert report["reopened"] == [uid]
        assert not rows(service, "authsrc")[uid].completed_at

    def test_an_additive_source_has_not_a_single_row_touched(self, sources, service) -> None:
        sources(SourceSpec("addsrc", ADDITIVE_CAPS, category="social"))
        uid = seed(service, "addsrc", "1")

        report = authoritative_round(service, [occurrence("addsrc", "other")])

        assert report["closed"] == [] and not rows(service, "addsrc")[uid].completed_at
        assert "not_authoritative" in report["skipped"]["addsrc"]

    def test_an_unmeasured_capability_refuses_to_act(self, sources, service) -> None:
        """`None` 不等于 `False`，也不等于"先当权威跑一下试试"。"""
        sources(SourceSpec("unsrc", UNMEASURED_CAPS, category="website"))
        uid = seed(service, "unsrc", "1")

        report = authoritative_round(service, [occurrence("unsrc", "other")])

        assert report["closed"] == [] and not rows(service, "unsrc")[uid].completed_at
        assert "unmeasured" in report["skipped"]["unsrc"]


class TestGuards:
    def test_an_empty_round_closes_nothing(self, sources, service) -> None:
        """48 → 0 那种"整条清单空了"更像选择器坏了，不像作业清零。宁可晚一轮再关。"""
        sources(SourceSpec("authsrc", AUTH_CAPS, category="website"))
        uid = seed(service, "authsrc", "1")

        report = authoritative_round(service, [])

        assert report["closed"] == [] and not rows(service, "authsrc")[uid].completed_at
        assert "empty_round" in report["skipped"]["(round)"]

    def test_only_the_classes_this_round_represent_are_reconciled(self, sources, service) -> None:
        """这一轮拉的是 homework（`ddl`）⇒ 同一条信源的 `task` 行与它无关，一个字都不动。

        没有这一条，"本轮未出现"就从"这一类里没出现"漂成"这家整体没提到它"，
        而下一个人没法从读数里看出漂了。
        """
        sources(SourceSpec("authsrc", AUTH_CAPS, category="website"))
        other_kind = seed(service, "authsrc", "9", kind="task")
        absent_ddl = seed(service, "authsrc", "8", kind="ddl")

        report = authoritative_round(service, [occurrence("authsrc", "1", kind="ddl")])

        events = rows(service, "authsrc")
        assert report["closed"] == [absent_ddl]
        assert not events[other_kind].completed_at, "本轮没代表过 task 这一类 ⇒ 不许动它"

    def test_the_scheduler_own_rows_are_out_of_reach(self, sources, service) -> None:
        """排程自己造的"计划项行"（裁 175）不是素材，永远不该被采集轮的缺席判定关掉。"""
        from plobi.agenda.store import PLAN_ARTIFACT_KEY

        sources(SourceSpec("authsrc", AUTH_CAPS, category="website"))
        artifact = service.create_from_source(
            title="焊接作业一", start_at="2026-10-20T19:10:00", kind="ddl", source="authsrc",
            evidence={PLAN_ARTIFACT_KEY: "plan", "uid": "plan-artifact-1"},
        ).id

        report = authoritative_round(service, [occurrence("authsrc", "1")])

        assert report["closed"] == []
        assert not rows(service, "authsrc")[artifact].completed_at

    def test_uid_is_the_identity_not_the_title(self, sources, service) -> None:
        """去重键是站方那个 `uid`（`_row_keys` 那一套），标题改过字也不该被当成"消失了"。"""
        sources(SourceSpec("authsrc", AUTH_CAPS, category="website"))
        uid = seed(service, "authsrc", "44")

        report = authoritative_round(service, [occurrence("authsrc", "44", title="改过名的作业")])

        assert report["closed"] == [] and report["reopened"] == []
        assert not rows(service, "authsrc")[uid].completed_at
