"""cron no_agent 脚本：采集巡检 + chatlog 健康看门狗（切片 A2）。

由 ``scripts/plobi/register_cron.py`` 复制到 ``HERMES_HOME/scripts/`` 并注册为
10 分钟 ``no_agent`` 任务。stdout（JSON 摘要）即任务产出，进 cron 输出留痕。

引导规则：优先信任当前解释器环境（开发 venv 的 editable 安装可直接
``import plobi``）；失败则按 ``HERMES_HOME/plobi/runtime.json`` 的
``code_root`` 定位仓库。不写死盘符。
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
    from plobi.collectors.chatlog.watchdog import Watchdog

    summary = Watchdog().tick()
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
