"""cron no_agent 脚本：每晚 21:30 秘书回访（C6）——确定性选题 → L2 起草 → 卡+推送。

由 ``scripts/plobi/register_butler.py`` 复制到 ``HERMES_HOME/scripts/`` 并注册
为每日 21:30 任务。stdout 为 JSON 结果，进 cron 输出留痕。

选题零 LLM（events diff / 计划状态 / pace 项目推进 / 改动率）；起草走 L2
便宜模型（与早报润色同级路由，永不打 L1），失败回落确定性模板问题。
降频护栏：连续 3 天被忽略 → 自动降为每周一次。
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
    from plobi.agenda.checkin import build_checkin, format_checkin_text
    from plobi.notify import get_notifier

    result = build_checkin()
    card = result.get("card")

    if card is None:
        print(
            json.dumps(
                {"checkin": result, "delivered": False, "reason": result.get("skipped_reason")},
                ensure_ascii=False,
            )
        )
        return 0

    body = format_checkin_text(card)
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
                {"warning": "no notifier configured; check-in not delivered"},
                ensure_ascii=False,
            )
        )

    print(json.dumps({"checkin": result, "body": body, "delivered": sent}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
