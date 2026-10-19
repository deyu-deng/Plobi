"""chatlog sidecar 现在**缺哪一步**——四种状态各给一句不同的话（WP-SOCIAL-SOURCES-PLUGGABLE 判据 8）。

存在的理由只有一个：这台机器上"采不到"有四种完全不同的原因，而产品面以前把它们糊成
"一切正常只是没数据"。顺序按**依赖链**判，第一个缺的就是该说的那句——
**产物 → 密钥 → 在听**，只有**没产物**时才问构建工具 Go（10-11 修正：Go 按落点纪律
装在 `D:/Runtimes/Languages/Go` 且刻意不进 PATH，产物早已建好，先问 Go 就等于永远怪错人）。

不在这里放安装串：那条链已经在用户看得见的地方（桌面启动台账 / 托盘那一行的 enableHint，
见 `apps/desktop/electron/deferred-sidecars.ts` 的 chatlog 定义）。**同一句话不在两处真源**
（裁定 94），本模块只回答"现在缺哪一步"。

三个 `chatlog.json` 各归谁（10-11 现核，别再笼统说"配置为空"）：
1. ``$PLOBI_HOME/plobi/chatlog.json`` = **采集器的配置真源**，由 :func:`config.config_path()` 解析，
   里面是 ``mode/enabled/talkers/poll_minutes``；这台现核**不空**（148 B，10-10 14:18，
   ``mode=blacklist`` / ``enabled=true`` / ``poll=10``）。本模块不读它。
2. ``$PLOBI_HOME/chatlog/chatlog.json`` = **chatlog 引擎自己的**配置，桌面把 `CHATLOG_DIR` 钉到这里
   （`apps/desktop/electron/chatlog-keys.ts`）；这台现核是空 ``{}``（那次取密钥没成，见下面第 4 点）。
   引擎写的 ``chatlog-server.json`` 也在这一档，密钥只落这一份。
3. ``~/.chatlog/chatlog.json`` = 上游的默认落点，也就是 C 盘那份。这台现核**不存在**，
   而且**不该存在**——运行数据禁残留 C 盘（`D:\\_POLICY`），桌面那条钉 `CHATLOG_DIR` 就是为这件事。
4. 桌面侧那份"第二真源"（旧 `main.ts` 硬编产物候选 + 自己读 ``~/.chatlog`` 判密钥）已经合并掉：
   产物路径只读本模块 :func:`resolved_binary` 那一格（`WP-SIDECAR-STATE-SINGLE-SOURCE`，`e5ce5ba8`），
   密钥只走上面第 2 条那一份文件（`7ee37708`）。本模块判"密钥有没有"时**读的就是那一份文件**
   （:func:`data_key_present`，只看有没有、绝不读值），环境变量只是它的另一张脸——
   以前只看环境变量，于是用户手工登进配置的密钥在面上说"没设"。
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Mapping, Optional

__all__ = ["STATE_READY", "STATE_GO_MISSING", "STATE_BUILD_MISSING",
           "STATE_KEY_MISSING", "STATE_NOT_LISTENING", "data_key_present",
           "diagnose", "engine_config_dir", "note_for"]

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
DATA_KEY_ENV = "CHATLOG_DATA_KEY"
IMG_KEY_ENV = "CHATLOG_IMG_KEY"
#: 引擎那一档的两个文件名——桌面 `chatlog-keys.ts` 里是同一对常量，别各写一份。
ENGINE_CONFIG_FILES = ("chatlog-server.json", "chatlog.json")
#: vendored Go 工程与它的构建落点都在这条腿自己家上（裁 187 归位）。
VENDOR_DIR = Path("vendor") / "chatlog"


def engine_config_dir(env: Mapping[str, str]) -> Path:
    """chatlog 引擎自己读的那一档配置目录（密钥的真源落点）。"""
    explicit = (env.get("CHATLOG_DIR") or "").strip()
    if explicit:
        return Path(explicit)
    from plobi_constants import get_plobi_home

    return get_plobi_home() / "chatlog"


def data_key_present(env: Mapping[str, str]) -> bool:
    """`data_key` 到底有没有——**只看有没有，绝不读值、绝不外传长度之外的东西**。

    环境变量不是真源：引擎起服务时读的是 `$CHATLOG_DIR/chatlog*.json` 里那一份
    （桌面 `chatlog-keys.ts::serverConfigReady` 判的就是它），用户手工登进来的值
    也落在那儿。以前这里只看环境变量，于是"手工填了密钥，面上仍说没设"——
    那句话把唯一还活着的那条路也判死了。
    ``img_key`` 不参与判断：`manager.go:323` 硬性要的只有 dataKey，
    图片是**能力差异**（声明式能力位），不是"缺一步"。
    """
    if (env.get(DATA_KEY_ENV) or "").strip():
        return True
    directory = engine_config_dir(env)
    for name in ENGINE_CONFIG_FILES:
        try:
            loaded = json.loads((directory / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(loaded, dict) and str(loaded.get("data_key") or "").strip():
            return True
    return False


def note_for(state: str) -> str:
    return _NOTES.get(state, f"没记到的采集器状态 {state!r}：不去猜它属于哪一种。")


def binary_candidates(env: Mapping[str, str] | None = None, repo_root: Path | None = None) -> list[str]:
    """产物候选：**环境变量优先**，其次这一腿自己家上的 vendor 构建落点（不猜盘符）。

    裁 187 之前这套 vendored 工程住在仓根的 Agent 工具命名空间下，微信这一条腿因此被劈成
    两半。归位之后**产物落点只有这一处拼法**：桌面与启动器都读同一个解析，不许再各写一份。
    """
    env = os.environ if env is None else env
    explicit = (env.get(BIN_ENV) or "").strip()
    if explicit:
        return [explicit]
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parent
    bin_dir = root / VENDOR_DIR / "bin"
    return [str(bin_dir / "chatlog.exe"), str(bin_dir / "chatlog")]


def resolved_binary(
    env: Mapping[str, str] | None = None,
    repo_root: Path | None = None,
) -> Optional[str]:
    """当前真实存在的产物路径，没有就 `None`。

    存在的理由只有一个（`WP-SIDECAR-STATE-SINGLE-SOURCE` 判据 1）：桌面要 spawn 时读**这一个**答案，
    不许在 `main.ts` 里再列一份候选——那份候选就是第二条真源。
    """
    return next((c for c in binary_candidates(env, repo_root) if Path(c).is_file()), None)


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

    has_binary = any(Path(c).is_file() for c in binary_candidates(env, repo_root))

    # 有产物就不提 Go：Go 只是"建不出产物"的原因，产物都在了还怪 PATH 上没 go，
    # 说出来的那句"为什么采不到"就是假的（10-11 早上现核到：Go 装在
    # D:\Runtimes\Languages\Go 且**刻意不进 PATH**，产物已由 WP-CHATLOG-SIDECAR-BUILD 建好，
    # 而面上仍然报 go_missing）。没产物时才问构建工具在不在——`GO_BINARY` 可以指绝对路径。
    if not has_binary and not shutil.which((env.get("GO_BINARY") or "go"), path=env.get("PATH")):
        return STATE_GO_MISSING, note_for(STATE_GO_MISSING)

    if not has_binary:
        return STATE_BUILD_MISSING, note_for(STATE_BUILD_MISSING)

    if not data_key_present(env):
        return STATE_KEY_MISSING, note_for(STATE_KEY_MISSING)   # 只看有没有，绝不读值

    if healthy is False:
        return STATE_NOT_LISTENING, note_for(STATE_NOT_LISTENING)
    return STATE_NOT_LISTENING, "还没探过 5030：前置三步齐了，但采集器活着没活着没验过。"
