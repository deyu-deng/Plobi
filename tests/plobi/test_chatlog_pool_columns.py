"""裁 187①/189 在采集侧那两格：`conversation` = 窗口 username、`visibility` 按 `@chatroom` 判，
以及**我依赖的时间契约**——那一列只准一种形状、原样留底、`since` 按"什么时候收到"筛得动。

装配那一步（`handle_message` → `pool_land`）的端到端证明不在这里，在仓里那支命令：
`python scripts/plobi/verify_chatlog_window_cell.py` —— 它把 `pool_land` 打成数一数的桩，
真拉真过规则、一个字不写进池，`columns_filled` 就是"两列真带上了没有"。
为什么不在这儿断言装配：`pipeline.py` 的落点此刻还是工作树里 WP-POOL 那一笔未提交的形状
（HEAD 里那段仍写 `ingest_occurrences`），把它写进 pytest 就等于签一条从干净 HEAD 跑不动的测。
时间那几条用临时库（`land(..., db_path=)` / `query(..., db_path=)`），真池一个字节不碰。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from plobi.collectors.chatlog.client import pool_columns


@pytest.mark.parametrize("talker,visibility", [
    ("56085102642@chatroom", "group"),      # 群：username 一律带 @chatroom
    ("wxid_9f2ab", "private"),              # 一对一
    ("gh_363b924965e9", "private"),         # 公众号：池那一侧没给第三个值，不自己拍
    ("brandsessionholder", "private"),
])
def test_visibility_is_read_off_the_username_not_invented(talker: str, visibility: str) -> None:
    assert pool_columns(talker) == {"visibility": visibility, "conversation": talker}


@pytest.mark.parametrize("talker", ["", "   ", None])
def test_a_missing_username_stays_empty_instead_of_becoming_a_guess(talker) -> None:
    """空串在池里的意思是「采它那一轮没记窗口」，不是「不属于任何窗口」——宁空勿猜。

    这也是"不许回填旧行"那一拍的同一句：旧那 12 条留空是用户 10-20 拍过的口径。
    """
    assert pool_columns(talker) == {"visibility": "", "conversation": ""}


def test_group_suffix_is_the_only_judgement_used() -> None:
    """判据就一条后缀，别的地方不许再长出一套第二种判法。"""
    from plobi.collectors.chatlog.client import GROUP_USERNAME_SUFFIX

    assert GROUP_USERNAME_SUFFIX == "@chatroom"
    assert pool_columns("50264584189" + GROUP_USERNAME_SUFFIX)["visibility"] == "group"
    assert pool_columns("50264584189" + GROUP_USERNAME_SUFFIX + "x")["visibility"] == "private"


# --- 裁 189：那一列只准一种形状，而这一形由池那一侧的唯一点管 ------------------------
# 采集侧**不许**再自己拼时间格式。下面两条钉的是我依赖的契约：形状归一、原样留底、
# `since` 按"什么时候收到"筛得动。用临时库，一个字都不碰真池。

CANON = "%Y-%m-%d %H:%M:%S"


def _item(external_id: str, occurred_at: str):
    from plobi.pool import PoolItem

    columns = pool_columns("50264584189@chatroom")
    return PoolItem(source="wechat", external_id=external_id, kind="meeting",
                    title="会", body="明天下午三点开会，记得带图纸",
                    occurred_at=occurred_at, **columns)


def test_the_column_keeps_one_shape_and_the_source_string_survives(tmp_path) -> None:
    from plobi.pool import land, query

    land([_item("cl_offset", "2026-10-07T11:45:20+08:00"),
          _item("cl_epoch", "1792419661")], db_path=tmp_path / "pool.db")
    rows = {r["external_id"]: r for r in query(source="wechat", db_path=tmp_path / "pool.db")}

    assert len(rows) == 2
    for row in rows.values():
        # 带 `T` 的（`'T'` 0x54 > `' '` 0x20）与纯 epoch 都必须落到同一形，
        # 否则 `ORDER BY occurred_at DESC` 与 `since` 就不等价于它们看起来的意思。
        assert "T" not in row["occurred_at"] and "+" not in row["occurred_at"]
        datetime.strptime(row["occurred_at"], CANON)
    assert rows["cl_offset"]["context"]["sent_at_raw"] == "2026-10-07T11:45:20+08:00"
    # epoch 那一形也归到同一列形状（不硬编换算结果，判据是"和 fromtimestamp 一致"）
    assert rows["cl_epoch"]["occurred_at"] == datetime.fromtimestamp(1792419661).strftime(CANON)


def test_since_means_received_since_and_accepts_a_loose_shape(tmp_path) -> None:
    from plobi.pool import land, query

    land([_item("cl_old", "2026-10-07T11:45:20+08:00"),
          _item("cl_today", datetime.now().strftime(CANON))], db_path=tmp_path / "pool.db")

    got = query(source="wechat", since="2026-10-08 00:00:00", db_path=tmp_path / "pool.db")
    assert [r["external_id"] for r in got] == ["cl_today"]
    # 读侧同样只归一一次：给 `T` 分隔的也认（列里那种形状不许反过来要求调用方拼对）
    assert [r["external_id"] for r in query(
        source="wechat", since="2026-10-08T00:00:00", db_path=tmp_path / "pool.db")] == ["cl_today"]

