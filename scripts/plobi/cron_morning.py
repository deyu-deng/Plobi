"""cron no_agent 脚本：每日早报（当天清单 + 改动率/待批项/额度）→ 钉钉。

由 ``scripts/plobi/register_butler.py`` 复制到 ``HERMES_HOME/scripts/`` 并注册
为每日 07:30 任务。stdout 为 JSON 结果，进 cron 输出留痕。

正文 = **当天可抄进手机日历的清单**（``format_day_digest``，清单在前）+ 空行 +
统计段。组装走 :func:`plobi.butler.report.morning_body`，与看门狗的开机补发
共用同一个函数。

默认零 LLM（确定性模板）。设 ``PLOBI_BUTLER_POLISH=1`` 时走 L2 便宜模型改写
**统计段**（B3 额度池），清单钟点永不经过模型；L1 始终只转发。

幂等：``HERMES_HOME/plobi/digest_state.json`` 已记今天发过就不再发——看门狗
的 07:30 补发可能抢在正点之前，两边共用一个标记保证手机只收到一次。
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

    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    runtime = home / "plobi" / "runtime.json"
    try:
        code_root = json.loads(runtime.read_text(encoding="utf-8"))["code_root"]
        if code_root and code_root not in sys.path:
            sys.path.insert(0, str(code_root))
    except (OSError, json.JSONDecodeError, KeyError):
        print(json.dumps({"error": f"cannot locate code root via {runtime}"}))
        raise SystemExit(1)


_bootstrap()


def _polish_stats(text: str):
    from plobi.butler.polish import polish

    return polish(text)


def main() -> int:
    from datetime import datetime

    from plobi.butler.report import (
        build_morning,
        mark_morning_sent,
        morning_body,
        morning_sent_for,
    )
    from plobi.notify import get_notifier

    now = datetime.now()
    day = now.date()

    if morning_sent_for() == day.isoformat():
        print(
            json.dumps(
                {"skipped": f"morning digest already delivered for {day}", "delivered": False},
                ensure_ascii=False,
            )
        )
        return 0

    data = build_morning(now=now)
    transform = None
    if os.environ.get("PLOBI_BUTLER_POLISH", "").strip() in ("1", "true", "yes"):
        transform = _polish_stats

    body = morning_body(now, report=data, transform_stats=transform)

    notifier = get_notifier()
    sent = False
    if notifier.configured:
        outcome = notifier.send(body)
        sent = outcome.ok
        if sent:
            mark_morning_sent(day)
        else:
            print(json.dumps({"error": f"notify failed: {outcome.detail}"}, ensure_ascii=False))
    else:
        print(json.dumps({"warning": "no notifier configured; morning report not delivered"}, ensure_ascii=False))

    print(
        json.dumps(
            {"report": data, "body": body, "delivered": sent},
            ensure_ascii=False,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
