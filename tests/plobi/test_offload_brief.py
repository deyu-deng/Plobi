"""派活四要素的契约（``WP-OFFLOAD-BRIEF``，11-22）。

两件事：**只拼给了的**（不留空标题，免得它照着空标题编边界）；
**缺项要说代价**（说规矩没人看）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from plobi.delegation.brief import check_brief, compose_brief, gaps_hint   # noqa: E402


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
