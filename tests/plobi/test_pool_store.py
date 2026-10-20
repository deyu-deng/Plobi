"""信息池本体的判据（`specs/source-pool.md`）。

跑法：``bash scripts/run_tests.sh tests/plobi/test_pool_store.py -q -j 6``
（唯一入口、不传 ``--basetemp``，裁定 87）。

这一档钉的是**关系**不是快照：唯一键的行为、共享层的落点、以及**那条边界**——
池子这一层认识不认识 `agenda.db`。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from plobi.pool import PoolItem, normalize_time, store


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


class TestTheTwoColumns:
    """裁定 187 第 1 条：`visibility`（私聊／群聊）与 `conversation`（哪一扇窗口）要**真存**。

    塞在 `context` 那格自由 JSON 里不算——`conversation_id` 以前只活在采集器的内存里
    去重窗口，落库就没了，读侧想按窗口筛就得每条先解析一遍。
    """

    def test_a_row_carries_its_window_and_visibility(self, pool):
        store.land([item(visibility="group", conversation="工程训练·课群组")])

        row = store.query()[0]
        assert row["visibility"] == "group"
        assert row["conversation"] == "工程训练·课群组"

    def test_both_are_filterable_not_only_printable(self, pool):
        store.land([
            item(external_id="a", visibility="group", conversation="群A"),
            item(external_id="b", visibility="private", conversation="群B"),
        ])

        assert [r["external_id"] for r in store.query(conversation="群A")] == ["a"]
        assert [r["external_id"] for r in store.query(visibility="private")] == ["b"]

    def test_a_window_filled_in_later_updates_in_place(self, pool):
        # 采集那一轮没记窗口、后来补上 ⇒ 内容算变了，要落，且不长第二份。
        store.land([item(conversation="")])
        result = store.land([item(conversation="群A")])

        assert result == {"created": 0, "updated": 1, "unchanged": 0}
        assert store.query()[0]["conversation"] == "群A"
        assert len(store.query()) == 1

    def test_an_old_database_gets_the_columns_and_keeps_its_rows(self, pool):
        """这台盘上此刻就有 12 条躺在旧形状里——`CREATE TABLE IF NOT EXISTS` 对旧库什么都不做。

        不 ALTER 的话写侧一写就是 `no column named visibility`。
        **一次一形**（用户 10-20 拍）：旧行留空、不回填（回填要从采集器的内存倒推，
        而这一刀不许碰采集器与 `source_talker`）。
        """
        old = sqlite3.connect(str(pool))
        old.execute(
            "CREATE TABLE pool_items ("
            " source TEXT NOT NULL, external_id TEXT NOT NULL,"
            " kind TEXT NOT NULL DEFAULT 'note', title TEXT NOT NULL DEFAULT '',"
            " body TEXT NOT NULL DEFAULT '', context TEXT NOT NULL DEFAULT '{}',"
            " occurred_at TEXT NOT NULL DEFAULT '', captured_at TEXT NOT NULL DEFAULT '',"
            " acted INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT '',"
            " PRIMARY KEY (source, external_id))"
        )
        old.execute(
            "INSERT INTO pool_items (source, external_id, body)"
            " VALUES ('wechat', 'old1', '那条旧的')"
        )
        old.commit()
        old.close()

        rows = store.query()

        assert [r["external_id"] for r in rows] == ["old1"], "迁移不许吃掉旧行"
        assert rows[0]["visibility"] == "" and rows[0]["conversation"] == ""
        # 补得上：留空是「当时没记」，不是「这条永远改不动」
        assert store.land([item(source="wechat", external_id="old1",
                               conversation="群A")])["updated"] == 1

    def test_the_columns_are_columns_not_json_keys(self, pool):
        store.land([item(visibility="group", conversation="群A")])

        with sqlite3.connect(str(pool)) as conn:
            names = {row[1] for row in conn.execute("PRAGMA table_info(pool_items)")}

        assert {"visibility", "conversation"} <= names


class TestTimeShapes:
    """裁定 189 那一格的形状判据：**列里只准一种形状**，归一在写入口这一点做。

    混形的后果不是不好看：`'T'`(0x54) 比 `' '`(0x20) 大，同一天前缀下谁排前面取决于谁先写入，
    `ORDER BY occurred_at DESC` 与 `since` 就不再是它们看起来的意思。
    """

    def test_four_shapes_collapse_to_the_one_the_column_promised(self, pool):
        epoch_s = int(datetime(2026, 10, 20, 9, 32).timestamp())
        store.land([
            item(external_id="iso_t", occurred_at="2026-10-20T09:30:00"),
            item(external_id="space", occurred_at="2026-10-20 09:31:00"),
            item(external_id="epoch_s", occurred_at=str(epoch_s)),
            item(external_id="epoch_ms", occurred_at=str(epoch_s * 1000)),
        ])

        got = {r["external_id"]: r["occurred_at"] for r in store.query()}

        assert got["iso_t"] == "2026-10-20 09:30:00"
        assert got["space"] == "2026-10-20 09:31:00"
        assert got["epoch_s"] == "2026-10-20 09:32:00"
        assert got["epoch_ms"] == "2026-10-20 09:32:00", "13 位是毫秒，别当 10 位秒读成 5 万年后"

    def test_since_is_the_read_side_of_the_same_normalization(self, pool):
        """`since` 给 `T` 形也必须筛得动——以前它和空格形比会整批落空（`'T' > ' '`）。"""
        store.land([
            item(external_id="today_a", occurred_at="2026-10-20T09:30:00"),
            item(external_id="today_b", occurred_at="2026-10-20 09:31:00"),
            item(external_id="epoch", occurred_at=str(int(__import__("datetime").datetime(2026, 10, 20, 9, 32).timestamp()))),
            item(external_id="yesterday", occurred_at="2026-10-19 23:59:00"),
        ])

        rows = store.query(since="2026-10-20T00:00:00")

        assert [r["external_id"] for r in rows] == ["epoch", "today_b", "today_a"]

    def test_the_raw_shape_survives_in_context_not_in_the_column(self, pool):
        store.land([item(external_id="t", occurred_at="2026-10-20T09:30:00"),
                    item(external_id="clean", occurred_at="2026-10-20 09:31:00")])

        rows = {r["external_id"]: r for r in store.query()}
        assert rows["t"]["context"]["sent_at_raw"] == "2026-10-20T09:30:00"
        # 已经是那一形的**不另存一份**——否则每轮重跑都在往 context 里堆没用的副本
        assert "sent_at_raw" not in rows["clean"]["context"]

    def test_an_unreadable_time_lands_empty_and_says_so_instead_of_guessing(self, pool):
        # 不抛到采集器：一条消息的时间读不出不该让整批信源失败。
        canonical, raw = normalize_time("上周五大概下午")

        assert canonical == "" and raw == "上周五大概下午"
        store.land([item(external_id="junk", occurred_at="上周五大概下午")])
        assert store.query()[0]["occurred_at"] == ""

    def test_aware_time_becomes_local_naive(self):
        got, raw = normalize_time("2026-10-07T11:45:20+08:00")
        expected = datetime.fromisoformat("2026-10-07T11:45:20+08:00").astimezone().replace(tzinfo=None)

        assert got == expected.strftime(store.CANONICAL_FORMAT)
        assert raw == "2026-10-07T11:45:20+08:00", "带偏移的那种必须留原样，本机时区不同读数就不同"

    def test_compact_and_numeric_forms(self):
        assert normalize_time("20261007")[0] == "2026-10-07 00:00:00"
        assert normalize_time("20261007114520")[0] == "2026-10-07 11:45:20"
        assert normalize_time("")[0] == ""
        assert normalize_time(None)[0] == ""

    def test_a_since_we_cannot_read_is_refused_not_ignored(self, pool):
        store.land([item()])

        with pytest.raises(store.PoolError, match="读不出来"):
            store.query(since="前天吧")

    def test_recollect_only_fixing_the_time_still_lands(self, pool):
        """裁 189 那句「旧行不迁移，重采同一窗口时 `occurred_at` 自己变对」靠这条钉住。

        旧实现里 `land()` 只比 body／title／context／那两列 ⇒ 正文一个字没变、只把时间改对的那次
        重采会被判成「没变」而 `continue` 掉，那一行就永远留着装错的"事情发生的时间"。
        """
        store.land([item(external_id="m1", occurred_at="2026-10-24T14:00:00")])

        result = store.land([item(external_id="m1", occurred_at="2026-10-19 11:45:20")])

        assert result["updated"] == 1 and result["created"] == 0
        assert store.stats()["total"] == 1, "重采不许长出第二份"
        assert store.query()[0]["occurred_at"] == "2026-10-19 11:45:20"

    def test_a_reclassified_kind_still_lands(self, pool):
        store.land([item(external_id="m1", kind="note")])

        assert store.land([item(external_id="m1", kind="meeting")])["updated"] == 1
        assert store.query()[0]["kind"] == "meeting"
        assert store.stats()["total"] == 1

    def test_rows_without_a_time_fall_back_to_when_we_read_them(self, pool):
        store.land([
            item(external_id="older", captured_at="2026-10-01 08:00:00"),
            item(external_id="newer", captured_at="2026-10-05 08:00:00"),
        ])

        assert [r["external_id"] for r in store.query()] == ["newer", "older"]
        # 没有时间的行不算在窗口外：宁可多给一条说不清时间的，也不藏一条真收到的
        assert {r["external_id"] for r in store.query(since="2026-10-20 00:00:00")} == {"older", "newer"}


class TestItDoesNotBreak:
    def test_an_unreadable_pool_says_so_instead_of_handing_nothing(self, pool, monkeypatch):
        # 吞掉的形状：写不进去 ⇒ 采集器报「跑了 0 条」，而用户看到的是「今天没东西进来」。
        def boom(items, *, db_path=None):
            raise sqlite3.OperationalError("disk I/O error")

        monkeypatch.setattr(store, "land", boom)

        with pytest.raises(sqlite3.OperationalError):
            store.land([item()])