"""`checkin_cards` 不许是"有表没数据"的第三态（`WP-PLAN-COMPLETION-ACK` 必做 4）。

现核（10-16，真家只读）：`plobi/agenda/store.py:325` 建了 `checkin_cards`，写者是
`plobi/agenda/checkin.py`（`build_checkin` → `store.upsert_checkin_card`），触发者是每晚 21:30 那条
`plobi-daily-checkin`（注册表在 `plobi/butler/nightly.py:47`，脚本 `scripts/plobi/cron_daily_checkin.py`），
入口是 `scripts/plobi/register_butler.py` → `register_butler_jobs`。这台家 `cron/jobs.json` 里
**只有** `plobi-evening-plan` 与 `plobi-source-collect` 两条产品作业，`plobi-daily-checkin` 从未注册，
家 `scripts/` 里也只有那两个脚本 ⇒ 表里 0 行。

producer 本体不在这档重复钉（`tests/plobi/test_checkin.py` 17 条覆盖选题/去重/状态机/降频/起草回落）。
本档只钉**接线**：表有真来源这件事必须能在 CI 里被证伪，不能靠人记着——
谁把脚本删了、改名了、schedule 写错了、或者把注册入口撤了而把表留着，这里当场红。
"""

from __future__ import annotations

import pathlib
import re

from plobi.agenda import store
from plobi.butler import nightly

REPO_ROOT = pathlib.Path(nightly.__file__).resolve().parents[2]
CHECKIN_JOB = "plobi-daily-checkin"


def _job_row(name: str) -> tuple[str, str, str, str]:
    return next(row for row in nightly.BUTLER_JOBS if row[0] == name)


def test_every_butler_job_has_a_real_script_on_disk():
    """「有表没数据」的第一种成因就是 producer 被搬走而注册表还留着那一行。"""
    for name, src_script, dst_script, schedule in nightly.BUTLER_JOBS:
        source = REPO_ROOT / "scripts" / "plobi" / src_script
        assert source.is_file(), f"{name} 的源脚本不存在：{source}"
        assert dst_script.endswith(".py") and "/" not in dst_script, (name, dst_script)
        assert len(schedule.split()) == 5, (name, schedule)
        assert name and src_script, (name, src_script)


def test_the_checkin_job_is_the_writer_of_checkin_cards():
    """`checkin_cards` 的触发者必须还在这张表里，且它那一行指着的脚本真会写这张表。"""
    _name, src_script, _dst, schedule = _job_row(CHECKIN_JOB)
    assert schedule == "30 21 * * *", schedule          # 每晚那一轮回访的时间点
    script = (REPO_ROOT / "scripts" / "plobi" / src_script).read_text(encoding="utf-8")
    assert "build_checkin" in script and "format_checkin_text" in script, src_script

    # 写这张表的那一手确实存在（不是注释里说说）：checkin → store.upsert_checkin_card
    checkin_src = (REPO_ROOT / "plobi" / "agenda" / "checkin.py").read_text(encoding="utf-8")
    assert re.search(r"store\.upsert_checkin_card\(", checkin_src), "回访不再写 checkin_cards 了，这张表成了孤儿"


def test_the_registration_entry_is_still_reachable_as_a_command():
    """把第三态收掉的那一步是**一条命令**，不是一段口述——入口没了就等于撤表。"""
    entry = REPO_ROOT / "scripts" / "plobi" / "register_butler.py"
    assert entry.is_file(), entry
    body = entry.read_text(encoding="utf-8")
    assert "register_butler_jobs" in body, "注册入口被撤了，而 checkin_cards 还留着"

    import plobi.agenda.checkin as checkin

    assert hasattr(checkin, "build_checkin") and hasattr(store, "upsert_checkin_card")
