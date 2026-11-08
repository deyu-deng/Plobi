"""替他把那把"记不住、也不该由他抄"的微信数据密钥取回来（裁定 203 口径 2）。

`chatlog key` 是 vendored 那份产物自带的子命令：它在这台机器上读**他自己登录的**微信进程，
把本地库的解密密钥找出来。这一档不发明新机制，只是把那条子命令接到产品里，并把结果写进
同一处真源（引擎自己那一档 handoff 配置），从此屏幕上不再要求他复制一串十六进制。

**这一条现在只管老版本微信**：微信 4.0.3.36 起不再长时间把密钥留在内存里，上游那条
`chatlog key` 在新版微信上取不到（上游 FAQ #197）。新版那条走 :mod:`.keyscan`
（我们自己那条只读扫描），由 :func:`plobi.collectors.chatlog._prepare` 先试它、这一条兜底。

四条硬规矩，缺一条就不该上线：

1. **值永不出门**：不进 stdout、不进日志、不进异常文本，也不进本函数的返回值——
   外面只拿得到"取到没取到 + 那一句人话"。落盘也只落它该在的那一档配置（`0600`），
   所以两个流都走管道、**不写临时文件**（写了就是把密钥请进产物区）。
2. **取不到就说是为什么**（没产物／那版不支持这个子命令／微信没在跑／它自己报错），
   不许退化成"那你去填一格"——那是把自动化的失败转嫁成他的步骤。
3. **只在被那一次动作带着走的时候做**：他点开微信的开关那一刻（`SourceSpec.sidecar_prepare`）。
   读数、列表、状态检查里都不碰——列一屏信源不该顺手去读任何进程。
4. **不在屏幕上吊着他**：10-26 真机量到一次**超过三分钟没回来**的扫描（`wechat.go:67` 那趟
   内存扫描自己没有任何截止时间，慢是它的正常形状）。所以这里只等一小会儿，
   等不到就**让它继续在后台扫**——`subprocess.run(timeout=)` 会把子进程杀掉，杀掉就是白扫，
   那样这条自动路在这台机器上永远取不到。他自己扫完会写进同一处真源（`manager.go:266`
   的 `UpdateConfig`，目录由我们钉死的 `CHATLOG_DIR` 决定），下一次读状态就是活的。
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Mapping, Optional

from .sidecar import data_key_present, engine_config_dir, resolved_binary

logger = logging.getLogger(__name__)

#: 屏幕上最多替他等这几秒。不是"扫描超时"，是"这次点击的耐心上限"：
#: 超过就转成后台那一趟，并把这句话说明白（口径 4）。
_WAIT_SECONDS = 60.0
#: 密钥的形状：32 个十六进制字符。抓取时**只判形状，不把值带出去**。
#: 10-26 对着 vendored 那份 `manager.go:269` 现核到成功时的原文是
#: `Data Key: [<32hex>]\nImage Key: [<32hex>]`——**带标签、套方括号**。
#: 原先只认"整行就是 32 个十六进制"，于是真取到了也匹配不上、屏幕上说"没取到"：
#: 那是口径 2 反过来咬我们一口（把系统的失败说成他的等待）。所以两种形状都认，
#: 但只取 `Data Key` 那一串（`manager.go:323` 硬性要的只有 dataKey，图片是能力差异）。
_KEY_LABELLED = re.compile(r"data[ _]?key[^0-9a-f\n]{0,8}([0-9a-fA-F]{32})", re.I)
_KEY_BARE = re.compile(r"^\s*\[?([0-9a-fA-F]{32})\]?\s*$")
#: 那件产物打的是带颜色的日志（10-26 真机现核，而且它**失败也回 0**）⇒ 匹配前先脱色，
#: 否则真密钥会因为裹着 ANSI 转义而匹配不上。
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
#: 命中这一形状的那一行**不许照搬到屏幕上**：长串十六进制 = 密钥本身，
#: 花括号/方括号 = JSON 转储那一行，反斜杠 = 本机路径，`@` = 账号，引号 = 某个值。
#: 10-26 真机现核到它会打印 `tui config: {"data_key": …}`——那一行里就有密钥，
#: 抄进屏幕那句等于把值从管道换个地方又说一遍。挑不到安全的一行就**不给那句**，
#: 只给我们自己的话（口径 2 要的是"说明白为什么"，不是"转述工具的原文"）。
_UNSAFE_SHAPE = re.compile(r"[0-9a-fA-F]{24,}|[{\"\[\]\\@]")

#: 那句"还在后台扫"的话——`fetch` 与测试都读它，别在两处各写一份。
STILL_RUNNING = ("已经让本机自己去找那把密钥了，第一次扫微信内存可能要一两分钟。"
                 "这一趟没等它；扫完会自己落进本机那一份配置，稍后再点一次开关就解得开。")


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


def _find_key(text: str) -> Optional[str]:
    plain = _plain(text)
    labelled = _KEY_LABELLED.search(plain)
    if labelled:
        return labelled.group(1)
    for line in plain.splitlines():
        bare = _KEY_BARE.match(line)
        if bare:
            return bare.group(1)
    return None


def _quoteable_line(text: str) -> str:
    """从它自己打印的话里挑一句能照搬到屏幕上的；挑不到就回空。

    空是**可接受的答案**：宁可只显示我们自己那句，也不把可能带密钥/路径/账号的那一行抄上去。
    """
    for raw in _plain(text).splitlines():
        line = raw.strip()
        if not line or _UNSAFE_SHAPE.search(line):
            continue
        return line[:60]
    return ""


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


def _drain(streams) -> str:
    """读干净那两个管道。**只在子进程已经退出来之后调**（真管道会读到 EOF 才回，
    也就是"等它退出"本身就是这一步的一部分）。"""
    text = ""
    for stream in streams:
        if stream is None:
            continue
        try:
            text += (stream.read() or "")
        except (OSError, ValueError):                # 管道已关/编码坏了：不是要紧的读数
            continue
    return text


def fetch(env: Optional[Mapping[str, str]] = None, *,
          launcher=subprocess.Popen, timeout: float = _WAIT_SECONDS) -> tuple[bool, str]:
    """试着替他取一次数据密钥。回 ``(取到没有, 那一句人话)``——**那句话里永远没有密钥**。

    ``launcher`` 只是测试的注入口（形状 = ``subprocess.Popen``），产品路径永远是它自己。
    """
    env = os.environ if env is None else env

    if data_key_present(env):
        return True, "解密密钥本机已经有了，这次没去动微信。"

    binary = resolved_binary(env)
    if not binary:
        return False, "本机还没有微信采集器的可执行产物，密钥取不了——缺的是那一次构建。"

    # 把配置目录钉成我们读的那一份：产物成功后自己会 `UpdateConfig()`（`manager.go:266`），
    # 没钉 `CHATLOG_DIR` 时它回退 `C:\\Users\\<user>\\.chatlog`——既违反 D 盘规矩（裁定 109 三），
    # 又让它写的位置和我们读的位置分成两处（写了也等于没写）。
    child_env = dict(env)
    child_env["CHATLOG_DIR"] = str(engine_config_dir(env))

    try:
        proc = launcher([str(binary), "key"], stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True, env=child_env)
    except OSError as exc:
        return False, f"取密钥的命令跑不起来：{type(exc).__name__}。"

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        # **不杀**（口径 4）。这一条返回时子进程还在跑，那两个管道里后面还会出东西，
        # 而我们已经不等了：留一个后台读手把管道读干净并在他扫完时落那一档配置。
        timed_out = True
        threading.Thread(target=_finish_in_background, args=(proc, env), daemon=True).start()

    if timed_out:
        return False, STILL_RUNNING

    text = _drain((getattr(proc, "stdout", None), getattr(proc, "stderr", None)))
    key = _find_key(text)

    if not key:
        # 退出码在这里**不是依据**：10-26 真机现核，它失败照样回 0（`cmd_key.go:31` 打完日志
        # 直接 return，没有 `os.Exit`）。唯一算数的是"这堆输出里有没有那串形状"
        # ——外加它可能已经自己把配置写好了，那一句就不算失败。
        if data_key_present(env):
            return True, "已经替你取到密钥并写进本机那一份配置，点开关就解得开了。"
        if "select a process" in _plain(text).lower():
            return False, ("本机同时在跑好几个微信，这一次没法替你定该取哪一份。"
                           "请先只留一个微信窗口，再点一次开关。")
        reason = _quoteable_line(text)
        return False, ("这次没从微信里取到密钥。"
                       + (f"它自己那句：{reason}" if reason else ""))

    try:
        _write_handoff(engine_config_dir(env), key)
    except OSError as exc:
        return False, f"密钥取到了，但写不进本机那一份配置：{type(exc).__name__}。"

    return True, "已经替你取到密钥并写进本机那一份配置，点开关就解得开了。"


def _finish_in_background(proc, env: Mapping[str, str]) -> None:
    """那一头等不到的扫描：把管道读干净（不读会卡死在它自己那 4KB 缓冲上），
    出来是密钥就照同一处真源落盘。全程**不进日志、不进 stdout**（口径 1）。

    `read()` 读到 EOF 才回，也就是这个线程天然等到子进程扫完；不需要再 wait 一次。
    """
    try:
        text = _drain((getattr(proc, "stdout", None), getattr(proc, "stderr", None)))
    except (OSError, ValueError) as exc:
        logger.debug("chatlog key background run unreadable: %s", type(exc).__name__)
        return
    key = _find_key(text)
    if not key:
        return                                       # 没取到：下一次读状态自然会说为什么
    try:
        _write_handoff(engine_config_dir(env), key)
    except OSError as exc:
        logger.debug("chatlog key handoff write failed: %s", type(exc).__name__)
