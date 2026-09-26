"""cron no_agent 脚本：额度预警（degraded/unavailable 源告警）→ 钉钉（切片 B5）。

由 ``scripts/plobi/register_butler.py`` 复制到 ``HERMES_HOME/scripts/`` 并注册
为每日 08:00 任务。所有源健康时不推送。消费 B3 ``QuotaPool.alert_statuses()``。
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
    from plobi.butler.report import build_quota_alert, format_quota_alert
    from plobi.notify import get_notifier

    data = build_quota_alert()
    body = format_quota_alert(data)

    # 无异常源 = 静默（预警只在降级/失效时打扰）。
    if not body:
        print(json.dumps({"report": data, "delivered": False, "reason": "all healthy"}, ensure_ascii=False))
        return 0

    notifier = get_notifier()
    sent = False
    if notifier.configured:
        outcome = notifier.send(body)
        sent = outcome.ok
        if not outcome.ok:
            print(json.dumps({"error": f"notify failed: {outcome.detail}"}, ensure_ascii=False))
    else:
        print(json.dumps({"warning": "no notifier configured; quota alert not delivered"}, ensure_ascii=False))

    print(json.dumps({"report": data, "body": body, "delivered": sent}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
