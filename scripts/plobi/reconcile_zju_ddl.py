"""`WP-DDL-SUBMITTED-RECON` 判据 1 的那张对账表——**只读**：不写库、不改状态、不删行。

库里 `source='zju'` 的作业行 × 学在浙大**本轮未提交清单**逐条对上，每条落三态之一：
`gone`（清单里没了）／`present`（还在）／`present_with_status`（还在且带状态字段——交原名与原值，不解释含义）。

四条硬规矩写死在这里：
- **不绕限速**：`min_minutes_between_pulls`（默认 45 分钟 + 抖动）没到点就**连学校都不碰**，
  报 `throttled` 与还需多久就走。轮询上限未实测（治理线只打过 2 成功 + 3 失败），绕它就是拿他的账号赌。
- **一轮只打一轮**：真打走产品自己那条 `pull_report()`（它会把这一轮记进限速状态），原始行由
  **注入的那层 transport 顺手抄一份**——`transport` 必须显式注入是裁定 104 二定的，这里只是包一层转发。
- **凭据不进输出**：只打条数、字段名、站方 id、标题与标量状态位；口令/用户名/Cookie 一个字节不落。
- **副本上数**：`agenda.db` 连 `-wal`/`-shm` 一起拷出来再读；真库带活 WAL 直接开会影响下一个读它的人。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Mapping, Optional

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from plobi.collectors.zju import auth as zju_auth
from plobi.collectors.zju.collector import HOMEWORK_TYPE, ZjuCollector
from plobi.collectors.zju.config import ZjuConfig, read_state

#: 字段名里带这些片段的算"状态位"。**只交原名与原值**——含义要另找依据，
#: 一次缺席推断就把作业判死，是这张表最不该产出的东西。
STATUS_HINTS = ("submit", "status", "state", "finish", "complete", "done", "lock", "is_")
TODO_MARK = "/api/todos"


class RecordingTransport:
    """原样转发那一发，只把 `/api/todos` 回体里的行抄下来当对账证据。"""

    def __init__(self, inner, sink: list) -> None:
        self._inner = inner
        self._sink = sink

    def _capture(self, url: str, text: str) -> None:
        if TODO_MARK not in str(url):
            return
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            return
        rows = parsed.get("data") if isinstance(parsed, dict) else parsed
        if isinstance(rows, dict):
            rows = rows.get("todos") or rows.get("list") or []
        if isinstance(rows, list):
            self._sink.extend([r for r in rows if isinstance(r, dict)])

    def get(self, url: str):
        response = self._inner.get(url)
        self._capture(url, getattr(response, "text", "") or "")
        return response

    def post_form(self, url: str, data: Mapping[str, str], *, referer: Optional[str] = None):
        response = self._inner.post_form(url, data, referer=referer)
        self._capture(url, getattr(response, "text", "") or "")
        return response


def status_fields(row: dict) -> dict:
    out = {}
    for key, value in row.items():
        if any(hint in str(key).lower() for hint in STATUS_HINTS):
            out[key] = (value if isinstance(value, (bool, int, float)) or value is None
                        else str(value)[:40])
    return out


def library_rows(copy: Path) -> list[dict]:
    conn = sqlite3.connect(str(copy))
    conn.row_factory = sqlite3.Row
    try:
        out = []
        for r in conn.execute("select title, start_at, status, evidence from events "
                              "where source='zju' order by start_at"):
            try:
                ev = json.loads(r["evidence"] or "{}")
            except ValueError:
                ev = {}
            out.append({"external_id": str(ev.get("uid") or ""), "title": r["title"],
                        "start_at": r["start_at"], "status": r["status"],
                        "is_locked": ev.get("is_locked"),
                        "prerequisite_count": ev.get("prerequisite_count")})
        return out
    finally:
        conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python scripts/plobi/reconcile_zju_ddl.py")
    ap.add_argument("--scratch", default=str(Path.home() / "Temp"), help="副本落点（默认用户临时区）")
    ap.add_argument("--limit", type=int, default=14, help="表里最多列几行（默认 14，够判三态）")
    ap.add_argument("--library-only", action="store_true",
                    help="完全不碰学校：只出库里这一侧（uid、截止、status、is_locked）与上一轮的真打记录")
    args = ap.parse_args(argv)

    cfg = ZjuConfig.load()
    state = read_state()
    if args.library_only:
        from plobi.agenda.store import agenda_db_path as _p

        _real = _p()
        _scratch = Path(args.scratch)
        _scratch.mkdir(parents=True, exist_ok=True)
        _copy = _scratch / "agenda-recon-library.sqlite"
        shutil.copyfile(_real, _copy)
        for _ext in ("-wal", "-shm"):
            if Path(str(_real) + _ext).exists():
                shutil.copyfile(str(_real) + _ext, str(_copy) + _ext)
        lib = library_rows(_copy)
        print(json.dumps({
            "verdict": "library_only",
            "detail": "这一趟一个字节都没碰学校；清单那一侧要等下一轮真打（限速没到就不打）",
            "last_pull": {"at_epoch": state.get("last_pull_at_epoch"),
                          "state": state.get("last_state"),
                          "rows_seen": state.get("rows_seen"),
                          "dropped": state.get("dropped"),
                          "endpoint": state.get("last_detail")},
            "library_ddl_rows": len(lib),
            "status_column_values": sorted({str(r.get("status")) for r in lib}),
            "is_locked_true": sum(1 for r in lib if r.get("is_locked") is True),
            "table": [{k: v for k, v in r.items() if k != "title"} | {"title": (r["title"] or "")[:30]}
                      for r in lib[: args.limit]],
        }, ensure_ascii=False, indent=1))
        return 0
    last = float(state.get("last_pull_at_epoch") or 0.0)
    now = time.time()
    if not cfg.pull_allowed_now(last, now):
        left = cfg.min_minutes_between_pulls - (now - last) / 60.0
        print(json.dumps({
            "verdict": "throttled",
            "detail": (f"上一轮真打 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(last))}"
                       f"（{state.get('last_state')}，rows_seen={state.get('rows_seen')}，"
                       f"dropped={state.get('dropped')}）；最短间隔 {cfg.min_minutes_between_pulls} 分钟带抖动"
                       f"还没到，约还需 {max(left, 0):.1f} 分钟——这一趟连学校都没碰"),
        }, ensure_ascii=False))
        return 2

    sink: list[dict] = []
    collector = ZjuCollector(config=cfg, transport=RecordingTransport(zju_auth.UrllibTransport(), sink))
    report = collector.pull_report()               # ← 整个脚本唯一一次真打学校
    if not report.ok:
        print(json.dumps({"verdict": report.state, "detail": report.detail,
                          "occurrences_captured": len(sink)}, ensure_ascii=False))
        return 4

    live = {str(r.get("id")): r for r in sink if r.get("id")}
    homework = {k: v for k, v in live.items() if v.get("type") == HOMEWORK_TYPE}

    from plobi.agenda.store import agenda_db_path

    real = agenda_db_path()
    scratch = Path(args.scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    copy = scratch / "agenda-recon.sqlite"
    shutil.copyfile(real, copy)
    for ext in ("-wal", "-shm"):
        if Path(str(real) + ext).exists():
            shutil.copyfile(str(real) + ext, str(copy) + ext)
    lib = library_rows(copy)

    table = []
    for row in lib:
        hit = homework.get(row["external_id"])
        if hit is None:
            table.append({"external_id": row["external_id"], "title": row["title"][:34], "state": "gone"})
            continue
        fields = status_fields(hit)
        table.append({"external_id": row["external_id"], "title": row["title"][:34],
                      "state": "present_with_status" if fields else "present",
                      "status_fields": fields or None})

    print(json.dumps({
        "verdict": "ok",
        "pulled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "rows_seen_by_collector": report.rows_seen,
        "rows_captured": len(sink),
        "homework_rows": len(homework),
        "library_ddl_rows": len(lib),
        "occurrences": len(report.occurrences),
        "dropped": [d.get("reason") for d in report.dropped][:6],
        "counted": {k: sum(1 for t in table if t["state"] == k)
                    for k in ("gone", "present", "present_with_status")},
        "list_field_names": sorted({key for r in sink[:80] for key in r}),
        "in_list_not_in_library": sorted(set(homework) - {r["external_id"] for r in lib})[:10],
        "table": table[: args.limit],
        "throttle_restarted_minutes": cfg.min_minutes_between_pulls,
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
