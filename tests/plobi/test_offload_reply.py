"""外包回执这一族（`WP-OFFLOAD-REPLY`）的行为契约。

用户 10-22 定的形状：**第三方软件要有区分的工具，不许长在 `say` 上**。所以回执不是
"它替自己发一条消息"，而是"它把结果写回这一趟的文件，我们收进外包自己的台账"。
判据因此全围绕三件事：身份是现造的、路径就是身份（持有即认证）、
**"它没回"与"我们读坏了"是两句不同的话**。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent import external_offload as channel                    # noqa: E402
from plobi.delegation.offload import OffloadResult, RoundLedger, summarize_round  # noqa: E402


@pytest.fixture
def replies(tmp_path, monkeypatch):
    root = tmp_path / "replies"
    monkeypatch.setenv(channel.REPLY_DIR_ENV, str(root))
    return root


# ── 身份：现造、可核、不是文件名穿越的口子 ────────────────────────────────

def test_job_tokens_are_fresh_each_time():
    tokens = {channel.new_job_token() for _ in range(50)}
    assert len(tokens) == 50
    assert all(len(t) >= 20 for t in tokens)


def test_a_token_that_is_not_a_safe_filename_gets_no_path():
    """token 同时是**文件名**，所以不合法就不给路径——不洗白、不截断、不猜。"""
    for bad in ("", "../escape", "a/b", "x" * 200, "has space", "中文名字"):
        assert channel.reply_path_for(bad) is None, bad


def test_collecting_an_unknown_job_is_a_reading_not_an_error(replies):
    assert channel.collect_reply("does-not-exist-at-all") == ("", "not_filed")


# ── 四种读数各说各话 ─────────────────────────────────────────────────────

def test_filed_json_is_taken_from_the_text_field(replies):
    job = channel.new_job_token()
    channel.reply_path_for(job).write_text(
        json.dumps({"text": "课表 12 节，周三全停", "status": "ok"}, ensure_ascii=False), encoding="utf-8")

    assert channel.collect_reply(job) == ("课表 12 节，周三全停", "filed")


def test_collecting_twice_reports_the_second_as_not_filed(replies):
    """读一次就收走：同一份回执不许被下一轮再数一次。"""
    job = channel.new_job_token()
    channel.reply_path_for(job).write_text('{"text": "一次性的"}', encoding="utf-8")
    assert channel.collect_reply(job) == ("一次性的", "filed")
    assert channel.collect_reply(job) == ("", "not_filed")


def test_a_wrongly_shaped_file_is_kept_and_marked_raw(replies):
    """它写了但不是我们给的形状 ⇒ 内容照收、形状照实标，不静默丢、也不假装 filed。"""
    job = channel.new_job_token()
    channel.reply_path_for(job).write_text("它直接写了一段纯文本", encoding="utf-8")
    assert channel.collect_reply(job) == ("它直接写了一段纯文本", "raw")

    job2 = channel.new_job_token()
    channel.reply_path_for(job2).write_text("   ", encoding="utf-8")
    assert channel.collect_reply(job2) == ("", "empty")


# ── 台账：这一趟的身份与回执都得留底 ─────────────────────────────────────

def test_ledger_row_carries_the_job_and_what_came_back(tmp_path):
    ledger = RoundLedger(root=tmp_path)
    row = ledger.append(OffloadResult(
        agent="qoder", cwd="D:/Temp/x", permission_flag="--permission-mode default",
        duration_seconds=62.0, cost_status="no_usage_reported",
        job="kQ7dA1mZx9pLb3Tn", reply="暗号是：紫罗兰三号。", reply_status="filed", reply_to="Sindri",
    ), brief="读 note.txt")
    assert row["job"] == "kQ7dA1mZx9pLb3Tn"
    assert row["reply"] == "暗号是：紫罗兰三号。" and row["reply_status"] == "filed"
    assert row["reply_to"] == "Sindri"
    # 落盘的那一行读回来必须一致（jsonl 是它的真身，不是内存里的字典）
    on_disk = [json.loads(l) for l in ledger.path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert on_disk[0]["job"] == "kQ7dA1mZx9pLb3Tn"


def test_summary_says_did_not_reply_and_could_not_read_apart():
    """判据 4 的同一族要求：失败原因要**分得开**，回执读数也一样。"""
    filed = summarize_round([OffloadResult(
        agent="qoder", cwd="D:/Temp/x", permission_flag="default", duration_seconds=62.0,
        cost_status="no_usage_reported", reply="暗号是：紫罗兰三号。", reply_status="filed")])
    silent = summarize_round([OffloadResult(
        agent="qoder", cwd="D:/Temp/x", permission_flag="default", duration_seconds=62.0,
        cost_status="no_usage_reported", reply_status="not_filed")])
    broken = summarize_round([OffloadResult(
        agent="qoder", cwd="D:/Temp/x", permission_flag="default", duration_seconds=62.0,
        cost_status="no_usage_reported", reply_status="unreadable")])

    assert "它自己回：暗号是：紫罗兰三号。" in filed
    assert "它没回话" in silent and "读不到" not in silent
    assert "回执读不到：unreadable" in broken and "它没回话" not in broken
