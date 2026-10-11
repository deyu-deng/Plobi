"""cron no_agent 脚本：每晚 20:00 用已有 events 写次日计划 → 钉钉。

表与注册在 ``plobi/butler/nightly.py``（产品真源）：装机后第一次启动由桌面后端种一次
``plobi-evening-plan``，手动全量注册走 ``scripts/plobi/register_cron.py --all``。
stdout 为 JSON 结果，进 cron 输出留痕。

**跑失败要说得出为什么**（WP-SCHEDULE-REAL-PLAN 判据 1）：读库那一段按类分格——
库被占（``db_locked``）与任何其它异常（``exception``）都打一条具名 JSON 到
stdout **和** stderr，再以非零退出；``cron/scheduler.py`` 的 ``no_agent`` 分支把非零退出
当成告警正文推给用户，所以这里绝不留 traceback-only 的失败。「明天没东西可排」
**不是**失败：那条走 ``plan.status == "empty"``，正文里已有「明天没有日程，计划为空，
不是没跑」这句，退出码 0。

正文 = 计划段（原有输出，一字不改）+ 空行 + **次日可抄进手机日历的清单**
（WP-DT-DIGEST）。清单与计划共用同一个 ``agenda.db``：计划 ``pending``（等你批）
时只列已确认事件并在标题行写明「计划待批」，绝不把没批的东西当成已安排推给手机。

默认零 LLM。设 ``PLOBI_BUTLER_POLISH=1`` 时只把 ``summary`` 一段交给 L2
便宜模型润色，**永不**打 L1。空计划推送必须含「明天没有日程，计划为空」。
"""

from __future__ import annotations

import json
import os
import sqlite3
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


def _fail(cause: str, detail: str) -> int:
    """具名失败：同一句既进 stdout（cron 的告警正文取它）也进 stderr（留痕取它）。"""
    payload = json.dumps({"outcome": "failed", "cause": cause, "detail": detail}, ensure_ascii=False)
    print(payload)
    print(payload, file=sys.stderr)
    return 1


def _classify(exc: BaseException) -> tuple[str, str]:
    detail = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, sqlite3.OperationalError) and ("locked" in str(exc).lower() or "busy" in str(exc).lower()):
        return "db_locked", detail
    return "exception", detail


def main() -> int:
    from datetime import datetime

    from plobi.agenda.planning import format_evening_plan_text, generate_evening_plan
    from plobi.butler.report import build_day_digest, format_day_digest

    try:
        plan = generate_evening_plan()
    except Exception as exc:  # noqa: BLE001 - 库锁 / 未知异常都要说得出是哪一类，不许只留 traceback
        cause, detail = _classify(exc)
        return _fail(cause, detail)

    summary = plan.summary

    if os.environ.get("PLOBI_BUTLER_POLISH", "").strip() in ("1", "true", "yes"):
        from plobi.butler.polish import polish

        polished = polish(summary)
        if polished:
            summary = polished

    body = format_evening_plan_text(plan, summary=summary)

    # 明天那份清单（计划待批则只列事件 + 标题行提示）。
    now = datetime.now()
    try:
        digest = format_day_digest(build_day_digest(for_date=plan.for_date, now=now))
    except Exception as exc:  # noqa: BLE001 - 同上：清单读不到也要说得出
        cause, detail = _classify(exc)
        return _fail(cause, detail)
    body = f"{body}\n\n{digest}"

    from plobi.notify import get_notifier

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
