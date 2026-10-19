"""`pool_read` / `pool_mark` 的判据（WP-POOL-READ-ANY）。

跑法：``bash scripts/run_tests.sh -j 1 tests/plobi/test_pool_tool.py``

钉的是四件事，全部是关系不是快照：
① 参数面里没有 `action`／`intent`／`mode`／`purpose` 这类选分支的东西（裁 185）；
② 两个不同角色经**同一套真实注册面**各调一次 `pool_read`，拿到**同一条**，且带
   `source`＋`external_id`＋**正文**（只回 id 或只回 "ok" 都不算）；
③ `pool_mark` 只是一个记号——标完那条**还在池里**，别人照样读得到；
④ 读不到 / 参数不齐 / 池里查无此条，都要说得出为什么，不许回一个空列表装成"池里没有"。

夹具只往**临时池**里造条目（`plobi.pool.store.path` 钉到 tmp）：判据④「不许造样本数据」
禁的是往真池插，不是禁测试——真条目由采集侧进来，人工体验轮另算。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

from plobi.pool import PoolItem, store  # noqa: E402
from tools import pool_tool  # noqa: E402
from tools.registry import registry  # noqa: E402


@pytest.fixture
def pool(tmp_path, monkeypatch):
    """把池子钉到临时文件（与 `test_pool_store.py` 同一手法），并放两条真形状的信源。"""
    db = tmp_path / "pool.db"
    monkeypatch.setattr(store, "path", lambda: db)
    store.land(
        [
            PoolItem(source="wechat", external_id="m1", kind="message",
                     title="组会改期", body="明天下午三点的组会改到四点半",
                     occurred_at="2026-10-19T10:00:00"),
            PoolItem(source="dingtalk", external_id="d1", kind="ddl",
                     title="实验报告", body="实验三报告 10-20 23:59 截止",
                     occurred_at="2026-10-18T09:00:00"),
        ]
    )
    return db


def _dispatch(name: str, args: dict) -> dict:
    """走注册表里那条 handler——就是模型调用时真正经过的那一步（不是直接摸模块函数）。"""
    entry = registry.get_entry(name)
    assert entry is not None, f"{name} 没注册上"
    return json.loads(entry.handler(args))


def _read(**filters) -> dict:
    return _dispatch("pool_read", filters)


def _mark(**args) -> dict:
    return _dispatch("pool_mark", args)


# ------------------------------------------------------------------ 判据①


def test_no_branch_picking_parameter_exists_on_either_atom():
    """裁 185：参数只许是筛选条件，`action`/`intent`/`mode`/`purpose` 一律不许有。"""
    banned = {"action", "intent", "mode", "purpose"}
    for name, schema in (("pool_read", pool_tool._POOL_READ_SCHEMA),
                         ("pool_mark", pool_tool._POOL_MARK_SCHEMA)):
        props = set(schema["parameters"]["properties"])
        assert not (props & banned), f"{name} 带了选分支的参数：{props & banned}"


def test_the_descriptions_do_not_point_at_any_downstream_destination():
    """工具描述里不许出现「待确认／落库／日程」——它读的是信息，不是某个下游的入口。"""
    for schema in (pool_tool._POOL_READ_SCHEMA, pool_tool._POOL_MARK_SCHEMA):
        blob = json.dumps(schema, ensure_ascii=False)
        for word in ("待确认", "落库", "日程"):
            assert word not in blob, f"描述里出现了 {word!r}"


def test_the_two_new_columns_come_back_on_the_entry_and_filter_the_read(pool):
    """裁 187 那两列要穿过读手：既看得见（带在条目上），也筛得动（是筛选条件）。"""
    store.land([
        PoolItem(source="wechat", external_id="g1", kind="message",
                 title="群里的通知", body="周四下午换教室",
                 visibility="group", conversation="工程训练·课群组",
                 occurred_at="2026-10-19T11:00:00"),
    ])

    hit = _read(conversation="工程训练·课群组")
    assert hit["ok"] is True and hit["count"] == 1
    assert hit["items"][0]["external_id"] == "g1"
    assert hit["items"][0]["visibility"] == "group"
    # 同一枚工具、没给这一筛选条件时，那两条旧条目照样读得到（旧的留空不等于被筛掉）
    everything = _read()
    assert {i["external_id"] for i in everything["items"]} == {"m1", "d1", "g1"}
    assert all("conversation" in i and "visibility" in i for i in everything["items"])


def test_visibility_and_conversation_are_advertised_as_filters():
    props = pool_tool._POOL_READ_SCHEMA["parameters"]["properties"]
    assert {"visibility", "conversation"} <= set(props)


# ------------------------------------------------------------------ 判据②


def test_two_different_roles_read_the_same_row_with_its_body(pool):
    """两个角色各调一次：同一条、带 source + external_id + 正文（只回 "ok" 不算）。"""
    first = _read(source="wechat")
    second = _read(contains="组会")

    assert first["ok"] and second["ok"], (first, second)
    assert [i["external_id"] for i in first["items"]] == ["m1"]
    assert [i["external_id"] for i in second["items"]] == ["m1"]
    assert first["items"][0]["source"] == "wechat"
    assert first["items"][0]["title"] == "组会改期"
    assert "组会" in first["items"][0]["body"], "正文没带出来 = 读手白建"
    assert first["items"] == second["items"]


def test_both_atoms_are_available_to_every_role_not_just_one_toolset():
    """「配给所有角色」= 在默认束里（每个平台的基束都继承它），不是挂进某个角色的工具集。"""
    from toolsets import TOOLSETS, _PLOBI_CORE_TOOLS

    assert {"pool_read", "pool_mark"} <= set(_PLOBI_CORE_TOOLS)
    assert TOOLSETS["pool"]["tools"] == ["pool_read", "pool_mark"]


def test_a_read_only_platform_and_a_cron_platform_both_get_the_atoms(pool):
    """按平台取工具集那条路上也不许少它——否则只有某一类 Agent 看得见信息。

    这一层返回的是**工具集名**（`pool`），两枚原子在不在 `pool` 里由上一条测钉。
    """
    from plobi_cli.tools_config import _get_platform_tools

    for platform in ("telegram", "cron"):
        tools = set(_get_platform_tools({}, platform))
        assert "pool" in tools, f"{platform} 这个角色拿不到信息池那组手：{sorted(tools)}"


def test_marking_a_row_is_a_marker_and_the_row_survives(pool):
    """判据③：不许做成"读一条删一条"——标过之后条目照旧读得到。"""
    marked = _mark(source="wechat", external_id="m1")
    assert marked["ok"] is True

    untouched = _read(source="dingtalk")
    assert [i["external_id"] for i in untouched["items"]] == ["d1"]

    marked_back = _read(acted=True)
    assert [i["external_id"] for i in marked_back["items"]] == ["m1"]
    assert _read(acted=False)["items"], "没标过的那批也得读得到"


def test_unmarking_reverses_the_marker_without_deleting(pool):
    assert _mark(source="wechat", external_id="m1")["ok"] is True
    assert _mark(source="wechat", external_id="m1", acted=False)["ok"] is True

    unmarked = {i["external_id"] for i in _read(acted=False)["items"]}
    assert "m1" in unmarked, "撤记号之后那条应当回到「没标过」那一堆"
    assert {i["external_id"] for i in _read()["items"]} == {"m1", "d1"}, "撤记号不许把条目弄丢"


def test_filters_compose_and_limit_is_honoured(pool):
    assert _read(since="2026-10-19T00:00:00")["items"][0]["external_id"] == "m1"
    assert {i["external_id"] for i in _read(kind="ddl")["items"]} == {"d1"}
    assert _read(limit=1)["count"] == 1
    assert _read(source="wechat", kind="ddl")["count"] == 0


# ------------------------------------------------------------------ 判据④（失败形状）


def test_a_missing_identity_is_named_not_silently_accepted(pool):
    out = _mark(source="wechat", external_id="")
    assert out["ok"] is False and "external_id" in out["error"]


def test_an_unknown_row_says_theres_no_such_entry(pool):
    out = _mark(source="wechat", external_id="nope")
    assert out["ok"] is False and "nope" in out["error"]


def test_an_unreadable_pool_says_so_instead_of_returning_an_empty_list(pool, monkeypatch):
    """池打不开 ≠ "池里没有"：回空列表会让模型以为信源干净，那是最贵的一种假绿。"""
    def boom(*_a, **_kw):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(store, "connect", boom)
    out = _read()
    assert out["ok"] is False and "locked" in out["error"]


def test_pool_mark_is_treated_as_a_mutation_and_pool_read_is_not():
    """读手不许进变更名单，否则每次读都要人批一次；写记号那枚该进。"""
    from agent.tool_guardrails import MUTATING_TOOL_NAMES

    assert "pool_mark" in MUTATING_TOOL_NAMES
    assert "pool_read" not in MUTATING_TOOL_NAMES
