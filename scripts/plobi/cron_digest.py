"""cron no_agent 脚本：消息待办 digest（逐条待确认消息改动）→ 钉钉（切片 B5）。

由 ``scripts/plobi/register_butler.py`` 复制到 ``PLOBI_HOME/scripts/`` 并注册
为每日 12:00 任务。无待办时不推送。stdout 为 JSON 结果。

默认零 LLM（确定性模板）。设 ``PLOBI_BUTLER_POLISH=1`` 时走 L2 便宜模型改写。
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
    from plobi.butler.report import build_todo_digest, format_todo_digest
    from plobi.notify import get_notifier

    data = build_todo_digest()
    body = format_todo_digest(data)

    # 无待办 = 不打扰。
    if data["count"] == 0:
        print(json.dumps({"report": data, "delivered": False, "reason": "no todos"}, ensure_ascii=False))
        return 0

    if os.environ.get("PLOBI_BUTLER_POLISH", "").strip() in ("1", "true", "yes"):
        from plobi.butler.polish import polish

        polished = polish(body)
        if polished:
            body = polished

    notifier = get_notifier()
    sent = False
    if notifier.configured:
        outcome = notifier.send(body)
        sent = outcome.ok
        if not outcome.ok:
            print(json.dumps({"error": f"notify failed: {outcome.detail}"}, ensure_ascii=False))
    else:
        print(json.dumps({"warning": "no notifier configured; digest not delivered"}, ensure_ascii=False))

    print(json.dumps({"report": {"count": data["count"]}, "body": body, "delivered": sent}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
