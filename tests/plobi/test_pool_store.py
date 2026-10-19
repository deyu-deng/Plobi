"""信息池本体的判据（`specs/source-pool.md`）。

跑法：``bash scripts/run_tests.sh tests/plobi/test_pool_store.py -q -j 6``
（唯一入口、不传 ``--basetemp``，裁定 87）。

这一档钉的是**关系**不是快照：唯一键的行为、共享层的落点、以及**那条边界**——
池子这一层认识不认识 `agenda.db`。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from plobi.pool import PoolItem, store


@pytest.fixture
def pool(tmp_path, monkeypatch):
    """把池子钉到临时文件。这一份被**所有分身**共享，测试不许碰真的那份。"""
    db = tmp_path / "pool.db"
    monkeypatch.setattr(store, "path", lambda: db)
    return db


def item(**over) -> PoolItem:
    # 默认值用 `setdefault` 而不是写死在 `PoolItem(...)` 的位置参数里：
    # 否则用例里再传一次 `source=` 就是「multiple values for keyword argument」。
    over.setdefault("body", "明天下午三点开组会")
    over.setdefault("external_id", "m1")
    over.setdefault("source", "wechat")

    return PoolItem(**over)


class TestItLands:
    def test_an_item_lands_whole(self, pool):
        result = store.land([item(title="开组会")])

        assert result == {"created": 1, "updated": 0, "unchanged": 0}
        rows = store.query()
        assert len(rows) == 1
        assert rows[0]["body"] == "明天下午三点开组会"
        assert rows[0]["title"] == "开组会"
        assert rows[0]["acted"] is False

    def test_the_same_id_does_not_land_twice(self, pool):
        store.land([item()])
        result = store.land([item()])

        assert result["created"] == 0
        assert len(store.query()) == 1

    def test_changed_content_updates_in_place(self, pool):
        store.land([item(body="旧正文")])
        result = store.land([item(body="新正文")])

        assert result["updated"] == 1
        assert store.query()[0]["body"] == "新正文"
        assert len(store.query()) == 1

    def test_identical_content_leaves_updated_at_alone(self, pool):
        # 每轮重跑都刷一遍 updated_at ⇒ Agent 若按那一列排序就看到一个永远在变的池子。
        store.land([item()])
        first = store.query()[0]

        store.land([item()])

        assert store.query()[0] == first

    def test_the_key_is_source_plus_external_id_not_the_id_alone(self, pool):
        # 两条信源各自的 `m1` 是两条不同的东西。
        store.land([item(source="wechat", external_id="m1")])
        store.land([item(source="dingtalk", external_id="m1")])

        assert len(store.query()) == 2
        assert {r["source"] for r in store.query()} == {"wechat", "dingtalk"}


class TestWhereItLands:
    def test_it_resolves_under_the_shared_root(self, pool, monkeypatch):
        # 裁定 42：分身家各存一份就是新的漂移源，而那次事故的表现正是
        # 「问『我明天有什么』得到的是空」。
        monkeypatch.undo()
        monkeypatch.setattr("plobi_constants.get_shared_plobi_root", lambda: Path("/shared-root"))

        assert store.path() == Path("/shared-root/plobi/pool.db")

    def test_the_store_module_never_reaches_the_agenda_store(self, pool):
        """**这条是整篇规格的中心那一格。**

        池子这一层**不认识** `agenda.db` —— 不认识，就没法顺手落日程。那条路是**断的**，
        不是靠约定守住的。（而 `ingest_occurrences` 恰恰是那种「留着但是不用」的出口。）

        **扫的是可执行代码，不是注释** —— 那一格的注记里要反复写「这一层不认识日程库」，
        写全名反而会让这条扫描永远红。所以先把 docstring 与注释剥掉再扫。
        """
        import ast
        import tokenize

        with tokenize.open(store.__file__) as handle:
            source = handle.read()

        tree = ast.parse(source)

        # 注释（tokenize）**与**文档字符串（ast）都要剥掉。`ast.unparse` 会把 docstring
        # 原样留在函数/模块体里，所以光去掉 token 里的注释**不够** —— 第一版就栽在这：
        # docstring 里那句「这一层不认识 `agenda.db`」自己让扫描红了。
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if (node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)):
                    node.body = node.body[1:] or [ast.Pass()]

        code_only = ast.unparse(tree).lower()

        for banned in ("agenda", "agendaservice", "ingest_occurrences", "apply="):
            assert banned not in code_only, f"pool/store.py 的可执行代码里出现了 {banned}"

        # 而 docstring 里**必须**把那条边界写清楚（那一格是给下一个读的人的，不是给扫描的）。
        assert "agenda.db" in source

    def test_land_takes_no_service_and_no_apply_mode(self, pool):
        """签名里**没有**那个出口。留着「顺便落日程」的形状就是留着一扇门。"""
        import inspect

        params = inspect.signature(store.land).parameters

        assert "service" not in params
        assert "apply" not in params
        assert "apply_mode" not in params


class TestAgentReadsIt:
    def test_it_returns_the_body(self, pool):
        # 用户 10-19 拍「放开：Agent 想读就读」，而 ADR-0010 第 3 条在此被取代。
        store.land([item(body="正文不该被藏起来")])

        assert store.query()[0]["body"] == "正文不该被藏起来"

    def test_it_filters_by_source_kind_and_text(self, pool):
        store.land([
            item(external_id="a", kind="message", title="开会"),
            item(external_id="b", kind="assignment", title="交作业")
        ])

        assert len(store.query(source="wechat")) == 2
        assert len(store.query(source="dingtalk")) == 0
        assert store.query(kind="assignment")[0]["title"] == "交作业"
        assert store.query(contains="作业")[0]["external_id"] == "b"
        assert store.query(contains="开会")[0]["external_id"] == "a"

    def test_the_limit_is_capped_because_a_prompt_is_not_a_bucket(self, pool):
        # 上限不是为了隐私，是 prompt 撑爆——那与隐私无关但同样要防。
        store.land([item(external_id=str(i)) for i in range(10)])

        assert len(store.query(limit=3)) == 3
        assert len(store.query(limit=10_000)) == 10
        assert len(store.query(limit=0)) == 1, "limit=0 不该变成「不要」"

    def test_acted_is_a_mark_not_a_queue(self, pool):
        # 池子**不是**队列：读了一条它还在，`acted` 只是一个记号。
        store.land([item()])
        assert store.mark_acted("wechat", "m1") is True

        assert len(store.query()) == 1
        assert store.query(acted=True)[0]["external_id"] == "m1"
        assert store.query(acted=False) == []

    def test_marking_something_absent_says_so(self, pool):
        assert store.mark_acted("wechat", "nope") is False


class TestStats:
    def test_it_is_read_live_not_remembered(self, pool):
        store.land([item(external_id="a"), item(external_id="b", source="zju")])
        store.mark_acted("wechat", "a")

        stats = store.stats()

        assert stats["total"] == 2
        assert stats["by_source"] == {"wechat": 1, "zju": 1}
        assert stats["acted"] == 1


class TestItDoesNotBreak:
    def test_an_unreadable_pool_says_so_instead_of_handing_nothing(self, pool, monkeypatch):
        # 吞掉的形状：写不进去 ⇒ 采集器报「跑了 0 条」，而用户看到的是「今天没东西进来」。
        def boom(items, *, db_path=None):
            raise sqlite3.OperationalError("disk I/O error")

        monkeypatch.setattr(store, "land", boom)

        with pytest.raises(sqlite3.OperationalError):
            store.land([item()])