"""多账号：一家客席下面的**几个身份**从哪读、派活时用哪一个（11-16）。

规矩在真源表（``agent/vendor_registry.py`` 的 :class:`AccountRule`），
这里只按规矩读**盘上那份**清单 —— 用户随时会切账号、加账号 ⇒ 它是**会变的东西**，
不进真源表的数据。

三件事，各有各的边界：

* **读清单**（:func:`read_accounts`）—— 这一族下面有哪几个身份、哪个是厂商那边当前登录的。
  名字被厂商加密时（``{"$wbEncrypted": 1, …}``）**读不出来** ⇒ 退到账号标识的前八位
  （宁可给一个认得出的标识，也不给空白，也不编一个名字）。
* **选身份**（:func:`selected_account` / :func:`set_selected_account`）—— 主人选的那一个
  落在 ``config.yaml`` 的 ``delegation.offload_accounts``（``{厂商: 账号标识}``）。
* **派活时把它交出去**（:func:`apply_account_env`）—— 厂商认哪个入口、写哪种形状，
  **写在真源表那一行**；这里只按规矩把那个身份的凭据摆成厂商要的那份快照。

**照实说（这一族）**：CodeBuddy／WorkBuddy 的会话是**共享一棵 projects 树**的，
会话文件里**不带账号字段** ⇒ 账号这一层管的是「有哪几个身份、派活用哪一个」，
**管不到会话归属**。

**照实说（凭据交接）**：厂商自己的账号切换走的是**交互式**那条路
（CodeBuddy CLI 的 ``/login``＝「Switch Tencent Cloud CodeBuddy accounts」，
底层是拿 refresh token 打 ``/v2/…/account/switch``）；非交互的派活腿吃不下它。
所以这一层走的是厂商**给非交互进程留的那个入口**（凭据快照文件），
而不是去代它做一次登录。
"""
from __future__ import annotations

import io
import json
import os
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from agent.vendor_registry import ACCOUNT_RULES, AccountRule, resolve_candidate

#: 盘上那份清单几秒内算一份（屏上那栏刷新不必每次都去读三个文件）。
_TTL = 5.0
_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}

#: 选中的身份落在 config.yaml 的这一段（``{厂商: 账号标识}``）。
_CONFIG_SECTION = "delegation"
_CONFIG_KEY = "offload_accounts"


# ── 读清单 ──────────────────────────────────────────────────────────────

def _label_of(item: dict[str, Any], rule: AccountRule) -> str:
    """显示名。

    厂商把昵称加密时（``{"$wbEncrypted": 1, "envelope": …}``）读不出来 ⇒
    **退到账号标识**（宁可给一个认得出的标识，也不给空白）。
    """
    raw = item.get(rule.label_field)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return ""


def _read_json(path: Path) -> Any:
    try:
        text = io.open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _entries(data: Any, rule: AccountRule) -> list[dict[str, Any]]:
    """那个文件里的账号数组（``accounts``／``allAccounts``……；顶层就是数组也行）。"""
    items: list[Any] = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for key in rule.list_keys:
            value = data.get(key)
            if isinstance(value, list):
                items = value
                break
    return [item for item in items if isinstance(item, dict)]


def read_accounts(vendor: str) -> list[dict[str, Any]]:
    """一"族"客席的账号清单：``[{"id", "label", "current"}]``。

    没登记规矩的那一族 ⇒ **空清单**（那意思是"这一族没做多账号"，
    不是"它没有账号"——两回事，屏上别混）。

    同一个账号出现在几个文件里 ⇒ **按标识合并成一条**；显示名取**第一个读到的**
    （厂商把昵称加密的那份文件读不出名字，先读到的明文那份就赢了）。

    **「当前登录的是哪一个」**：这一族的身份文件是**一个族里几个产品各存各的**
    （CodeBuddy 扩展／WorkBuddy 桌面／WorkBuddy 桌面 AI 各登录着各自的那个）⇒
    文件次序就是**产品次序**，**第一份文件里当前登录的那个**才是这一族当前的身份；
    其余文件里那个是**另一个产品**当前登录着谁，不是这一族的。
    """
    rule = ACCOUNT_RULES.get(vendor)
    if not rule:
        return []
    now = time.time()
    hit = _CACHE.get(vendor)
    if hit is not None and now - hit[0] < _TTL:
        return [dict(row) for row in hit[1]]

    merged: dict[str, dict[str, Any]] = {}
    #: 这一族当前登录着的那一个（**第一份**文件说了算，见上面那段说明）。
    primary: str = ""
    for candidate in rule.files:
        path = resolve_candidate(candidate)
        if not path.is_file():
            continue
        for item in _entries(_read_json(path), rule):
            ident = str(item.get(rule.id_field) or "").strip()
            if not ident:
                continue
            row = merged.setdefault(ident, {"id": ident, "label": "", "current": False})
            if not row["label"]:
                row["label"] = _label_of(item, rule)
            if item.get(rule.current_field) and not primary:
                primary = ident

    out = sorted(merged.values(), key=lambda row: (row["id"] != primary, row["id"]))
    for row in out:
        row["current"] = row["id"] == primary
        if not row["label"]:
            # 读不出名字 ⇒ 给标识的前八位（认得出是谁，且不编一个名字）
            row["label"] = row["id"][:8]
    _CACHE[vendor] = (now, [dict(row) for row in out])
    return out


# ── 那个身份的凭据（派活时交出去的那一份）──────────────────────────────

