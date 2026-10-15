"""cron no_agent 脚本：每天一轮信源采集（用户 2026-10-15 13:0x 拍：新条目进待确认）。

执行方在 ``plobi/collectors/sweep.py``。它只问注册表两件事——这条点没点采集开关、
有没有构造得出它的入口——**不按信源名字分支**（判据 3），所以加一个新平台不需要动这个脚本。

读数口径与 ``cron_evening_plan.py`` 同一族：**具名失败，同一句既进 stdout（cron 的告警正文取它）
也进 stderr（留痕）**，不留 traceback-only 的失败。退出码的语义是这条链的真话：

- 至少有一条真跑了一轮 ⇒ 0（其余条目的失败照样逐条打在 JSON 里，不合并成"成功"）。
- **一条都没跑成 ⇒ 1**，正文就是那句 verdict 加每条自己的状态（教务掉登录、构造失败、
  注册表说在采却没有执行方——三种是不同的话，不许都写成"没数据"）。
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
    from plobi.collectors.sweep import run_sweep

    report = run_sweep()
    print(json.dumps(report, ensure_ascii=False))
    if report["ran"]:
        return 0

    detail = "；".join(f"{row['source']}：{row['state']}（{row['detail']}）" for row in report["sources"])
    payload = json.dumps({"outcome": "failed", "cause": "no_source_ran", "detail": detail}, ensure_ascii=False)
    print(payload)
    print(payload, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
