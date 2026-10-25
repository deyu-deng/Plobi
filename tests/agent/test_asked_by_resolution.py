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


def test_kind_follows_the_same_precedence():
    """裁定 196/201：`kind` 与 `asked_by` 同一条优先级，派生方用环境交代。"""
    from run_agent import resolve_kind

    assert resolve_kind({"kind": "system"}, env={"PLOBI_MESSAGE_KIND": "agent"}) == "system"
    assert resolve_kind({"role": "user"}, env={"PLOBI_MESSAGE_KIND": "system"}) == "system"
    assert resolve_kind({"role": "user"}, env={}) is None


def test_reply_to_follows_the_same_precedence():
    """裁定 196 ④：这条在回哪一条，与 asked_by/kind 同一条优先级。"""
    from run_agent import resolve_reply_to

    assert resolve_reply_to({"reply_to": "4091"}, env={"PLOBI_REPLY_TO": "9"}) == "4091"
    assert resolve_reply_to({"role": "user"}, env={"PLOBI_REPLY_TO": "9"}) == "9"
    assert resolve_reply_to({"role": "user"}, env={}) is None
