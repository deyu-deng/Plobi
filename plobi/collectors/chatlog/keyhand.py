"""替他把那把"记不住、也不该由他抄"的微信数据密钥取回来（裁定 203 口径 2）。

`chatlog key` 是 vendored 那份产物自带的子命令：它在这台机器上读**他自己登录的**微信进程，
把本地库的解密密钥找出来。这一档不发明新机制，只是把那条子命令接到产品里，并把结果写进
同一处真源（引擎自己那一档 handoff 配置），从此屏幕上不再要求他复制一串十六进制。

三条硬规矩，缺一条就不该上线：

1. **值永不出门**：不进 stdout、不进日志、不进异常文本，也不进本函数的返回值——
   外面只拿得到"取到没取到 + 那一句人话"。
2. **取不到就说是为什么**（没产物／那版不支持这个子命令／微信没在跑／它自己报错），
   不许退化成"那你去填一格"——那是把自动化的失败转嫁成他的步骤。
3. **只在被那一次动作带着走的时候做**：他点开微信的开关那一刻（`SourceSpec.sidecar_prepare`）。
   读数、列表、状态检查里都不碰——列一屏信源不该顺手去读任何进程。
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Mapping, Optional

from .sidecar import DATA_KEY_ENV, data_key_present, engine_config_dir, resolved_binary

logger = logging.getLogger(__name__)

#: 密钥的形状：32 个十六进制字符。抓取时**只判形状，不把值带出去**。
_KEY_SHAPE = re.compile(r"^\s*(?:[A-Za-z_ ]{0,12}[:=]\s*)?([0-9a-fA-F]{32})\s*$")
_TIMEOUT_SECONDS = 180.0


def _find_key(text: str) -> Optional[str]:
    for line in text.splitlines():
        m = _KEY_SHAPE.match(line)
        if m:
            return m.group(1)
    return None


def _write_handoff(directory: Path, key: str) -> None:
    """并进引擎那一档配置（已有别的键就留着），写完收到只有本用户可读。"""
    target = directory / "chatlog.json"
    merged: dict[str, object] = {}
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            merged = loaded
    except (OSError, ValueError):
        pass

    merged["data_key"] = key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(target, 0o600)
    except OSError:                                  # 有的文件系统不支持 chmod：值已经在该在的地方
        logger.debug("chatlog key handoff chmod skipped: %s", target)


def fetch(env: Optional[Mapping[str, str]] = None, *,
          runner=subprocess.run, timeout: float = _TIMEOUT_SECONDS) -> tuple[bool, str]:
    """试着替他取一次数据密钥。回 ``(取到没有, 那一句人话)``——**那句话里永远没有密钥**。"""
    env = os.environ if env is None else env

    if data_key_present(env):
        return True, "解密密钥本机已经有了，这次没去动微信。"

    binary = resolved_binary(env)
    if not binary:
        return False, "本机还没有微信采集器的可执行产物，密钥取不了——缺的是那一次构建。"

    try:
        proc = runner([str(binary), "key"], capture_output=True, text=True,
                      timeout=timeout, env=dict(env))
    except subprocess.TimeoutExpired:
        return False, "取密钥这一步超时没回来（微信通常要正在运行）。这次没拿到，也没有别的办法要你做。"
    except OSError as exc:
        return False, f"取密钥的命令跑不起来：{type(exc).__name__}。"

    stdout = getattr(proc, "stdout", "") or ""
    stderr = getattr(proc, "stderr", "") or ""
    key = _find_key(stdout) or _find_key(stderr)

    if not key:
        reason = ""
        for line in (stdout + "\n" + stderr).splitlines():
            stripped = line.strip()
            # 那一边报的原文里可能带路径/账号，只取不含这些形状的一行当线索
            if stripped and not re.search(r"[A-Za-z]:\\|@|password|key", stripped, re.I):
                reason = stripped[:60]
                break
        return False, ("这次没从微信里取到密钥（它通常要正在运行的微信）。"
                       + (f"它自己那句：{reason}" if reason else ""))

    try:
        _write_handoff(engine_config_dir(env), key)
    except OSError as exc:
        return False, f"密钥取到了，但写不进本机那一份配置：{type(exc).__name__}。"

    return True, "已经替你取到密钥并写进本机那一份配置，点开关就解得开了。"
