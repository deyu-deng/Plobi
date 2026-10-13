"""`resolve_asked_by` 的优先级：消息自己带的 > 进程环境 > 没记（NULL）。

这一格决定界面上那条消息标"我"、标"L1"，还是显式说"没记发起方"。
最容易犯的错是把 NULL 塌成 owner——那等于把猜测显示成事实。
"""

from run_agent import resolve_asked_by


def test_message_level_label_wins_over_the_process_default():
    got = resolve_asked_by({"asked_by": "owner"}, env={"PLOBI_ASKED_BY": "l1:s-1"})
    assert got == "owner"


def test_process_env_is_the_fallback_for_surfaces_that_set_it():
    assert resolve_asked_by({"role": "user"}, env={"PLOBI_ASKED_BY": "owner"}) == "owner"


def test_nothing_recorded_stays_none_rather_than_becoming_owner():
    assert resolve_asked_by({"role": "user"}, env={}) is None


def test_blank_or_whitespace_env_is_not_a_label():
    assert resolve_asked_by(None, env={"PLOBI_ASKED_BY": "   "}) is None
    assert resolve_asked_by({"asked_by": ""}, env={}) is None
