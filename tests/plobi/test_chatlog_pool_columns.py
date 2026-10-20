"""裁 187① 那两列在采集侧的取值口径：`conversation` = 窗口 username，`visibility` = 私聊／群聊。

这一档只测**这个文件自己算出来的那两位**——装配那一步（`handle_message` → `pool_land`）
的端到端证明在仓里那支命令里：`python scripts/plobi/verify_chatlog_window_cell.py`
它把 `pool_land` 打桩成数一数的桩，真拉真过规则、一个字不写进池，
读出的 `columns_filled` 就是"两列真带上了没有"。
为什么不在这儿断言装配：`pipeline.py` 的落点此刻还是工作树里 WP-POOL 那一笔未提交的形状
（HEAD 里那段仍写 `ingest_occurrences`），把它写进 pytest 就等于签一条从干净 HEAD 跑不动的测。
"""

from __future__ import annotations

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
