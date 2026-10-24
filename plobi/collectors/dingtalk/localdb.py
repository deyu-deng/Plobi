r"""钉钉这一条**在本机成不成立** —— 那只探针（用户 10-24：「我们组织没有开放 CLI。要用别的方式」）。

先说这档**不做**什么：不解密、不读正文、不碰进程内存、不联网。它只做一件事——
回答「这台机器现在给不给得出钉钉那条要的前提」，并把答案变成一个能上屏的读数。

为什么要有这一格：屏上那一行今天画的是 `error/internal`（「本机采集组件暂未就绪」），
那是**说我们在座的某样东西坏了**。它的真话不是这个。真读数（2026-10-24 现核）是：

- 甲路（官方 dws CLI，个人身份 OAuth）——**用户组织没开**，这条路要管理员在开发者后台点"CLI 访问管理"，
  不在我们手里；
- 乙路（企业内部应用 + Stream 长连接，本仓网关侧已有收发通道）——要 Client ID/Secret，
  同样要管理员建应用；这台机器上 `$PLOBI_HOME/.env` 现读**没有**那两个键；
- 丙路（**桌面客户端那份本地库**，与本仓微信那一路同形状）——算法与目录都是公开的
  （`Docs/reference/dingtalk-collection-survey.md` §4：V2/V3 密钥推导、`dingtalk_encrypt.db`、
  128 张 `tbmsg_*` 分片表），唯一的前提是**这台机器上有人登录过那个客户端**。
    现读：客户端装在 `D:\Software\DingDing`，而 `%APPDATA%\DingTalk` 下面只有 `log/package.log`
    ——**从没登录过**，所以本地库根本不存在，丙路今天能给的是零。

所以这一档交的是"把丙路的前提变成一句看得见的话"，**解密器本体押后到有真库可验时**——
判据在仓里写着：静态绿不算可用（没有一枚真库就写 AES 页解密，我验的是自己编的形状）。

`ready=True` 那一支现在没人能触发（这台机器还没登录过），但它不是留着占位的：它是
下一刀（真解密 + 落池）的**入口条件**，而那一刀开工前该先跑的就是这一只探针。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional

#: 测试注入位；界面上不出现这一格。生产走那两只唯一的解析口（:func:`_data_roots` / :func:`_installed`）。
DB_NAME = "dingtalk_encrypt.db"
USER_CONFIG_NAME = "user_config"
#: 目录名那一族：V2 是 `{uid}_v2`，V3 是 `{20 位 hex}_v3`（调研件 §4.2 那一表：**V3 目录名不是 UID**）。
_PROFILE = re.compile(r"^[0-9A-Za-z]{6,}(_v[23])?$")


def data_roots() -> list[Path]:
    """客户端可能把本机数据放的那几个根（`PLOBI_DINGTALK_DATA_DIR` 是给测试钉的，不是给用户填的）。"""
    override = (os.environ.get("PLOBI_DINGTALK_DATA_DIR") or "").strip()
    if override:
        return [Path(override)]

    roots: list[Path] = []
    for var in ("APPDATA", "LOCALAPPDATA"):
        base = (os.environ.get(var) or "").strip()
        if base:
            roots.append(Path(base) / "DingTalk")

    # macOS/Linux 那两个常见落点（调研件 §4.1 逐字：`~/.config/DingTalk`、
    # `~/Library/Application Support/DingTalkMac`）——留着不是"顺手支持一下"，
    # 是因为这一族的目录**已经在换过一次家**（那条 macOS 的 issue 还开着），
    # 只认一个根就会把"装了并登录了"读成"没装"。
    home = Path.home()
    roots.append(home / ".config" / "DingTalk")
    roots.append(home / "Library" / "Application Support" / "DingTalkMac")

    return roots


def _profile_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    try:
        return [p for p in sorted(root.iterdir())
                if p.is_dir() and _PROFILE.match(p.name) and not p.name.startswith(".")]
    except OSError:
        return []


def _store_version(profile: Path) -> str:
    """这一代加密是哪一版（V3 的 salt 在 `user_config` 里，V2 不需要那个文件）。"""
    if (profile / USER_CONFIG_NAME).is_file():
        return "v3"
    if profile.name.endswith("_v2"):
        return "v2"
    return "unknown"


def _installed() -> bool:
    """这台机器装过钉钉桌面端没有。**只做存在性判断**，不执行、不读取那个目录里的任何东西。"""
    import sys

    if sys.platform.startswith("win"):
        # 10-24 现核：这台那条记录在 **HKLM 的 `WOW6432Node`** 那侧，`DisplayName` 是「钉钉」。
        # 只扫 HKCU 会把它读成"没装过"——而"没装"与"装了没登录"是两句不同的话，
        # 界面上落的是两句不同的句子（`client_not_installed` / `client_not_logged_in`）。
        for hive in ("HKCU", "HKLM"):
            for base in (r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
                         r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"):
                if _uninstall_names(f"{hive}\\{base}"):
                    return True
        return any(Path(one).is_dir() for one in _install_candidates())

    return Path("/Applications/DingTalk.app").is_dir() or Path("/Applications/钉钉.app").is_dir()


_HIVES = {"HKCU": "HKEY_CURRENT_USER", "HKLM": "HKEY_LOCAL_MACHINE"}


def _uninstall_names(key: str) -> list[str]:
    """那一支下面所有DisplayName里像钉钉的那几个。**读不到就当没有**（不抛）。"""
    names: list[str] = []
    try:
        import winreg

        hive_name, _, sub_key = key.partition("\\")
        with winreg.OpenKey(getattr(winreg, _HIVES[hive_name]), sub_key) as hkey:
            for index in range(winreg.QueryInfoKey(hkey)[0]):
                sub_name = winreg.EnumKey(hkey, index)
                try:
                    with winreg.OpenKey(hkey, sub_name) as sub:
                        value = winreg.QueryValueEx(sub, "DisplayName")[0]
                except OSError:
                    continue
                if isinstance(value, str) and ("ding" in value.lower() or "钉" in value):
                    names.append(value)
    except (OSError, ImportError, AttributeError, ValueError):
        return []
    return names


def _install_candidates() -> list[str]:
    out = []
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = (os.environ.get(var) or "").strip()
        if base:
            out.append(str(Path(base) / "DingDing"))
            out.append(str(Path(base) / "DingTalk"))
    return out


def probe(*, roots: Optional[list[Path]] = None, installed: Any = None) -> dict:
    """`{"ready": bool, "code": str, "detail": str}` —— **这一只探针永不抛**。

    读不到盘就说读不到（`local_store_missing`），不把那一屏弄红：那条红点是"我们的产品坏了"
    的意思，而这里要说的是"这台机器还没给它要的前提"。
    """
    roots = data_roots() if roots is None else roots
    is_installed = _installed() if installed is None else (installed() if callable(installed) else installed)

    try:
        profiles = [one for root in roots for one in _profile_dirs(root)]
        saw_root = any(root.is_dir() for root in roots)
    except OSError as exc:  # 盘读不到（权限/网络盘掉了）也是一种"前提不在"
        return {"ready": False, "code": "local_store_missing",
                "detail": f"本机数据目录读不到：{type(exc).__name__}"}

    with_db = [one for one in profiles if (one / DB_NAME).is_file()]

    if with_db:
        # 前提齐了：本地库在位。**注意这一句不等于"能采到东西"**——本地库会不会滞后是
        # 一条独立的健康判据（调研件 §4.4：dingwave issue #4「dingtalk.db 拿不到最新的消息」，
        # 至今 open）。那一刀开工时这里就是它的入口。
        first = with_db[0]
        return {"ready": True, "code": "local_store_found",
                "detail": f"本地库在位：{first / DB_NAME}（{_store_version(first)}）"}

    if profiles:
        return {"ready": False, "code": "local_store_missing",
                "detail": f"有 {len(profiles)} 个已登录的资料目录，但里面没有 {DB_NAME}"}

    # 资料目录一个都没有，但**根目录在**（安装器自己会建它，这台现读就是 `<APPDATA>\DingTalk\log`）
    # ⇒ 装过、跑过，只是从没登录过。这一支不看注册表：盘上那个根比一条卸载记录更硬。
    if saw_root or is_installed:
        return {"ready": False, "code": "client_not_logged_in",
                "detail": "桌面客户端装了，但这台机器上它从没登录过 ⇒ 本地库里没有东西可采"}

    return {"ready": False, "code": "client_not_installed",
            "detail": "这台机器上没有装过钉钉桌面客户端"}
