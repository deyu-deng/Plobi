"""cron no_agent 脚本：每晚 20:00 用已有 events 写次日计划 → 钉钉。

由 ``scripts/plobi/register_butler.py`` 复制到 ``HERMES_HOME/scripts/`` 并注册
为每日 20:00 任务。stdout 为 JSON 结果，进 cron 输出留痕。

正文 = 计划段（原有输出，一字不改）+ 空行 + **次日可抄进手机日历的清单**
（WP-DT-DIGEST）。清单与计划共用同一个 ``agenda.db``：计划 ``pending``（等你批）
时只列已确认事件并在标题行写明「计划待批」，绝不把没批的东西当成已安排推给手机。

默认零 LLM。设 ``PLOBI_BUTLER_POLISH=1`` 时只把 ``summary`` 一段交给 L2
便宜模型润色，**永不**打 L1。空计划推送必须含「明天没有日程，计划为空」。
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


def main() -> int:
    from datetime import datetime

    from plobi.agenda.planning import format_evening_plan_text, generate_evening_plan
    from plobi.butler.report import build_day_digest, format_day_digest
    from plobi.notify import get_notifier

    plan = generate_evening_plan()
    summary = plan.summary

    if os.environ.get("PLOBI_BUTLER_POLISH", "").strip() in ("1", "true", "yes"):
        from plobi.butler.polish import polish

        polished = polish(summary)
        if polished:
            summary = polished

    body = format_evening_plan_text(plan, summary=summary)

    # 明天那份清单（计划待批则只列事件 + 标题行提示）。
    now = datetime.now()
    digest = format_day_digest(build_day_digest(for_date=plan.for_date, now=now))
    body = f"{body}\n\n{digest}"

    notifier = get_notifier()
    sent = False
    if notifier.configured:
        outcome = notifier.send(body)
        sent = outcome.ok
        if not outcome.ok:
            print(json.dumps({"error": f"notify failed: {outcome.detail}"}, ensure_ascii=False))
    else:
        print(
            json.dumps(
                {"warning": "no notifier configured; evening plan not delivered"},
                ensure_ascii=False,
            )
        )

    print(
        json.dumps(
            {"plan": plan.to_dict(), "body": body, "delivered": sent},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
