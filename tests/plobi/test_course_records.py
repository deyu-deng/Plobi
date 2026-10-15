"""课程归属查询的两条钉子（`WP-COURSE-DATA-MOUTH`）——负例优先，写成关系不写数值快照。

跑法：`bash scripts/run_tests.sh tests/plobi/test_course_records.py -q`（唯一入口，不传 `--basetemp`）。

1. **匹配规则**：作业按 `evidence.course` 等值；课表只能按标题对，而那条规则必须是单向的——
   串课（「电工电子学」罩进「电工电子学实验」）与漏课（长名对不上短名）各挡一次。
2. **话要说准**：库里真没有、与库读不到，是两句话；把后者说成前者就是骗用户"你没作业"
   （18:22 那次实况是它在硬盘上搜不到，于是回答"我没有你任何作业提交记录的数据"，而库里 50 条）。
"""

from __future__ import annotations

import json
import sqlite3

from plobi.agenda.course_records import NO_STORE, records_for_course

DDL = ("基础部分实验报告（组作业）", "第6次课后作业-(2.3)")


def _store(tmp_path, events):
    db = tmp_path / "agenda.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE events (title TEXT, kind TEXT, start_at TEXT, end_at TEXT, status TEXT, evidence TEXT)")
    conn.executemany("INSERT INTO events VALUES (?,?,?,?,?,?)", events)
    conn.commit()
    conn.close()
    return db


def _ddl(title, course, due="2026-11-02T22:30"):
    return (title, "ddl", due, "", "pending", json.dumps({"course": course, "due": due, "uid": title}))


def _class(title, start="2026-10-20T10:00"):
    return (title, "class", start, "", "confirmed", None)


def test_the_title_rule_blocks_both_wrong_matches(tmp_path):
    """漏课与串课各挡一次；作业那一侧按 `evidence.course` 等值取。"""
    db = _store(tmp_path, [
        _ddl(DDL[0], "电工电子学"), _ddl(DDL[1], "电工电子学实验"),
        _class("电工电子学"), _class("电工电子学实验"), _class("大学物理实验"),
    ])

    # 串课挡住：短名不许把长名的条目一起罩进来（全等 1 条 vs 包含 2 条）
    short = records_for_course("电工电子学", db_path=db, kind="class")
    assert [r["title"] for r in short["records"]] == ["电工电子学"]

    # 漏课挡住：名册带年份节次的长名要能对上课表用的短名
    long_name = records_for_course("大学物理实验2026周四345", db_path=db, kind="class")
    assert [r["title"] for r in long_name["records"]] == ["大学物理实验"]

    # 反方向也挡：长名自己有全等时，前缀规则不许把短名那门课一起够走
    # （这台现核错过一次——「电工电子学实验」读出 48 条 = 自己 16 + 别人 32）
    other = records_for_course("电工电子学实验", db_path=db, kind="class")
    assert [r["title"] for r in other["records"]] == ["电工电子学实验"]

    # 作业那一侧：只认自己那门，标题与截止日期都答得出
    ddl = records_for_course("电工电子学", db_path=db, kind="ddl")
    assert [r["title"] for r in ddl["records"]] == [DDL[0]]
    assert ddl["records"][0]["due"]


def test_absent_and_unreadable_are_two_different_sentences(tmp_path):
    """「库里没有」与「库读不到」不许汇成一句，也不许把前者说成后者。"""
    db = _store(tmp_path, [_ddl(DDL[0], "电工电子学"), _class("电工电子学")])

    absent = records_for_course("马克思主义基本原理", db_path=db, kind="ddl")
    assert absent["ok"] is True and absent["count"] == 0
    assert "没有 ddl 记录" in absent["detail"] and NO_STORE not in absent["detail"]

    unreadable = records_for_course("电工电子学", db_path=tmp_path / "不在这里.db", kind="ddl")
    assert unreadable["ok"] is False and unreadable["count"] == 0
    assert "读不到" in unreadable["detail"] and "没有 ddl 记录" not in unreadable["detail"]

    # 课表那一侧同一条规矩：这门有作业、没课表条目，两句话不许互相顶
    no_class = records_for_course("电工电子学", db_path=tmp_path / "不在这里.db", kind="class")
    assert no_class["ok"] is False and "课表条目" not in no_class["detail"]
