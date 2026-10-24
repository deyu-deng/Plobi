r"""钉钉这一条在本机成不成立（用户 10-24：「我们组织没有开放 CLI。要用别的方式」＋
「钉钉你两个方式都要支持。用户按需选择。只不过我肯定是用"我登录一次客户端"的方式」）。

先说这档**不做**什么：不解密、不读正文、不碰进程内存、不联网。它只回答一句
**「这台机器今天给得出哪条路」**，并把答案变成能上屏的读数。

三条路今天的形状（2026-10-24 现核，客户端 `D:\Software\DingDing\main\current\DingTalk.exe`
= 9.0.2.261013001）：

- **甲 · 官方 dws CLI（个人身份 OAuth → 钉钉 MCP 网关）**——最宽的一条（历史、实时、待办、审批、
  考勤都给它），但它要求**该组织管理员在开发者后台开通"CLI 访问管理"**，用户原话"我们组织没有开放
  CLI" ⇒ 今天走不通。这一档只报"这台装没装、配没配"，不去替他猜组织点没点。
- **乙 · 企业内部应用（Client ID/Secret + Stream）**——本仓网关侧**已有**这条收发通道
  （`plugins/platforms/dingtalk/adapter.py`），天花板是"被 @ 的那一条"、无历史回读；
  建应用同样要管理员。这一档报那两个键在不在（只看键名，值一个字节都不读出来）。
- **丙 · 桌面客户端那份本机库**——用户自己就能解锁的一条（登录一次即可），也就是他选的这条。
  实现体在 :mod:`.store` 与 :mod:`.collector`；这一档只报"库在不在位"，**不报"采得到"**：
  库在位但一条会话都没准入，那是零条，两句话不许合成一句（判据 5 那条「成功但空货」的教训）。

为什么不接 WAL 合并：`store.py` 的模块头写着实测——这台的 3845 帧重放后行数与基础库一致，
而对密文页算校验和链一帧都对不上 ⇒ 我不 ship 一个"看起来合并了"的路，也不 ship 一个
解不开的假判据。代价照实写：**最新那几秒到几分钟的写入可能还在 WAL 里**。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Optional

from . import store


def _org_routes() -> dict[str, bool]:
    """组织侧那两条今天有没有配。**只看键名存在与不为空**，绝不把值带出去。"""
    from plobi_cli.config import load_env

    env = load_env()
    configured = bool((os.environ.get("DINGTALK_CLIENT_ID") or env.get("DINGTALK_CLIENT_ID") or "").strip())
    secret = bool((os.environ.get("DINGTALK_CLIENT_SECRET") or env.get("DINGTALK_CLIENT_SECRET") or "").strip())
    return {
        "app": configured and secret,
        "cli": shutil.which("dws") is not None,
    }


def probe(*, roots: Optional[list[Path]] = None, org: Any = None) -> dict:
    """`{"ready", "code", "detail"}` ——**这一只探针永不抛**（读不到盘也算一条读数）。

    `ready` 的判据只有一件事：**有没有一条今天走得通的采集路**。今天只有丙。
    """
    org_routes = _org_routes() if org is None else (org() if callable(org) else dict(org))
    org_note = "组织侧：CLI {cli}、企业内部应用 {app}".format(
        cli="已装" if org_routes.get("cli") else "未装",
        app="已配" if org_routes.get("app") else "未配")

    try:
        found = store.profiles(roots if roots is not None else store.data_roots())
    except OSError as exc:
        return {"ready": False, "code": "local_store_missing",
                "detail": f"本机数据目录读不到：{type(exc).__name__}；{org_note}"}

    if not found:
        if any(root.is_dir() for root in (roots or store.data_roots())):
            return {"ready": False, "code": "client_not_logged_in",
                    "detail": f"客户端装了、但这台没有已登录的资料目录 ⇒ 本机库里没有东西可采；{org_note}"}
        return {"ready": False, "code": "client_not_installed",
                "detail": f"这台机器上没有装过/登录过钉钉桌面客户端；{org_note}"}

    profile = found[0]
    db = store.db_path(profile)
    if db is None:
        return {"ready": False, "code": "local_store_missing",
                "detail": f"资料目录 {profile.name} 在，但里面没有 {store.DB_NAME}；{org_note}"}

    # 到这一步丙路成立。注意这一句**不声称**采得到东西：准入名单是空的，今天就是零条。
    return {"ready": True, "code": "local_store_found",
            "detail": f"本机库在位：{db.name}（{db.stat().st_size // 1024} KB，{profile.name}）；"
                      f"采集要逐扇会话点名，一条都没点名就是零条；{org_note}"}