def account_session(vendor: str, account_id: str) -> dict[str, Any]:
    """选中那个身份的**登录凭据**：``{"account": …, "auth": …}``；取不到就空。

    **凭据从哪个文件取**：那个文件里「当前身份」正好是**你选的那个**的文件。
    （同一族里有好几个身份文件，各是各的产品的——CodeBuddy 扩展与 WorkBuddy 桌面
    各存各的令牌；取错了就是拿另一个产品的令牌去打这一家的后端。）

    **这是照抄盘上的东西**：``account``／``auth`` 那两块原样搬，不拼、不造、不补。
    """
    rule = ACCOUNT_RULES.get(vendor)
    target = str(account_id or "").strip()
    if not rule or not target:
        return {}
    for candidate in rule.files:
        path = resolve_candidate(candidate)
        if not path.is_file():
            continue
        data = _read_json(path)
        if not isinstance(data, dict):
            continue
        account = data.get(rule.account_key)
        auth = data.get(rule.auth_key)
        if not isinstance(account, dict) or not isinstance(auth, dict):
            continue
        if str(account.get(rule.id_field) or "").strip() != target:
            continue
        token = str(auth.get("accessToken") or "").strip()
        if not token:
            continue
        return {"account": account, "auth": auth}
    return {}


# ── 主人选了哪一个 ──────────────────────────────────────────────────────

def _config() -> dict[str, Any]:
    try:
        from plobi_cli.config import load_config
    except Exception:  # noqa: BLE001 - 读不到配置就当没选过
        return {}
    try:
        return load_config() or {}
    except Exception:  # noqa: BLE001
        return {}


def selected_account(vendor: str) -> str:
    """主人为这一族选的身份标识。空 = 没选 ⇒ 厂商用它自己当前登录的那个。"""
    section = _config().get(_CONFIG_SECTION)
    if not isinstance(section, dict):
        return ""
    chosen = section.get(_CONFIG_KEY)
    if not isinstance(chosen, dict):
        return ""
    return str(chosen.get(vendor) or "").strip()


def set_selected_account(vendor: str, account_id: str) -> str:
    """把「这一族用哪一个身份」写进 ``config.yaml``。

    只动 ``delegation.offload_accounts`` 这一格，其余配置原样写回。
    写不进去就抛 ``RuntimeError``，让路由如实回错误，**不假装存下了**。
    """
    name = str(vendor or "").strip().lower()
    if not name:
        raise ValueError("厂商名不能为空")
    target = str(account_id or "").strip()
    if target and target not in {row["id"] for row in read_accounts(name)}:
        raise ValueError("这一族里没有这个账号")
    try:
        from plobi_cli.config import save_config
    except Exception as exc:  # noqa: BLE001 - 配置模块不在就当存不下
        raise RuntimeError(f"读不到配置模块：{exc}") from exc

    current = _config()
    section = current.get(_CONFIG_SECTION)
    if not isinstance(section, dict):
        section = {}
        current[_CONFIG_SECTION] = section
    chosen = section.get(_CONFIG_KEY)
    if not isinstance(chosen, dict):
        chosen = {}
    if target:
        chosen[name] = target
    else:
        chosen.pop(name, None)
    section[_CONFIG_KEY] = chosen
    save_config(current)
    return target


# ── 派活时：把它交出去 ──────────────────────────────────────────────────

def apply_account_env(agent: str, env: dict[str, str]) -> tuple[str, Optional[Path]]:
    """把选中的那个身份的凭据交给厂商（**凭据交接**）。

    返回 ``(身份标识, 快照文件路径)``；没交出去就是 ``("", None)``。

    **厂商认哪个入口、写哪种形状，写在真源表那一行**（``AccountRule``）⇒
    这里没有按厂商分叉的代码：加一家新的多账号客席，只改表。

    **没选身份 ⇒ 什么都不交**（厂商用它自己当前登录的那个，行为一个字不变）。
    """
    rule = ACCOUNT_RULES.get(agent)
    if not rule or not rule.credential_env:
        return "", None
    account_id = selected_account(agent)
    if not account_id:
        return "", None
    session = account_session(agent, account_id)
    if not session:
        return "", None
    snapshot = _write_snapshot(agent, session, rule)
    if not snapshot:
        return "", None
    env[rule.credential_env] = str(snapshot)
    return account_id, snapshot


def _write_snapshot(agent: str, session: dict[str, Any], rule: AccountRule) -> Optional[Path]:
    """把那份凭据摆成厂商要读的快照文件，返回它的路径（写不出就 ``None``）。

    **厂商读的那份形状**（CodeBuddy CLI 现读）：``{"session": {"account": …, "auth": …}}``。
    里面那两块是**盘上原样搬过来的**，不拼、不造、不补。

    **为什么要落盘**：厂商给非交互进程留的入口就是一个**路径**
    （``CODEBUDDY_AUTH_SNAPSHOT_PATH``）——它自己那次交接在 Windows 上不走盘
    （走文件描述符），而文件描述符那一套是父子进程之间才有的通道。
    文件名是随机的、跑完就删（见 :func:`remove_snapshot`）。
    """
    if rule.credential_kind != "session":
        return None
    folder = Path(tempfile.gettempdir()) / "plobi-offload-accounts"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{agent}-{secrets.token_urlsafe(8)}.json"
        path.write_text(json.dumps({"session": session}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return None
    return path


def remove_snapshot(path: Any) -> None:
    """跑完把那份快照删掉（它里面有令牌）。"""
    if not path:
        return
    try:
        Path(str(path)).unlink(missing_ok=True)
    except OSError:
        pass


def forget_accounts_cache() -> None:
    """清掉缓存（测试与「刚切完账号要立刻看见」用）。"""
    _CACHE.clear()
