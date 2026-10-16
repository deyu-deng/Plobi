"""裁定 101 四 / 裁定 94 —— 代理地址不许写进脚本，代理读法只许一条（10-16 网络线代收判据）。

两条都是**棘轮**，不是愿望：

* ① 脚本（`.ps1`/`.sh`/`.cmd`/`.bat`/`.vbs`）里不许出现被赋值的代理地址字面量。这条今天真的成立
  （全仓 1828 个跟踪源文件扫下来 0 命中），所以它是能挡住下一次「先写死再说」的牙。
* ② 读 `*_PROXY` 环境变量的模块**今天还有三份重复**（下面 `_DUPLICATE_READERS` 逐条点名，含各
  自服务的是哪条腿）。这一条不是"已经合规"，是**把欠账钉成一个集合**：多出来第四份就红。
  正解一律是 `agent.process_bootstrap._get_proxy_for_base_url` —— 只有它先看 `network.egress`
  表、再退环境变量，也只有它尊重表里的 `NO_PROXY` 展开。重复那三份只读 env，于是**表配了它们不认**
  （与桌面昨天那件「配了不生效」同族）。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

_SKIP_PARTS = {"node_modules", ".venv", "venv", "dist", "release", "build", "__pycache__", ".git"}
_TEST_EXT = (".ps1", ".sh", ".cmd", ".bat", ".vbs")

#: 被赋值成字面量的代理地址——含 `KEY=value`、`KEY = "value"`、PowerShell `$env:X = "..."` 与
#: Chromium 的 `--proxy-server=...`。允许出现的是变量/占位符，不是 `127.0.0.1:7890` 这种实际地址。
_ADDRESS = r"(?:127\.0\.0\.1|0\.0\.0\.0|localhost|\d{1,3}(?:\.\d{1,3}){3})(?::\d{2,5})?"
LITERAL_PROXY = re.compile(
    r"""(?:--proxy-server=|(?:HTTP|HTTPS|ALL|WS|WSS)_?PROXY(?:_URL)?\s*[:=]\s*"""
    r"""["']?\s*(?:https?://|socks5?://)?[_a-z0-9.-]*)(""" + _ADDRESS + r""")""",
    re.IGNORECASE,
)

_ENV_NAMES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
              "http_proxy", "https_proxy", "all_proxy", "no_proxy")
READS_ENV_PROXY = re.compile(r"""["'](?:%s)["']""" % "|".join(_ENV_NAMES), re.IGNORECASE)

#: 被认可碰这些名字的四处：读法本体 + 按表**写**子进程环境的投影器 + 规范化 + 外包那一条腿
#: （它只把自己刚解析出来的值写进子 env，并读回那一份，不自己按名字猜出口）。
_SANCTIONED = {
    "agent/process_bootstrap.py",   # 唯一的解析器：先看 network.egress 表，再退 env，尊重 NO_PROXY
    "agent/egress/env_projection.py",  # 出口选路的子进程 env 投影器（按表写）
    "agent/external_offload.py",    # 外包子进程的 env 组装（用已解析的值，不再判一次）
    "utils.py",                     # normalize_proxy_url / normalize_proxy_env_vars（规范化，不是判往哪走）
}

#: 今天的重复读法（棘轮的起点，不是许可名单）。每一条都只读 env、不看 `network.egress` 表，
#: 所以表接管了它们也不认——这正是 10-16 那份审计要钉出来的东西。
_DUPLICATE_READERS = {
    "agent/auxiliary_client.py",           # 辅助 LLM 调用那一条腿
    "gateway/platforms/base.py",           # 消息平台 HTTP 客户端（还自带一份 NO_PROXY 解析）
    "gateway/platforms/qqbot/adapter.py",  # QQ 机器人 WebSocket（另造了一个 WSS_PROXY 名字）
}


def _tracked_sources(suffixes: tuple[str, ...]) -> list[Path]:
    """仓库里"随包走"的那批源文件——剪掉产物与依赖目录，别去读 node_modules 里锁住的东西。"""
    found = []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in _SKIP_PARTS and not d.startswith(".")]
        for name in files:
            if not name.lower().endswith(suffixes):
                continue
            path = Path(root) / name
            rel = path.relative_to(REPO).as_posix()
            if rel.startswith("tests/") or ".test." in rel:
                continue
            found.append(path)
    return found


def test_no_proxy_address_is_written_into_a_shipped_script():
    """脚本只许把代理地址**转手**（环境变量 / 表里读出来的值），不许自己写一个地址。"""
    offenders = []

    for path in _tracked_sources(_TEST_EXT):
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in LITERAL_PROXY.finditer(text):
            line = text[:match.start()].count("\n") + 1
            offenders.append("%s:%d -> %s" % (path.relative_to(REPO).as_posix(), line, match.group(0).strip()[:70]))

    assert not offenders, (
        "裁定 101 四（含 48.1）：代理地址一律走环境变量/表，不许写进脚本。命中：\n  " + "\n  ".join(offenders)
    )


def test_the_proxy_env_readers_are_exactly_the_recorded_set():
    """谁在按名字碰 `*_PROXY` 必须点名在册；重复读法今天三份，第四份不许再冒出来。"""
    sanctioned, duplicates, unexpected = set(), set(), set()

    for path in _tracked_sources((".py",)):
        rel = path.relative_to(REPO).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        if not READS_ENV_PROXY.search(text):
            continue
        if rel in _SANCTIONED:
            sanctioned.add(rel)
        elif rel in _DUPLICATE_READERS:
            duplicates.add(rel)
        else:
            unexpected.add(rel)

    assert not unexpected, (
        "又多一处自己按名字读代理环境变量的地方：" + ", ".join(sorted(unexpected))
        + "。正解是 `agent.process_bootstrap._get_proxy_for_base_url`——只有它先看 network.egress 表、"
          "再退 env，也只有它尊重表里的 NO_PROXY 展开（裁定 94：一处读数；裁定 101 判据①：不许再造第二条）。"
    )
    converged = sorted(_DUPLICATE_READERS - duplicates)
    assert not converged, (
        "这几条已经并回单一读法了，把 `_DUPLICATE_READERS` 里的对应行删掉再签（欠账不许留着当装饰）："
        + ", ".join(converged)
    )
    assert len(sanctioned) == len(_SANCTIONED), (sanctioned, _SANCTIONED)
