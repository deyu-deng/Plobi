"""`WP-WECHAT-KEY-LOCATOR` 那一格的复跑脚本：sidecar 解不解得开真库，只看会话条数。

为什么是一支脚本而不是一行 `python -c`：`Docs/scripts/verify_register_cells.py` 的只读白名单
**不跑内联代码**（`python -c` / `node -e` / `bash -c` 一律 SKIP，"它算不算只读要看代码体，脚本判不了"）。
裁 174 的口径是"判据要写成一整条能跑的命令"，所以这条判据的落点就是这里，登记格里只放调用行。

只读：不发补全、不写盘、不打印任何密钥或会话正文——读数只有一个布尔与一个条数。
退出码 0 = 那条腿解得开真库（判据①②成立），1 = 解不开（面上该说真原因，不是"0 会话"）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from plobi.collectors.chatlog.client import ChatlogClient  # noqa: E402
from plobi.collectors.chatlog.config import CollectorConfig  # noqa: E402


def main() -> int:
    cfg = CollectorConfig.load()
    client = ChatlogClient(base_url=cfg.base_url)
    try:
        sessions = len(client.list_talker_sessions())
    except Exception as exc:  # 读不出来要说成读不出来，不算 0 会话
        print("unreadable: %s" % type(exc).__name__)
        return 1
    print("sessions=%d healthy=%s" % (sessions, client.healthy()))
    if sessions <= 0:
        print("判据不成立：枚举到 0 条会话 ⇒ 密钥解不开真库，或那条腿没在跑")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
