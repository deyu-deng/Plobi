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


def test_the_two_spellings_of_the_same_course_are_one_course() -> None:
    """名册里同一门课有两种拼法：`display_name` 用 U+2161（Ⅱ），`project_path` 末段用 ASCII（II）。

    架构师 10-16 现核点名（这台真名册 `大学物理（甲）Ⅱ` ⇔ 文件夹 `...大学物理（甲）II`）。
    真源不认这一对就还会串：**认不上**退成无主、**乱认**退成挂到隔壁班。
    归一化只做**字符等值**——`Ⅰ` 与 `Ⅱ` 必须是两门课，不许被折成同一个 key。
    """
    ddl_ascii = _ddl("第3次作业", "大学物理（甲）II", "7001")
    ddl_roman = _ddl("第4次作业", "大学物理（甲）Ⅱ", "7002")
    ddl_first = _ddl("第1次作业", "大学物理（甲）I", "7003")

    assert matches_course(ddl_ascii, "大学物理（甲）Ⅱ") is True        # 名册 Ⅱ 问得到库里的 II
    assert matches_course(ddl_roman, "大学物理（甲）II") is True        # 反向同样成立
    assert matches_course(ddl_first, "大学物理（甲）Ⅱ") is False       # I 不是 II
    assert matches_course(ddl_ascii, "大学物理（甲）Ⅱ", other_names=("大学物理（甲）I",)) is True

    # 第二形（现核这台 8 门课的 `project_path` 末段全是它）：学期写在**前面**。
    # 教材就在那些文件夹里，别名不截这一头，「这本书/这门课」与「这份作业」两头对不上。
    from plobi.agenda.planning import course_key

    assert course_key(course_alias("2026-2027秋冬-大学物理（甲）II")) == course_key("大学物理（甲）\u2161")
    assert course_key(course_alias("2026-2027秋冬-大学物理（甲）I")) != course_key("大学物理（甲）\u2161")
    assert course_alias("电工电子学") == "电工电子学", "两头都没有时原样返回，不许截掉真名"


def test_the_reading_side_resolves_both_spellings_and_never_globs(tmp_path) -> None:
    """课程 Agent 查自己的作业时，库里另一种拼法也得查得到（SQL 的 `= ?` 看不见这一对）。

    负例要紧：折拼法**不许**把隔壁班（`大学物理（甲）I`）捞进来——查不到就说查不到。
    """
    import json
    import sqlite3

    from plobi.agenda.course_records import records_for_course

    def _db(tag: str, stored: str):
        db = tmp_path / f"agenda-{tag}.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE events (title TEXT, start_at TEXT, end_at TEXT,"
                     " status TEXT, kind TEXT, evidence TEXT)")
        conn.execute("INSERT INTO events VALUES (?,?,?,?,?,?)",
                     ("第3次作业", "2026-10-18T23:55:00", None, "pending", "ddl",
                      json.dumps({"course": stored, "course_id": "7001"}, ensure_ascii=False)))
        conn.execute("INSERT INTO events VALUES (?,?,?,?,?,?)",
                     ("隔壁班作业", "2026-10-19T23:55:00", None, "pending", "ddl",
                      json.dumps({"course": "大学物理（甲）I", "course_id": "7003"}, ensure_ascii=False)))
        conn.commit()
        conn.close()
        return db

    # 库里存 ASCII、名册问 U+2161（这台名册的真实形状）
    got = records_for_course("大学物理（甲）\u2161", db_path=_db("ascii", "大学物理（甲）II"), kind="ddl")
    assert got["ok"] is True and got["count"] == 1, got
    assert [r["title"] for r in got["records"]] == ["第3次作业"]

    # 反过来也成立
    back = records_for_course("大学物理（甲）II", db_path=_db("roman", "大学物理（甲）\u2161"), kind="ddl")
    assert back["count"] == 1, back

    # 隔壁班不是这一门：查不到就说查不到，不靠子串去够
    wrong = records_for_course("大学物理（甲）\u2161", db_path=_db("first", "大学物理（甲）I"), kind="ddl")
    assert wrong["ok"] is True and wrong["count"] == 0, wrong
    assert "查过的结果" in wrong["detail"]
