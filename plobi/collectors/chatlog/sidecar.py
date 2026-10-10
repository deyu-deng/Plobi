"""chatlog sidecar 现在**缺哪一步**——四种状态各给一句不同的话（WP-SOCIAL-SOURCES-PLUGGABLE 判据 8）。

存在的理由只有一个：这台机器上"采不到"有四种完全不同的原因，而产品面以前把它们糊成
"一切正常只是没数据"。顺序按**依赖链**判（Go → 产物 → 密钥 → 在听），第一个缺的就是该说的那句。

不在这里放安装串：那条链已经在用户看得见的地方（桌面启动台账 / 托盘那一行的 enableHint，
见 `apps/desktop/electron/deferred-sidecars.ts` 的 chatlog 定义）。**同一句话不在两处真源**
（裁定 94），本模块只回答"现在缺哪一步"。

⚠️ 两处已知的第二真源，留给收口时合并（本模块不假装它们不存在）：
- 产物路径：桌面侧 `apps/desktop/electron/main.ts:8419-8420` 另外硬编了两个候选（含
  ``D:\\Tools\\wechat\\chatlog\\chatlog.exe``），与本模块的"env 优先 + 仓内默认"不是同一份解析。
- 密钥：桌面侧 `main.ts:8449` 会去读 ``~/.chatlog/chatlog.json``，而这里只看环境变量。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Mapping, Optional

__all__ = ["STATE_READY", "STATE_GO_MISSING", "STATE_BUILD_MISSING",
           "STATE_KEY_MISSING", "STATE_NOT_LISTENING", "diagnose", "note_for"]

STATE_GO_MISSING = "go_missing"
STATE_BUILD_MISSING = "build_missing"
STATE_KEY_MISSING = "key_missing"
STATE_NOT_LISTENING = "not_listening"
STATE_READY = "ready"

#: 一句话只描述"缺的是哪一步"，安装怎么做在台账那一行的提示里。
_NOTES = {
    STATE_GO_MISSING: "构建工具 Go 没装：chatlog 采集器还没法从源码建出来（装 Go 属软件安装，要本人点头）。",
    STATE_BUILD_MISSING: "没有 chatlog 可执行产物：已装 Go 的话还差 `go build` 那一步（或把路径写进 PLOBI_CHATLOG_BIN）。",
    STATE_KEY_MISSING: "微信数据密钥没设：产物在也解不开库，采不到东西。",
    STATE_NOT_LISTENING: "5030 上没有 chatlog 服务：前三步都齐了，但它没在跑。",
    STATE_READY: "采集器活着。",
}

BIN_ENV = "PLOBI_CHATLOG_BIN"
KEY_ENVS = ("CHATLOG_DATA_KEY", "CHATLOG_IMG_KEY")


def note_for(state: str) -> str:
    return _NOTES.get(state, f"没记到的采集器状态 {state!r}：不去猜它属于哪一种。")


def binary_candidates(env: Mapping[str, str] | None = None, repo_root: Path | None = None) -> list[str]:
    """产物候选：**环境变量优先**，其次仓内默认构建落点（不猜盘符）。"""
    env = os.environ if env is None else env
    explicit = (env.get(BIN_ENV) or "").strip()
    if explicit:
        return [explicit]
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[3]
    return [str(root / "tools" / "chatlog" / "bin" / "chatlog.exe"),
            str(root / "tools" / "chatlog" / "bin" / "chatlog")]


def diagnose(
    *,
    env: Mapping[str, str] | None = None,
    healthy: Optional[bool] = None,
    repo_root: Path | None = None,
) -> tuple[str, str]:
    """(状态, 一句话)。``healthy=None`` 时不去探端口（只判前置三步）。"""
    env = os.environ if env is None else env

    if healthy is True:
        # 5030 都探得活，就不要再回头说"你 Go 没装"——那是自相矛盾的一句话。
        return STATE_READY, note_for(STATE_READY)

    if not shutil.which((env.get("GO_BINARY") or "go"), path=env.get("PATH")):
        return STATE_GO_MISSING, note_for(STATE_GO_MISSING)

    if not any(Path(c).is_file() for c in binary_candidates(env, repo_root)):
        return STATE_BUILD_MISSING, note_for(STATE_BUILD_MISSING)

    if not all((env.get(name) or "").strip() for name in KEY_ENVS):
        return STATE_KEY_MISSING, note_for(STATE_KEY_MISSING)   # 只看有没有设，绝不读值

    if healthy is False:
        return STATE_NOT_LISTENING, note_for(STATE_NOT_LISTENING)
    return STATE_NOT_LISTENING, "还没探过 5030：前置三步齐了，但采集器活着没活着没验过。"
