"""cron no_agent 脚本：每天一轮信源采集（用户 2026-10-15 13:0x 拍：新条目进待确认）。

执行方在 ``plobi/collectors/sweep.py``。它只问注册表两件事——这条点没点采集开关、
有没有构造得出它的入口——**不按信源名字分支**（判据 3），所以加一个新平台不需要动这个脚本。

读数口径与其它 cron 脚本同一族：**具名失败，同一句既进 stdout（cron 的告警正文取它）
也进 stderr（留痕）**，不留 traceback-only 的失败。退出码的语义是这条链的真话：

- 至少有一条真跑了一轮 ⇒ 0（其余条目的失败照样逐条打在 JSON 里，不合并成"成功"）。
- **一条都没跑成 ⇒ 1**，正文就是那句 verdict 加每条自己的状态（教务掉登录、构造失败、
  注册表说在采却没有执行方——三种是不同的话，不许都写成"没数据"）。
- **一条都没到点 ⇒ 0**（裁定 191 二那一格接上之后新添的一行）。"这一轮按每条信源自己的频率
  谁都不该打"不是故障：把它报成红灯就等于每天给用户发一次假事故。没到点的那几条**照样逐条
  出现在读数里**（``state=not_due`` + 下一次该打在几点），所以"为什么今天没打它"是一行读数，
  不是要人去猜。真跑过的那一家会被写进台账（``cadence.mark_run``），下一轮才知道到没到点。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _bootstrap() -> None:
    try:
        import plobi  # noqa: F401

        return
    except ImportError:
        pass

    home = Path(os.environ.get("PLOBI_HOME") or Path.home() / ".plobi")
    runtime = home / "plobi" / "runtime.json"
    try:
        code_root = json.loads(runtime.read_text(encoding="utf-8"))["code_root"]
        if code_root and code_root not in sys.path:
            sys.path.insert(0, str(code_root))
    except (OSError, json.JSONDecodeError, KeyError):
        print(json.dumps({"error": f"cannot locate code root via {runtime}"}))
        raise SystemExit(1)


_bootstrap()


#: 这些状态是**这一轮没打它**，不是"打失败了"：不进退出码，但每一条都要逐条出现在读数里。
#: `invalid_cadence`（用户那句频率写坏了）与 `exhausted`（那一句不会再触发）也归这一族——
#: 它们该被**看见**（verdict 里点名），不该被报成"采集坏了"。
NOT_DUE_STATES = ("not_due", "invalid_cadence", "exhausted")


def main() -> int:
    from plobi.collectors import cadence
    from plobi.collectors.sweep import run_sweep

    due, skipped = cadence.partition(_collecting_sources())
    report = run_sweep(sources=due) if due else None
    rows: list[dict] = sorted([*(report or {}).get("sources", []), *skipped],
                              key=lambda row: str(row.get("source") or ""))

    for row in rows:                                  # 打过的一律记账：频率问的是"多久碰它一次"
        if row["state"] in NOT_DUE_STATES:
            continue
        cadence.mark_run(str(row["source"]), state=str(row.get("state") or ""))

    attempted = [row for row in rows if row["state"] not in NOT_DUE_STATES]
    ran = [row for row in attempted if row["ok"]]
    bad_cadence = [row for row in rows if row["state"] in ("invalid_cadence", "exhausted")]

    if not ran:
        if attempted:
            verdict = (report or {}).get("verdict") or f"{len(attempted)} 条一条都没真跑成"
        elif not rows:
            verdict = "没有任何一条信源被点亮（`collecting_sources()` 是空的）：这一轮什么都不会做"
        else:
            verdict = f"{len(rows)} 条这一轮都没到点：" + "、".join(
                f"{row['source']}（{row['state']}，下一次 {row.get('next_run_at') or '未设'}）"
                for row in rows)
        if bad_cadence:
            verdict += f"；其中 {len(bad_cadence)} 条那句频率写坏了或不再触发：" + \
                "、".join(f"{row['source']}={row.get('cadence')!r}" for row in bad_cadence)
    else:
        verdict = report["verdict"] if report else ""
        if skipped:
            verdict += f"；另有 {len(skipped)} 条按各自频率本轮不打：" + \
                "、".join(str(row["source"]) for row in skipped)

    payload = {"sources": rows, "ran": len(ran), "attempted": len(attempted),
               "total": len(rows), "verdict": verdict}
    print(json.dumps(payload, ensure_ascii=False))

    if ran:
        return 0

    if not rows:
        # 注册表一条都没点亮：这不是"没到点"，是**没有东西在采**（当年 `no_builder` 那个形状）。
        empty = json.dumps({"outcome": "failed", "cause": "no_source_registered",
                            "detail": verdict}, ensure_ascii=False)
        print(empty)
        print(empty, file=sys.stderr)
        return 1

    if not attempted:
        return 0                                      # 全部没到点：正常的一轮，正文已经逐条写明

    detail = "；".join(f"{row['source']}：{row['state']}（{row['detail']}）" for row in attempted)
    failed = json.dumps({"outcome": "failed", "cause": "no_source_ran", "detail": detail},
                        ensure_ascii=False)
    print(failed)
    print(failed, file=sys.stderr)
    return 1


def _collecting_sources() -> tuple[str, ...]:
    from plobi.collectors import registry

    return registry.collecting_sources()


if __name__ == "__main__":
    raise SystemExit(main())
