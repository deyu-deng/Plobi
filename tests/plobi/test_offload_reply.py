"""外包回执这一族（`WP-OFFLOAD-REPLY`）的行为契约。

回执文件落在**它那趟的工作目录**里，文件名就是这一趟的 job token ⇒ 持有即认证。

用户 10-22 定的形状：**第三方软件要有区分的工具，不许长在 `say` 上**。所以回执不是
"它替自己发一条消息"，而是"它把结果写回这一趟的文件，我们收进外包自己的台账"。
判据因此全围绕三件事：身份是现造的、路径就是身份（持有即认证）、
**"它没回"与"我们读坏了"是两句不同的话**。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent import external_offload as channel                    # noqa: E402
from plobi.console.router import _cwd_under_folder               # noqa: E402
from plobi.delegation.offload import OffloadResult, RoundLedger, summarize_round  # noqa: E402


@pytest.fixture
def work(tmp_path):
    """回执落在**它那趟的工作目录**里（10-22 实测：它不肯往我们家目录写）。"""
    root = tmp_path / "offload-work"
    root.mkdir()
    return root


# ── 身份：现造、可核、不是文件名穿越的口子 ────────────────────────────────

def test_job_tokens_are_fresh_each_time():
    tokens = {channel.new_job_token() for _ in range(50)}
    assert len(tokens) == 50
    assert all(len(t) >= 20 for t in tokens)


def test_a_token_that_is_not_a_safe_filename_gets_no_path(tmp_path):
    """token 同时是**文件名**，所以不合法就不给路径——不洗白、不截断、不猜。"""
    for bad in ("", "../escape", "a/b", "x" * 200, "has space", "中文名字"):
        assert channel.reply_file_for(tmp_path, bad) is None, bad


def test_collecting_a_missing_file_is_a_reading_not_an_error(work):
    assert channel.collect_reply(work / ".offload-reply-nothing-here.json") == ("", "not_filed")
    assert channel.collect_reply(None) == ("", "not_filed"), "token 不合法时连路径都不给"


# ── 四种读数各说各话 ─────────────────────────────────────────────────────

def test_filed_json_is_taken_from_the_text_field(work):
    job = channel.new_job_token()
    channel.reply_file_for(work, job).write_text(
        json.dumps({"text": "课表 12 节，周三全停", "status": "ok"}, ensure_ascii=False), encoding="utf-8")

    assert channel.collect_reply(channel.reply_file_for(work, job)) == ("课表 12 节，周三全停", "filed")


def test_collecting_twice_reports_the_second_as_not_filed(work):
    """读一次就收走：同一份回执不许被下一轮再数一次。"""
    job = channel.new_job_token()
    path = channel.reply_file_for(work, job)
    path.write_text('{"text": "一次性的"}', encoding="utf-8")
    assert channel.collect_reply(path) == ("一次性的", "filed")
    assert channel.collect_reply(path) == ("", "not_filed")


def test_a_wrongly_shaped_file_is_kept_and_marked_raw(work):
    """它写了但不是我们给的形状 ⇒ 内容照收、形状照实标，不静默丢、也不假装 filed。"""
    job = channel.new_job_token()
    path = channel.reply_file_for(work, job)
    path.write_text("它直接写了一段纯文本", encoding="utf-8")
    assert channel.collect_reply(path) == ("它直接写了一段纯文本", "raw")

    job2 = channel.new_job_token()
    path2 = channel.reply_file_for(work, job2)
    path2.write_text("   ", encoding="utf-8")
    assert channel.collect_reply(path2) == ("", "empty")


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


# ── 抽屉的归属比较：大小写走本平台语义，边界必须是真的目录边界 ─────────
# 10-22 实测：名册绑 `D:\projects\Sindri`，台账记的是文件系统解析出的 `D:\Projects\Sindri`，
# 逐字符 startswith 让这一格**永远空表**——用户因此看不到外包那一条。同一个式子还反过来错：
# `Sindri-2` 会被算到 `Sindri` 头上。

def test_a_deeper_directory_still_belongs_to_that_agent():
    assert _cwd_under_folder("D:/projects/Sindri/sub/dir", "D:\\projects\\Sindri")


def test_sibling_folder_with_the_same_prefix_is_not_the_same_agent():
    """纯前缀匹配会把兄弟目录的活记到隔壁分身名下——那比漏报更坏。"""
    assert not _cwd_under_folder("D:/projects/Sindri-2", "D:/projects/Sindri")
    assert not _cwd_under_folder("D:/projects/Sindrievous", "D:/projects/Sindri")


def test_parent_folder_is_not_inside_the_bound_one():
    assert not _cwd_under_folder("D:/projects", "D:/projects/Sindri")


def test_case_rule_follows_this_platform_not_a_lowercase_hack():
    """Windows 上大小写是显示差异；POSIX 上它是数据的一部分，不许无条件抹平。"""
    assert _cwd_under_folder("D:/PROJECTS/SINDRI/x", "D:/projects/Sindri") is (os.name == "nt")


def test_unbound_agent_gets_nothing_rather_than_a_guess():
    assert not _cwd_under_folder("D:/anything/at/all", "")
    assert not _cwd_under_folder("", "D:/projects/Sindri")


# ── 它说的话要留得下来：回执文件与口头回话是两句不同的话 ─────────────
# 10-22 实测那一趟（qoder／D:\Projects\Sindri／24.9s／11 个落盘产物）干了活也回了话，
# 只是没按我们给的路径写回执文件。通道把正文解析出来了，却在「通道形状 → 台账形状」
# 那一跳被丢掉 ⇒ 抽屉里除了「它没回话」无事可显示。


def test_the_answer_it_spoke_survives_into_the_ledger(tmp_path):
    ledger = RoundLedger(root=tmp_path)
    row = ledger.append(OffloadResult(
        agent="qoder", cwd="D:/Projects/Sindri", permission_flag="--permission-mode default",
        duration_seconds=24.9, cost_status="no_usage_reported",
        job="kQ7dA1mZx9pLb3Tn", reply_status="not_filed", reply_to="Sindri",
        answer="README 的第一个标题是 `Hello_Sindri`，我按形状写不进回执文件（权限层拒了）。",
    ), brief="读 README 的第一个标题，写进回执文件")
    assert row["reply"] == "" and row["reply_status"] == "not_filed"
    assert row["answer_excerpt"].startswith("README 的第一个标题是")
    on_disk = [json.loads(l) for l in ledger.path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert on_disk[0]["answer_excerpt"] == row["answer_excerpt"]


def test_a_spoken_answer_is_never_reported_as_silence():
    """「没写回执文件」和「整趟没吭声」是两回事，界面上必须各说各的。"""
    spoke = summarize_round([OffloadResult(
        agent="qoder", cwd="D:/Projects/Sindri", permission_flag="--permission-mode default",
        duration_seconds=24.9, cost_status="no_usage_reported", reply_status="not_filed",
        answer="标题是 Hello_Sindri。")])
    silent = summarize_round([OffloadResult(
        agent="qoder", cwd="D:/Projects/Sindri", permission_flag="--permission-mode default",
        duration_seconds=24.9, cost_status="no_usage_reported", reply_status="not_filed")])
    filed = summarize_round([OffloadResult(
        agent="qoder", cwd="D:/Projects/Sindri", permission_flag="--permission-mode default",
        duration_seconds=24.9, cost_status="no_usage_reported",
        reply="标题是 Hello_Sindri。", reply_status="filed", answer="（顺带也这么说的）")])

    assert "回执文件它没写，但它口头回了：标题是 Hello_Sindri。" in spoke
    assert "它没回话" not in spoke
    assert "它没回话" in silent
    # 它自己写回来的那份优先，不能被口头那截抢掉
    assert "它自己回：标题是 Hello_Sindri。" in filed and "口头回" not in filed
