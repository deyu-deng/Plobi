"""cron no_agent 脚本：连续 7 天改动率日报 → 钉钉（切片 A5）。

由 ``scripts/plobi/register_cron.py --all`` 复制到 ``PLOBI_HOME/scripts/`` 并
注册为每日 21:00 任务。stdout 为 JSON 结果，进 cron 输出留痕。

引导规则与 plobi_watchdog.py 相同：优先 editable 安装，失败则按
``PLOBI_HOME/plobi/runtime.json`` 的 ``code_root`` 定位仓库。
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


def main() -> int:
    from plobi.agenda.stats import change_report, format_report
    from plobi.notify import get_notifier

    report = change_report()
    body = format_report(report)

    notifier = get_notifier()
    sent = False
    if notifier.configured:
        outcome = notifier.send(body)
        sent = outcome.ok
        if not outcome.ok:
            print(json.dumps({"error": f"notify failed: {outcome.detail}"}, ensure_ascii=False))
    else:
        print(json.dumps({"warning": "no notifier configured; report not delivered"}, ensure_ascii=False))

    print(json.dumps({"report": report, "body": body, "delivered": sent}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
