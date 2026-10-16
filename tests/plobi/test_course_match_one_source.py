"""课名匹配收成一份真源（`WP-COURSE-MATCH-ONE-SOURCE`）：子串匹配必须死。

治理线在**真库只读副本**上实测过：名册「电工电子学」靠 `needle in value` 把
「电工电子学实验」的 2 条作业也认领走（`evt_1842479535ce` / `evt_3fd8c0dedcf3`）⇒ 两门课的
「下一个 DDL」是同一块数据。两条测试：

1. **负例**：两门课各只拿自己的作业，简称抢不走全称那一条。
2. **别名与 ID 级**：名册带年份节次的长名对上课表短名（全等比较，不是包含）；
   `calendar_name` 那格不算归属——现核 240 条 class 的它全是 `浙大课程表-2026-2027秋冬`（日历名）。
"""

from __future__ import annotations

from plobi.agenda.planning import course_alias, matches_course
from plobi.agenda.store import Event


def _ddl(title: str, course: str, cid: str) -> Event:
    return Event(id=f"evt_{cid}", title=title, start_at="2026-10-16T23:55:00", kind="ddl",
                 evidence={"uid": cid, "course": course, "course_id": cid})


def _class(title: str, calendar_name: str = "浙大课程表-2026-2027秋冬") -> Event:
    return Event(id=f"cls_{title}", title=title, start_at="2026-10-16T08:00:00", kind="class",
                 evidence={"uid": title, "calendar_name": calendar_name, "teacher": "孙晖"})


def test_each_course_gets_only_its_own_ddl() -> None:
    lab = _ddl("实验报告提交-实验三", "电工电子学实验", "2001")
    theory = _ddl("第5次课后作业-(2.3)", "电工电子学", "2002")
    roster = ("电工电子学", "电工电子学实验")

    assert matches_course(theory, "电工电子学", other_names=roster) is True
    assert matches_course(lab, "电工电子学", other_names=roster) is False      # 抢不走
    assert matches_course(lab, "电工电子学实验", other_names=roster) is True
    assert matches_course(theory, "电工电子学实验", other_names=roster) is False

    # ID 级：站方改了显示名但课程号没变，仍然归同一门课
    renamed = _ddl("示波器的使用+声速测定预习报告", "大学物理实验（2026 周四）", "99659")
    assert matches_course(renamed, "随便什么名字", course_ids=("99659",)) is True


def test_the_class_side_uses_titles_not_the_calendar_name() -> None:
    phylab = _class("大学物理实验")
    ee = _class("电工电子学")
    ee_lab = _class("电工电子学实验")

    assert course_alias("大学物理实验2026周四345") == "大学物理实验"
    assert matches_course(phylab, "大学物理实验2026周四345") is True           # 别名全等
    assert matches_course(ee, "电工电子学实验") is False                        # 别名不许罩短名
    assert matches_course(ee_lab, "电工电子学实验") is True
    assert matches_course(ee_lab, "电工电子学") is False

    # 判据那句「按 calendar_name 全等」按现核更正：这一格所有行都同值，不是课名
    same_calendar = _class("马克思主义基本原理")
    assert matches_course(same_calendar, "电工电子学") is False
