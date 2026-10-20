"""复跑「微信这一腿按会话带时间窗能不能进池」这一格——零模型、零写库、一行结论。

判据写成人话没用，得是一支能重跑的命令（裁 174）。而 `plobi.pool.land` 没有 dry-run 出口：
落点就是解析出来的那个库文件。所以这里**把 `pipeline` 绑定的那个名字换成数一数的桩**——
真的拉、真的过规则、真的走到"这条该进池"那一步，唯独一个字都不往池里写。
往池里落是产品那一轮采集的事（`/api/collect/sweep`），不是这条命令的事。

三条口径：
- **一次补全都不发**：confirmer 只注 `HeuristicConfirmer`（它自己写着 "no network, no model,
  no token spend"）。时间钉不住的那些消息在生产里会去打模型，这里只数着报出来。
- **不碰任何真账**：走白名单档、只点前 N 扇窗口；`seen`／`talkers` 两档落在 `--out` 旁边。
  真 `seen` 要是被这种核对跑写满了，将来筛子改好之后那一窗永远回填不进来。
- **拿不到就说拿不到**：侧车不在、目录还在、hooks 那位在不在——全照实写进那行 JSON，不猜原因。
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

OLD_HOME = "tools/chatlog"
#: 那两条「装完钩子才会开始执行」的能力位，在本版 vendor 里到底有没有——格子里写过，就得能重验。
HOOK_MARKERS = ("hook_active", "GetRecentContacts", "/rpc/")


#: 裁 187① 那两列在采集侧的取值口径——**名字与取值以 Butler 的列契约为准**
#: （`plobi/pool/store.py`：`visibility` ∈ `private`／`group`；`conversation` = 那一扇窗口）。
#: Plan 线 10-20 07:5x 明确 `conversation` 取 `SessionTable.username` 那个 wxid，
#: 而 Butler 自己的测试那行写的是显示名（"工程训练·课群组"）——**这两处不一致，谁定稿谁改**；
#: 本命令按 wxid 报，因为采集侧手里那份就是 wxid（`message.talker`），显示名另有 `context.talker_name`。
GROUP_SUFFIX = "@chatroom"

def time_shape_ok(row, now: float) -> bool:
    """裁 189 那一列的两件事：**形状**与**语义**，都在这一个位上判。

    形状：过池那一侧同一个归一点（`store.normalize_time`，不在这里再拼一种格式），
    结果必须是 naive 本地 `YYYY-MM-DD HH:MM:SS`——`'T'`(0x54) 比 `' '`(0x20) 大，
    混着写时同一天前缀下谁排前面取决于谁先写入。
    语义：这一列得等于**这条消息自己的时间**（`context.sent_at` 那一位），不是
    `context.event_at`——后者是规则抽出来的「事情什么时候发生」，那 12 条里现读过
    `2026-10-24T14:00:00` 这种还没开的会被算进「今天收到的」，`since` 就不是它字面的意思了。
    """
    from plobi.pool.store import normalize_time

    canon, _raw = normalize_time(row.occurred_at)
    if len(canon) != 19 or "T" in canon:
        return False
    if canon > time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)):
        return False
    context = row.context or {}
    sent = normalize_time(context.get("sent_at"))[0]
    return bool(sent) and canon == sent


def expected_columns(row) -> dict:
    """这一条**本来该带上**的两列——证明采集侧在那个落点手上就有数据，不是还得去问谁。"""
    talker = str((row.context or {}).get("talker") or "")
    return {
        "visibility": "group" if talker.endswith(GROUP_SUFFIX) else "private",
        "conversation": talker,
    }


def _vendor_markers() -> dict[str, int]:
    root = REPO / "plobi" / "collectors" / "chatlog" / "vendor" / "chatlog"
    out = dict.fromkeys(HOOK_MARKERS, 0)
    if not root.is_dir():
        return dict.fromkeys(HOOK_MARKERS, -1)
    for path in root.rglob("*.go"):
        try:
            text = io.open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for marker in HOOK_MARKERS:
            out[marker] += text.count(marker)
    return out


def _old_home_tracked() -> int:
    try:
        proc = subprocess.run(["git", "-C", str(REPO), "ls-files", OLD_HOME],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return -1
    return len([line for line in proc.stdout.splitlines() if line.strip()])


def _real_pool_stamp() -> dict:
    from plobi.pool import path as pool_path

    try:
        stat = pool_path().stat()
        return {"exists": True, "bytes": stat.st_size, "mtime_ns": int(stat.st_mtime_ns)}
    except OSError:
        return {"exists": False, "bytes": 0, "mtime_ns": 0}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python scripts/plobi/verify_chatlog_window_cell.py")
    ap.add_argument("--days", type=int, default=7, help="每扇窗口往回带几天时间窗（默认 7）")
    ap.add_argument("--talkers", type=int, default=20, help="只点前 N 扇窗口（默认 20，别全量跑）")
    ap.add_argument("--out", default="", help="临时 seen／talkers 落点，默认系统临时区下的一个子目录")
    args = ap.parse_args(argv)

    from plobi.collectors.chatlog import pipeline as pipeline_module
    from plobi.collectors.chatlog.client import ChatlogClient
    from plobi.collectors.chatlog.config import CollectorConfig
    from plobi.collectors.chatlog.confirm import HeuristicConfirmer
    from plobi.collectors.chatlog.pipeline import ChatlogPipeline
    from plobi.collectors.chatlog.state import SeenStore, TalkerStore

    scratch = Path(args.out) if args.out else Path(tempfile.gettempdir()) / "plobi-chatlog-window-cell"
    scratch.mkdir(parents=True, exist_ok=True)
    seen_path = scratch / "seen.db"
    talkers_path = scratch / "talkers.db"
    for stale in (seen_path, talkers_path):
        if stale.exists():
            stale.unlink()  # 只清我自己这两份临时件

    cfg = CollectorConfig.load()
    client = ChatlogClient(cfg.base_url)
    before = _real_pool_stamp()

    tally = {"would_land": 0, "by_kind": {}, "rows": []}

    def _counting_land(items, *args, **kwargs):
        rows = list(items)
        tally["would_land"] += len(rows)
        for row in rows:
            tally["by_kind"][row.kind] = tally["by_kind"].get(row.kind, 0) + 1
            expected = expected_columns(row)
            tally["rows"].append([row.source, row.external_id, row.kind,
                                  len(row.body or ""), bool(row.title),
                                  row.visibility, row.conversation,
                                  list(expected.values()),
                                  time_shape_ok(row, now),
                                  bool((row.context or {}).get("event_at"))])
        return {"created": len(rows), "updated": 0, "unchanged": 0, "ids": []}

    healthy: dict = {"ok": False, "state": "not_read", "detail": "没跑到探活那一步", "binary": None}
    report: dict = {}
    scoped: list[str] = []
    note = ""
    now = time.time()
    original_land = pipeline_module.pool_land
    pipeline_module.pool_land = _counting_land
    try:
        pipe = ChatlogPipeline(config=cfg, client=client)
        healthy = pipe.health()
        if healthy["ok"]:
            all_sessions = client.list_talkers()
            scoped = all_sessions[: max(1, args.talkers)]
            pipe = ChatlogPipeline(
                config=dataclasses.replace(cfg, mode="whitelist", talkers=scoped, enabled=True),
                client=client,
                confirmer=HeuristicConfirmer(tier_of=cfg.tier_of),
                seen=SeenStore(seen_path),
                talkers=TalkerStore(talkers_path),
            )
            report = pipe.run_once(day=datetime.date.today(),
                                   lookback_days=max(1, args.days)).as_dict()
        else:
            note = f"侧车这一轮没答：{healthy['detail']}"
    except Exception as exc:  # noqa: BLE001 - 这一支命令的职责是报数，不是抛栈
        note = f"{type(exc).__name__}: {exc}"
    finally:
        pipeline_module.pool_land = original_land
        # 两份临时账本用完就清掉——落点是平台无关解析来的，但"C 盘不许残留运行数据"这条
        # 不因它是临时件就豁免（D:\_POLICY）。要留现场就带 --out 指到自己的目录。
        for stale in (seen_path, talkers_path):
            try:
                stale.unlink()
            except OSError:
                pass

    scanned = int(report.get("scanned", 0))
    filtered = int(report.get("filtered_out", 0))
    unresolved = int(report.get("unresolved", 0))
    created = len(report.get("created", []) or [])
    dup = int(report.get("skipped_duplicate", 0))
    after = _real_pool_stamp()
    markers = _vendor_markers()

    verdict = {
        "sidecar_healthy": bool(healthy["ok"]),
        "sidecar_state": healthy["state"],
        "binary_home_is_new": OLD_HOME not in str(healthy.get("binary") or ""),
        "old_home_tracked_files": _old_home_tracked(),
        "vendor_hook_markers": markers,
        "hook_surface_present": any(count > 0 for count in markers.values()),
        "sessions_enumerated": len(scoped),
        "window_days": max(1, args.days),
        "model_calls": 0,
        "counters": {"scanned": scanned, "rule_hits": scanned - filtered - unresolved - dup,
                     "filtered_out": filtered, "unresolved": unresolved,
                     "skipped_duplicate": dup, "landed_this_run": created},
        "pooled_would_land": tally["would_land"],
        # 一条都没命中时是 None（不知道），不是 True——空集上的 all() 恒真，
        # 那会把"这一刀没采到东西"洗成"两列都填好了"。
        "time_shape_ok": None if not tally["rows"]
                       else all(row[8] for row in tally["rows"]),
        "event_at_in_context": None if not tally["rows"]
                               else all(row[9] for row in tally["rows"]),
        "columns_filled": None if not tally["rows"]
                        else all(row[5] and row[6] for row in tally["rows"]),
        "columns_expected_from": [row[7] for row in tally["rows"]][:2],
        "pooled_by_source": {"wechat": tally["would_land"]} if tally["would_land"] else {},
        "pooled_by_kind": tally["by_kind"],
        "sample_row": tally["rows"][:3],
        "real_pool_untouched": before == after,
        "scratch": str(scratch),
        "note": note,
    }
    print(json.dumps(verdict, ensure_ascii=False))

    ok = (verdict["sidecar_healthy"] and verdict["binary_home_is_new"]
          and verdict["old_home_tracked_files"] == 0
          and not verdict["hook_surface_present"]
          and verdict["real_pool_untouched"] and verdict["model_calls"] == 0
          and verdict["pooled_would_land"] > 0
          and verdict["columns_filled"] is True
          and verdict["time_shape_ok"] is True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
