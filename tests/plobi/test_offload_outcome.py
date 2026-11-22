"""回执三态的契约（``WP-OFFLOAD-OUTCOME``，11-22）。

判据只有一条：**下一步动作不同**。
``failed`` 是「办砸了」——重试或换人；
``waiting`` 是「它停下来等一句话」——重试十遍还在原地，得有人回它。

所以这一族测试全部围绕「什么读数才允许被读成 waiting」：
它问过授权（有读数）、它停在问句上（看末尾）、它自己拒了（不是等人）。
没有读数的，一律不许猜成 waiting——猜错的代价是没人去重试。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent import external_offload as channel                           # noqa: E402
from plobi.delegation.offload import OffloadResult, summarize_round     # noqa: E402


# ── 判据：resolve_outcome ────────────────────────────────────────────

def test_done_wins_over_anything_else():
    """做成了就是做成了：它中途问过几次授权，都不改变这一格。"""
    assert channel.resolve_outcome(ok=True) == (channel.OUTCOME_DONE, "")
    assert channel.resolve_outcome(
        ok=True, text="(y/n)", permission_requests=3) == (channel.OUTCOME_DONE, "")


def test_its_own_refusal_is_not_waiting():
    """它自己拒了（stopReason=refusal）不是「在等人」：回一句也不会变。"""
    assert channel.resolve_outcome(
        ok=False, refused=True, permission_requests=2) == (channel.OUTCOME_FAILED, "")


def test_permission_questions_that_ended_badly_are_waiting():
    """问过授权却没做成 ⇒ 等的是授权本身（放宽档，或人手答一句）。"""
    assert channel.resolve_outcome(
        ok=False, permission_requests=1) == (channel.OUTCOME_WAITING, "permission")


def test_a_trailing_question_means_waiting_for_an_answer():
    assert channel.resolve_outcome(
        ok=False, text="要不要现在覆盖 src/main.py？(y/n)") == (channel.OUTCOME_WAITING, "answer")


def test_a_question_asked_and_answered_midway_is_not_waiting():
    """中途打过问句、后面自己跑完的，不算等人——判据读它停在哪儿，不读它说过什么。"""
    text = "Do you want to proceed? (y/n)\n" + "通了。\n" * 80
    assert channel.resolve_outcome(ok=False, text=text) == (channel.OUTCOME_FAILED, "")


def test_no_reading_at_all_is_failed_never_waiting():
    """没有任何读数就不许猜：猜成 waiting 会让一条真失败的路没人去重试。"""
    assert channel.resolve_outcome(
        ok=False, error="external agent did not finish within 900s") == (channel.OUTCOME_FAILED, "")


# ── 台账这一格：空串按 ok 兜底，不许空串当成功 ─────────────────────────

def _row(**kw):
    base = dict(agent="qoder", cwd=str(Path.cwd()), permission_flag="--mode=edit")
    base.update(kw)
    return OffloadResult(**base)


def test_an_old_row_without_outcome_falls_back_to_ok():
    assert _row().resolved_outcome == channel.OUTCOME_DONE
    assert _row(failure="external agent did not finish within 900s").resolved_outcome == channel.OUTCOME_FAILED


def test_a_row_that_says_waiting_reads_as_waiting_even_with_a_failure_code():
    row = _row(failure="timeout", outcome="waiting", waiting_on="permission")
    assert row.resolved_outcome == channel.OUTCOME_WAITING


# ── 读成人话：等人 ≠ 没成 ───────────────────────────────────────────

def test_the_round_summary_tells_waiting_apart_from_failed():
    waiting = _row(failure="blocked_by_gate", outcome="waiting", waiting_on="permission",
                   reply="", reply_status="not_filed")
    text = summarize_round([waiting])
    assert "停在等一句授权" in text
    assert "没成" not in text


def test_the_round_summary_still_calls_a_plain_failure_a_failure():
    failed = _row(failure="timeout", outcome="failed")
    text = summarize_round([failed])
    assert "没成" in text
    assert "停在等" not in text


# ── 通道这一格：两个腿都要算出来 ─────────────────────────────────────

def test_the_channel_result_carries_both_cells():
    """字段存在且默认是空串：老构造点（几十处）不用逐个改也能跑。"""
    result = channel.OffloadResult(
        agent="qoder", cwd=str(Path.cwd()), permission_flags=(), sandboxed=False,
        exit_code=0, duration_s=1.0, argv=(), usage={}, cost=None, cost_status="unknown",
        artifacts=(), text="", ok=True, error="",
    )
    assert result.outcome == "" and result.waiting_on == ""
