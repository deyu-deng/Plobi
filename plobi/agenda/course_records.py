"""课程那型 L2 的两张**只读**归属查询：作业（ddl）与课表条目（class）。

为什么要有这一档（`WP-COURSE-DATA-MOUTH`）：18:22 实况是课程 Agent 用 `search_files` 在硬盘上搜自己
那门课的作业，命中 0 条，于是回了一句「我没有你任何作业提交记录的数据」——**库里其实有**：
`agenda.db` 的 `kind='ddl'` 共 50 条，每一条 `evidence.course` 都是名册里那个课程名。
数据在场、缺的只是归属查询，所以这一档不开新的存储、**也不许把 events 复制进 profile**（那是 R-096 的反方向）。

三条口径：

1. **作业按 `evidence.course` 等值**取 —— 站方给的就是名册那个名字（现核 5 门课 41/3/2/2/2 条）。
2. **课表条目只能按标题对**（那 240 条 `evidence.course` 全为 NULL），而规则必须是**单向**的：
   名册名可能带年份与节次（`大学物理实验2026周四345`），课表标题用的是短名（`大学物理实验`）⇒
   命中 = 标题与名册名全等，**或**名册名以标题开头且比它长。
   反方向（标题以名册名开头且更长）**不算命中** —— 那正是「电工电子学」把「电工电子学实验」16 条
   罩进来的串课形状（全等 32 条 vs 包含 48 条，现核）。
3. **查不到要说清是哪一种查不到**：这门课在库里没有 ddl 记录、还是没有课表条目、还是整库读不到。
   把"读不到"讲成"没有"就是骗；把"没搜到文件"讲成"没作业"也已经真发生过一次。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

NO_STORE = "这一门查不了：日程库（`agenda.db`）读不到或读不动——这不是「没有作业」，是没尺"


@dataclass(frozen=True)
class CourseRecord:
    title: str
    start_at: str
    end_at: str
    status: str
    due: str

    def as_dict(self) -> dict[str, Any]:
        return {"title": self.title, "start_at": self.start_at, "end_at": self.end_at,
                "status": self.status, "due": self.due}


def _default_db() -> Optional[Path]:
    try:
        from plobi_constants import get_plobi_home

        return get_plobi_home() / "plobi" / "agenda.db"
    except Exception:                                 # noqa: BLE001 - 常量读不到就是查不了
        return None


def _open(db_path: Optional[Path]) -> sqlite3.Connection:
    path = Path(db_path) if db_path else _default_db()
    if path is None or not path.is_file():
        raise OSError(f"日程库不在或读不到：{path}")
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def _course_of(evidence: Any) -> str:
    return _field_of(evidence, "course")


def _due_of(evidence: Any) -> str:
    return _field_of(evidence, "due")


def _field_of(evidence: Any, key: str) -> str:
    try:
        return str(json.loads(evidence or "{}").get(key) or "").strip()
    except (ValueError, TypeError):
        return ""


def _match_class_titles(registry_name: str, titles: list[str]) -> tuple[list[str], str]:
    """课表短名 ⇔ 名册全名：先全等，**全等存在就不许再拿前缀去够别的课**。

    现核形状（`agenda.db` 240 条 class）：库里「电工电子学」(32) 与「电工电子学实验」（16）
    **各自都是独立标题**。只按"名册名以标题开头"会倒过来串课——长名把短名那 32 条也罩进来
    （实测 48 = 16 + 32）。所以规则两条，顺序要紧：

    1. 有全等 ⇒ 只取全等（课表已经用这个名字认识这门课）；
    2. 没全等 ⇒ 才允许"名册名以标题开头且更长"那种短名（`大学物理实验2026周四345` ⇔ `大学物理实验`）。
    """
    name = registry_name.strip()
    exact = [t for t in titles if t.strip() == name]

    if exact:
        return exact, f"全等命中 {len(exact)} 条"

    loose = [t for t in titles if t.strip() and name.startswith(t.strip()) and len(name) > len(t.strip())]

    return loose, (f"没有全等标题，按短名前缀命中 {len(loose)} 条" if loose else "全等与前缀都没命中")


def records_for_course(course: str, *, db_path: Optional[Path] = None,
                       kind: str = "ddl", limit: int = 200) -> dict[str, Any]:
    """一门课的只读归属读数。`kind` ∈ {"ddl", "class"}；两者各查各的，不互相顶。"""
    name = (course or "").strip()
    if not name:
        return {"ok": False, "course": "", "kind": kind, "count": 0, "records": [],
                "detail": "没给课程名：这一问没有对象，不去猜是哪一门"}

    try:
        conn = _open(db_path)
    except (OSError, sqlite3.Error) as exc:
        return {"ok": False, "course": name, "kind": kind, "count": 0, "records": [],
                "detail": f"{NO_STORE}（{exc}）"}

    try:
        conn.row_factory = sqlite3.Row
        if kind == "ddl":
            rows = [CourseRecord(str(r["title"]), str(r["start_at"] or ""), str(r["end_at"] or ""),
                                 str(r["status"] or ""), _due_of(r["evidence"]))
                    for r in conn.execute(
                        "SELECT title, start_at, end_at, status, evidence FROM events"
                        " WHERE kind = 'ddl' AND json_extract(evidence, '$.course') = ?"
                        " ORDER BY start_at LIMIT ?", (name, limit))]
        elif kind == "class":
            fetched = [(str(r["title"]), str(r["start_at"] or ""), str(r["end_at"] or ""), str(r["status"] or ""))
                       for r in conn.execute(
                           "SELECT title, start_at, end_at, status FROM events"
                           " WHERE kind = 'class' ORDER BY start_at")]
            picked, rule = _match_class_titles(name, [row[0] for row in fetched])
            matched = set(picked)
            rows = [CourseRecord(title, start, end, status, "")
                    for title, start, end, status in fetched if title in matched]
            # 截断只发生在过滤之后：先 LIMIT 再按标题对会把后面的课整个丢掉
            # （这台 240 条现核就这么少算过：电工电子学 32 条读成 27 条）。
            rows = rows[:limit]
        else:
            return {"ok": False, "course": name, "kind": kind, "count": 0, "records": [],
                    "detail": f"不认识的读数种类 {kind!r}：只查 ddl（作业）与 class（课表条目）"}
    except (OSError, sqlite3.Error, ValueError) as exc:
        return {"ok": False, "course": name, "kind": kind, "count": 0, "records": [],
                "detail": f"读不动日程库：{exc}"}
    finally:
        conn.close()

    if rows:
        how = f"（{rule}）" if kind == "class" else ""
        detail = f"这一门在库里有 {len(rows)} 条{'作业' if kind == 'ddl' else '课表条目'}{how}"
    else:
        detail = (f"这一门「{name}」在库里没有 {'ddl 记录' if kind == 'ddl' else '课表条目'}"
                  "——这是查过的结果，不是读不到；换一种问法或看另一种种类，别把它当成『没有这回事』")

    return {"ok": True, "course": name, "kind": kind, "count": len(rows),
            "records": [r.as_dict() for r in rows], "detail": detail}
