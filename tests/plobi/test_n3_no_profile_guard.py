"""数据条目（没有 profile 的名册行）被派活时，必须**指名道姓**地拒，而不是漏一句内部报错。

2026-10-13 13:2x 现核：脑搬家线扫同类空 profile 的门时报出 `append_n3_dispatch()` 前面什么守卫都没有
—— 它当时不炸只是因为唯一调用方 `_secretary_ask()` 把整段包在 `try/except Exception` 里，
于是现场只落进 payload 的一行 `n3_error: profile name cannot be empty`。那句话和"保留名"的报错长得一模一样，
谁都分不出是"这条目本来就没有运行时"还是"名字撞了保留字"。门要自己会拒绝，不能靠上游替它挡着。
"""

from pathlib import Path

import pytest

from plobi.agents.registry import AgentEntry, append_n3_dispatch


def test_data_row_without_profile_is_refused_by_name() -> None:
    entry = AgentEntry(name="电工电子学", role="l2_course", category="courses")

    with pytest.raises(ValueError) as exc:
        append_n3_dispatch(entry, user_text="把作业清单拉一下", intent="course-homework")

    msg = str(exc.value)
    assert "has no profile to dispatch to" in msg, msg
    assert "电工电子学" in msg, "要报出是哪一条名册条目，不然现场只能靠猜"
    assert "l2_course" in msg, "角色要一起报——它才是这条不是分身的原因"
    assert "cannot be empty" not in msg, "不许再把内部那句『profile name cannot be empty』当结论抛出来"


def test_the_refusal_does_not_open_a_session_behalf(tmp_path: Path, monkeypatch) -> None:
    """拒绝要干净：不能先建半个 session 再报错（半截写入比报错更难查）。

    做法是把家目录钉到临时区，跑一次数据条目的派发，然后证明那里没多出任何库文件。
    """
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path))
    entry = AgentEntry(name="大学物理（甲）Ⅱ", role="l2_course", category="courses")

    with pytest.raises(ValueError):
        append_n3_dispatch(entry, user_text="拉作业", intent="course-homework")

    assert list(tmp_path.rglob("*.db")) == [], "拒了却留下 state.db，等于把脏写进现场"
    assert list(tmp_path.rglob("*.sqlite*")) == [], tmp_path
