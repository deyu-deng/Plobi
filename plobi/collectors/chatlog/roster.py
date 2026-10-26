"""设置页那份**会话名单**的那一手：把产物报的会话号变成他认得出的三组列表（裁定 203 口径 1）。

为什么单独一档，而不是并进 :mod:`.client`：`client.py` 今天在别人在派的那把刀名下
（`WP-CHATLOG-HOMING`），而这一屏要的东西与"采集怎么读"是两件事——采集只认会话号，
**看得懂是给人看的这一侧的事**。所以这一档只做一件事：把三张表拼成界面上那一行。

三条判据都写在这里，界面不再各算一份：

1. **名字不是 session 那一格给的**。10-26 真机现核：`/api/v1/session` 在这台上有
   **125/167 扇的 `nickName` 是空的**，屏幕上于是直接念 `wxid_k4e2zzvrbfvs22` 这种号——
   那不是"乱码"，是我们没去查。同一台机上 `/api/v1/contact`（5850 条）与
   `/api/v1/chatroom`（64 条）就有 `remark`/`nickName`，join 完 **166/167 扇认得出**。
   次序按他那句「**尽量显示我的备注**」：**备注 ＞ 昵称 ＞ 会话号**。
2. **群名只认 `chatroom` 那张表**。session 里那一格 `nickName` 对群是**某个群成员**的名字
   （这台现核：`56172831152@chatroom` 那扇写的是一个人名），拿它当群名就是一句假话；
   查不到群名就让他看到号，并由 `resolved=False` 说明白"这是号不是名"。
3. **三组的判据只有形状**：`@chatroom` ⇒ 群、`gh_` ⇒ 公众号、其余 ⇒ 私聊。
   这是**名单那一档的词**（`group/official/private`，他 10-26 拍的三组），
   与池里 `visibility` 那两档（`group/private`，裁 187①）**不是一张词表**——
   那一档问"这条消息发生在不在群里"，这一档问"这一扇在名单里排哪一组"。
   公众号的消息进池仍归 `private`，那是池的口径，这里不动它。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

#: 界面上那三组的词表（顺序由界面定，这里只保证只出这三个值）。
ROSTER_KINDS = ("group", "official", "private")
GROUP_USERNAME_SUFFIX = "@chatroom"
OFFICIAL_USERNAME_PREFIX = "gh_"
#: 一次问几行。session 那一路默认 10000（`client.py` 同款），联系人那表这台 5850 行。
_LIMIT = 20000


def talker_kind(talker: Any) -> str:
    """这一扇排进哪一组。判据只有形状：不查库、不抛。"""
    name = str(talker or "").strip()

    if name.endswith(GROUP_USERNAME_SUFFIX):
        return "group"
    if name.startswith(OFFICIAL_USERNAME_PREFIX):
        return "official"
    return "private"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _items(client: Any, path: str) -> list[dict]:
    """那三只列表口都是 `{"items": [...]}`（这台现核），拿不到就当空表。

    走的是 `client._get` 这一缝——**这一包自己的那条 HTTP 腿**，不在这里再开一条
    `urllib`，否则产物换端口/换鉴权就要改两处。
    """
    try:
        payload = client._get(path, {"format": "json", "keyword": "", "limit": str(_LIMIT)})
    except Exception as exc:  # noqa: BLE001 - 查名字这条路失败不许把整份名单变成读不到
        logger.debug("roster: %s unreadable: %s", path, type(exc).__name__)
        return []

    if isinstance(payload, list):
        rows = payload
    else:
        rows = payload.get("items") if isinstance(payload, dict) else None

    return [row for row in (rows or []) if isinstance(row, dict)]


def display_names(client: Any) -> dict[str, str]:
    """会话号 → 他看得懂的名字（联系人与群那两张表拼出来的）。

    键登记两种写法：session 给的是 `57452225881@chatroom`，群那张表可能只给号——
    少一种写法就是下一行"有名字但查不到"。
    """
    names: dict[str, str] = {}

    for row in _items(client, "/api/v1/contact"):
        talker = _clean(row.get("userName") or row.get("username"))
        # 备注优先：那是**他给自己写的**称呼，比对方的昵称好认（他 10-26 点名的就是这个）。
        label = (_clean(row.get("remark")) or _clean(row.get("nickName"))
                 or _clean(row.get("nickname")) or _clean(row.get("alias")))
        if talker and label:
            names[talker] = label

    for row in _items(client, "/api/v1/chatroom"):
        room = _clean(row.get("name") or row.get("userName") or row.get("chatroomID"))
        label = (_clean(row.get("remark")) or _clean(row.get("nickName"))
                 or _clean(row.get("nickname")) or _clean(row.get("subject")))
        if not (room and label):
            continue
        bare = room.removesuffix(GROUP_USERNAME_SUFFIX)
        for key in (room, bare, f"{bare}{GROUP_USERNAME_SUFFIX}"):
            names.setdefault(key, label)

    return names


def label_for(talker: str, session_hint: str, names: dict[str, str]) -> tuple[str, bool]:
    """上屏的那个名字 + **它是不是真名字**（查不到就是 `(号, False)`，不假装是个人）。

    `session_hint` 由调用方**按类别挑过**再传进来：群聊只允许 `topicName`（旧版产物给的是群名），
    其余两类可以再加 `nickName`。
    """
    found = _clean(names.get(talker)) or _clean(session_hint)

    return (found or talker, bool(found))


def rows(client: Any, status_of: Optional[Callable[[str], Any]] = None) -> list[dict[str, Any]]:
    """界面上那一屏的每一份：**id / name / kind / last_at / resolved / status**。

    `client` 是那只侧车客户端（要用它那条 `_get` 缝）；`status_of` 是"这一扇采不采"的读手
    （微信那边是它自己那张 `TalkerStore`）。顺序按 session 给的原样（最近在前），
    界面那侧再按组内排序——这里不替界面决定它怎么排。
    """
    names = display_names(client)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for row in _items(client, "/api/v1/session"):
        talker = _clean(row.get("userName") or row.get("UserName")
                        or row.get("topicId") or row.get("personID") or row.get("talker"))
        if not talker or talker in seen:
            continue
        seen.add(talker)
        kind = talker_kind(talker)
        # 群那一档不给 `nickName` 兜底的机会（那句为什么写在 `label_for` 与档头第 2 条）。
        hint = _clean(row.get("topicName")) or ("" if kind == "group" else _clean(row.get("nickName")))
        name, resolved = label_for(talker, hint, names)
        out.append({
            "id": talker,
            "name": name,
            "kind": kind,
            "last_at": _clean(row.get("nTime")),
            "resolved": resolved,
            "status": (status_of(talker) if status_of else None) or "pending",
        })

    return out
