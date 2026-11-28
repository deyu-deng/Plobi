"""派活四要素的契约（``WP-OFFLOAD-BRIEF``，11-22）。

两件事：**只拼给了的**（不留空标题，免得它照着空标题编边界）；
**缺项要说代价**（说规矩没人看）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from plobi.delegation.brief import (  # noqa: E402
    check_brief,
    compose_brief,
    gaps_hint,
    hands_touched,
    missed_dispatch,
    missed_hint,
    rationale_hint,
)


def test_a_bare_goal_is_missing_the_other_three():
    assert check_brief("把 README 补个用法章节") == ["deliverable", "tools", "bounds"]


def test_all_four_given_means_nothing_missing():
    assert check_brief("补 README", "一段 markdown 追加到 README 末尾", "只准用仓库脚本", "别动其它文件") == []


def test_compose_keeps_only_what_was_given():
    """没给的项**不留空标题**：空标题会被它当成「你来补」，它补出来的东西不是你的意思。"""
    text = compose_brief("补 README", bounds="别动 src/")
    assert "【目标】补 README" in text
    assert "【边界】别动 src/" in text
    assert "【产出】" not in text and "【可用】" not in text


def test_compose_orders_the_four_as_goal_output_tools_bounds():
    text = compose_brief("目标", "产出", "可用", "边界")
    assert text.splitlines() == ["【目标】目标", "【产出】产出", "【可用】可用", "【边界】边界"]


def test_the_hint_talks_about_cost_not_rules():
    hint = gaps_hint(["bounds"])
    assert "授权" in hint and "边界" in hint


def test_a_missing_goal_is_reported_too():
    """没有目标就没有活——单独兜一层，别让它只报另外三样。"""
    assert check_brief("")[0] == "goal"


# --- 第五段「为什么这么拆」（WP-DISPATCH-VERDICT，11-25）---------------------


def test_rationale_goes_last_because_it_explains_the_other_four():
    text = compose_brief("改解析器", "改好的 parser.py", "只准用仓库脚本", "别动测试", "旧口径是错的")
    assert text.splitlines()[-1] == "【为什么这么拆】旧口径是错的"


def test_rationale_alone_is_enough_to_compose():
    """只给一句为什么，也要拼得出来——别因为没给别的就把它丢了。"""
    assert compose_brief("补 README", rationale="顺手对齐目录结构") == "【目标】补 README\n【为什么这么拆】顺手对齐目录结构"


def test_rationale_is_not_a_hard_gap():
    """软项不拦派活：它不该出现在缺项里，否则人开始凑废话交差。"""
    assert "rationale" not in check_brief("补 README")


def test_rationale_hint_talks_about_what_you_will_have_to_guess():
    hint = rationale_hint("")
    assert "反推" in hint and "为什么" in hint
    assert rationale_hint("因为旧口径废了") == ""


# --- 事后判据：伸手干了本该别人干的活没有 ---------------------------------


def test_read_only_tools_are_not_hands():
    """只读不算伸手：查资料本来就该自己来。"""
    assert hands_touched(["read_file", "search_files", "web_search"]) == []


def test_write_and_terminal_are_two_hands():
    assert hands_touched(["read_file", "write_file", "shell_executor"]) == ["write", "terminal"]


def test_dispatching_clears_the_miss():
    """派过了就不算漏派——哪怕这一轮自己也写了文件（拼回结果本来就要写）。"""
    assert missed_dispatch(["write_file", "external_offload_run"]) == []
    assert missed_dispatch(["shell_executor", "external_session_send"]) == []


def test_doing_it_all_yourself_is_a_miss():
    assert missed_dispatch(["read_file", "write_file", "shell_executor"]) == ["write", "terminal"]


def test_missed_hint_says_it_is_fine_when_nobody_owns_that_patch():
    """没主就自己干，本来就对——别把它说成错误。"""
    hint = missed_hint(["write"], owners=0)
    assert "自己干是对的" in hint and "写" in hint


def test_missed_hint_points_at_the_owner_when_there_is_one():
    hint = missed_hint(["terminal", "write"], owners=2)
    assert "有主" in hint and "2" in hint and "跑命令" in hint and "写" in hint


def test_no_miss_no_hint():
    assert missed_hint([], owners=3) == ""
