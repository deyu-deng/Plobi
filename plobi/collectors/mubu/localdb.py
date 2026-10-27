"""幕布客户端在本机留下的那份数据：**只借它的登录态，不读它的正文缓存**。

2026-10-27 这台实测（只按键名/长度/摘要说话，没落一个正文）：

- 落点在 ``%APPDATA%\\Mubu\\mubu_app_data\\mubu_data\\.storage``——**在 C 盘**，是它自己选的，
  我们只读不写，也不挪它（挪一家别人的软件不是这一刀的范围）。
- 里面是 RxDB（PouchDB/LevelDB）一族目录，每枚集合一个目录；集合里那份
  ``…-rxdb-1-users`` 有一条 ``|u`` = 239 字符的 JWT，就是当前登录态。
- ``…-rxdb-2-documents`` 只有**一条**（``|c`` 16 175 字符），是"最后打开过的那一篇"的缓存，
  而且与接口今天的 ``definition``（17 339 字符）**不等** ⇒ 本机那份是过期快照，
  当不了正文真源。所以这一包不实现"本机读正文"那条腿，正文一律走接口（`api.py`）；
  这一只手只回答"这台机器上有没有一个登录好的幕布客户端"。

键名单字母（``|u``/``|c``）不是幕布自己的字典，是 RxDB 的 schema 压缩：表按 schema
属性顺序生成、前缀 ``|``（在 ``app.asar`` 的 ``DEFAULT_COMPRESSION_FLAG`` 那只手里核到）。
我们**不按属性名找**，按 `_id` 是否存在 + 值像不像 JWT 来认——省得跟着它的 schema 走。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

#: 集合目录名的尾巴（``…-rxdb-1-users``、``…-rxdb-2-documents``）。数字那一段随版本跳过。
_USERS_COLLECTION = "users"

_DECODER = json.JSONDecoder()
_BRACE = re.compile(rb"\{")


def storage_roots() -> list[Path]:
    """幕布本机数据的可能落点。Windows 走 ``APPDATA``；别的平台今天没有对应的客户端。"""
    appdata = Path(__import__("os").environ.get("APPDATA", "") or ".")
    return [appdata / "Mubu" / "mubu_app_data" / "mubu_data" / ".storage"]


class LocalStoreUnavailable(RuntimeError):
    """本机那份读不出可用登录态。``code`` 是给界面查语言目录的词，不是给人看的句子。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _records(root: Path, collection: str) -> dict[str, dict[str, Any]]:
    """扫那一枚集合目录，返回 ``{_id: 记录}``。

    LevelDB 的 ``.log``/``.ldb`` 里值是裸 JSON 文本，所以"每个 ``{`` 处试解一次"就能把
    记录捞出来。这一只手**只用来挑键名与 ``_id`**，不解正文——正文那一条腿不在这走（模块头写了为什么）。
    """
    out: dict[str, dict[str, Any]] = {}
    for blob in sorted(list(root.glob("*.log")) + list(root.glob("*.ldb"))):
        try:
            data = blob.read_bytes()
        except OSError:
            continue
        for match in _BRACE.finditer(data):
            try:
                text = data[match.start():].decode("utf-8")
            except UnicodeDecodeError:
                continue
            try:
                node, _ = _DECODER.raw_decode(text)
            except ValueError:
                continue
            if isinstance(node, dict) and isinstance(node.get("_id"), str):
                out[node["_id"]] = node
    return out


def _collection_dirs(root: Path, collection: str) -> list[Path]:
    return [p for p in root.iterdir()
            if p.is_dir() and p.name.endswith("-" + collection) and "mrview" not in p.name]


def read_token(*, roots: Optional[list[Path]] = None) -> str:
    """幕布客户端今天用的那枚 JWT。读不到就抛 :class:`LocalStoreUnavailable`。

    这只手是**凭据的唯一出口**：调用方拿到值只进内存、进请求头，
    不许进日志、进读数、进命令行（``probe()`` 因此只报长度量级，一个字都不回显）。
    """
    for root in (roots if roots is not None else storage_roots()):
        if not root.is_dir():
            continue
        for collection in _collection_dirs(root, _USERS_COLLECTION):
            for record in _records(collection, _USERS_COLLECTION).values():
                for key, value in record.items():
                    if isinstance(value, str) and value.count(".") == 2 and len(value) > 80:
                        return value
    raise LocalStoreUnavailable("client_not_logged_in")


def probe(*, roots: Optional[list[Path]] = None) -> dict:
    """`{"ready", "code", "detail"}` ——**这一只探针永不抛**（读不到盘也是一条读数）。

    `ready` 的判据只有一件事：**这台机器上有没有一个登录好的幕布客户端可以借**。
    借得到登录态不等于已经采到过一条东西，所以这里只报前提、不报结果。
    """
    checked = roots if roots is not None else storage_roots()
    try:
        present = [root for root in checked if root.is_dir()]
    except OSError as exc:
        return {"ready": False, "code": "local_store_missing",
                "detail": f"本机数据目录读不到：{type(exc).__name__}"}
    if not present:
        return {"ready": False, "code": "client_not_installed",
                "detail": "这台机器上没有装过幕布桌面客户端"}
    try:
        read_token(roots=checked)
    except LocalStoreUnavailable as exc:
        return {"ready": False, "code": exc.code,
                "detail": "装了，但那份数据里没有可用的登录态 ⇒ 客户端没在这台登录过"}
    return {"ready": True, "code": "ready", "detail": "本机客户端已登录，可借它的登录态"}


__all__ = ["LocalStoreUnavailable", "probe", "read_token", "storage_roots"]
