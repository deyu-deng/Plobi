"""cron 脚本：每晚 20:00 **读**次日计划并投递（钉钉 + 手机可抄的清单）。

**本脚本零模型**（裁定 163，用户 10-16 22:5x：「不要用脚本排程。让日程秘书自己排一次明天的
计划。要落到真实的 entry 里面。」）：脚本退成"读库 + 格式化 + 投递"，一个模型都不发。以前那句
「排程器由脚本显式注入」连同它的生产接线一起撤下——今晚那两笔钱（20:02 秘书 13182 tokens、
20:07 脚本 2886 tokens/0.54 credit）换回一份 ``block_count=0`` 的空计划，就是这条形状错误的账单。

排程那一发的**主人是日程秘书那个 L2 本人**（裁定 165 改判 163①，②③ 不动）：落库口在
``plobi/agenda/llm_planning.py:plan_and_store_day``（材料→提案→边界闸→写），嘴上那个入口是
``plan_tomorrow``。**照实说现状**：那条作业 ``plobi-evening-plan`` 的 prompt 现读仍写着
「不要自己重排时间」，``l2-agenda`` 的工具集只有 ``terminal``——把 prompt 改成「你来排」并给它
装上手，登记在 ``WP-AGENDA-PLANS-ITSELF``（排在「详情列 → 修超时」之后）。那一把刀落地之前，
夜里这一轮**还是不会自己排**，本脚本也**不代排**：明天没有计划就照实说没有。

表与注册在 ``plobi/butler/nightly.py``（产品真源）：装机后第一次启动由桌面后端种一次
``plobi-evening-plan``，手动全量注册走 ``scripts/plobi/register_cron.py --all``。
stdout 为 JSON 结果，进 cron 输出留痕。

**跑失败要说得出为什么**（WP-SCHEDULE-REAL-PLAN 判据 1）：读库那一段按类分格——
库被占（``db_locked``）与任何其它异常（``exception``）都打一条具名 JSON 到
stdout **和** stderr，再以非零退出；``cron/scheduler.py`` 的 ``no_agent`` 分支把非零退出
当成告警正文推给用户，所以这里绝不留 traceback-only 的失败。

**「明天还没有计划」也不是崩**（``outcome="no_plan"``，退出码 0）：那是秘书那一轮还没落库，
照实说一句人话，**不许**由脚本顺手补生成一份——``generate_evening_plan`` 会先 DELETE 当天那些
``plan_items`` 再重插，秘书已经排好的块会被这一手抹掉。真有 ``status=empty`` 那份计划时，
正文里仍是既有的「明天没有日程，计划为空，不是没跑」那句。

正文 = 计划段（既有输出）+ 空行 + **次日可抄进手机日历的清单**（WP-DT-DIGEST）。清单与计划
共用同一个 ``agenda.db``：计划 ``pending``（等你批）时只列已确认事件并在标题行写明「计划待批」，
绝不把没批的东西当成已安排推给手机。

润色是独立的一道门：只有设 ``PLOBI_BUTLER_POLISH=1`` 才把 ``summary`` 一段交给 L2 便宜模型，
**永不**打 L1 润色；门没开时本脚本一次模型都不调（探针计数 0）。空计划推送必须含
「明天没有日程，计划为空」。
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


def _no_plan(day: str) -> int:
    """明天还没有计划：照实说一句，退出 0——这不是崩，也不许由脚本补一份。

    为什么宁可不说"失败"也不生成：``generate_evening_plan`` 会先 DELETE 当天那些 ``plan_items``
    再重插，脚本一插手就把秘书已经排好的块抹掉（裁定 163 撤脚本排程的正解）。
    """
    text = f"明天（{day}）那份计划还没有——日程秘书那一轮没落库，脚本不代排。"
    print(json.dumps({"outcome": "no_plan", "for_date": day, "summary": text}, ensure_ascii=False))
    notifier = get_notifier_safe()
    if notifier is not None and notifier.configured:
        outcome = notifier.send(text)
        if not outcome.ok:
            print(json.dumps({"error": f"notify failed: {outcome.detail}"}, ensure_ascii=False))
    return 0


def get_notifier_safe():
    from plobi.notify import get_notifier

    return get_notifier()


def main() -> int:
    from datetime import datetime

    from plobi.agenda import store
    # 「明天」只留一处定义：与排程用的同一个解析（planning 内部那份），不在脚本里再算一遍。
    from plobi.agenda.planning import _as_for_date, format_evening_plan_text
    from plobi.butler.report import build_day_digest, format_day_digest

    day = _as_for_date(None)
    try:
        # 裁定 163：脚本**只读**——排程与落库都在秘书那一轮里，本脚本零模型。
        conn = store.connect(None)
        try:
            plan = store.get_daily_plan(conn, day)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - 库锁 / 未知异常都要说得出是哪一类，不许只留 traceback
        cause, detail = _classify(exc)
        return _fail(cause, detail)

    if plan is None:
        return _no_plan(day)

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

    notifier = get_notifier_safe()
    sent = False
    if notifier is not None and notifier.configured:
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
