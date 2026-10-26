r"""钉钉那一版本机库的**读手**：找到资料目录 → 推出钥匙 → 解出一枚能开的副本 → 只读查询。

先说这文档串里最该被下一位读者知道的一条：**这台机器（客户端 9.0.2.261013001）上没有
`dingtalk_encrypt.db` 这个文件**——调研件 §4.1 那一句是旧版本/旧工具的写法，现读的实际落点是
`{profile}/DBFiles/dingtalk.db`（外加 `-wal` / `-shm`）。2026-10-24 我把 2454 组推导式全试了一遍
都解不开，原因不在算法而不在钥匙：**`_v3` 目录名是账号哈希、不是 UID**，而推导要用的是那个
10 位数字 UID。UID 在客户端自己的日志里（`real_uid=` / `myOpenId=` / `&uid=`），
拿它配上 `user_config` 里的 salt 一把就对——所以这一档的"找钥匙"这一段必须留在代码里，
它不是一个可以省掉的常数。

三条纪律：

1. **源文件一个字节都不写**。所有解密都落在 `$PLOBI_HOME/plobi/dingtalk-local/` 里的副本上。
   这与微信那一路同形（`chatlog/db_storage/` 也是解密副本躺在 D 盘），不是新开的口子；
   明文落在盘上是这条管道的固有代价，写在纸面上而不是藏起来。
2. **钥匙、UID、正文都不进日志**。`repr` 里也不许出现（`LocalStore` 故意不带 key 字段）。
3. **默认什么都读不到**：没有准入的会话 ⇒ `pull()` 回空表。这一格是隐私默认关（与微信
   那一份名册同一口径），不是"忘了实现"。

WAL 那一格照实说：这一版我没接合并。理由是一条实测——这台的 `-wal` 有 3845 帧，我把帧页
逐帧解密并按 SQLite 那套累加和重算校验和之后，**行数与只读基础库完全一样**（`tbmsg_*` 都是
155、`tbconversation` 都是 178、`integrity_check ok`），而对**密文页**算链则一帧都对不上
⇒ 我不能声称"WAL 里还有更新的东西被我解出来了"，也不能声称"合并已实现"。它带来的真实风险
是**最新几秒到几分钟的写入可能还在 WAL 里没进基础库**，所以 `latest_message_at` 那一格要
跟着健康判据一起看（调研件 §4.4 那条「dingtalk.db 拿不到最新的消息」就是这一族的历史）。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

#: 推导常数：`PBKDF2-HMAC-SHA1(uid+salt, "666DingT", 1000, 32)` → `MD5(...)` 的**十六进制前 16 个
#: 字符本身**当 AES-128 密钥（ASCII 文本，不是那 16 字节原始摘要——这一点我实测错过：原始摘要那一支不通）。
PBKDF2_SALT = b"666DingT"
PBKDF2_ITERATIONS = 1000
PAGE = 4096
DB_NAME = "dingtalk.db"
#: V3 目录名是账号哈希（20 位 hex），V2 目录名**就是** UID。两种形状分开认——
#: 上一版探针用一条宽松的"字母数字就行"把它们全认成了资料目录，于是 `%APPDATA%\DingTalk\config`
#: 这种也算进去了，报出「有 8 个已登录的资料目录」这种假读数。
_V3 = re.compile(r"^[0-9a-fA-F]{12,40}_v3$")
_V2 = re.compile(r"^\d{6,}_v2$")
_UID_PATTERNS = (re.compile(r"real_uid=(\d{8,14})"), re.compile(r"myOpenId=(\d{8,14})"),
                 re.compile(r"[&?]uid=(\d{8,14})"))
_BARE = re.compile(r"(?<!\d)(\d{9,13})(?!\d)")


class LocalStoreUnavailable(RuntimeError):
    """这一台现在给不出可读的本机库。`code` 是给界面查语言目录用的原因码。"""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def data_roots() -> list[Path]:
    """客户端可能放本机数据的那几个根（`PLOBI_DINGTALK_DATA_DIR` 是给测试钉的）。"""
    override = (os.environ.get("PLOBI_DINGTALK_DATA_DIR") or "").strip()
    if override:
        return [Path(override)]

    roots: list[Path] = []
    for variable in ("APPDATA", "LOCALAPPDATA"):
        base = (os.environ.get(variable) or "").strip()
        if base:
            roots.append(Path(base) / "DingTalk")
    home = Path.home()
    roots.append(home / ".config" / "DingTalk")
    roots.append(home / "Library" / "Application Support" / "DingTalkMac")
    return roots


def profiles(roots: Optional[Iterable[Path]] = None) -> list[Path]:
    """真·已登录的资料目录（形状见上面那两个式子）。"""
    found: list[Path] = []
    for root in (data_roots() if roots is None else roots):
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            if entry.is_dir() and ((_V3.match(entry.name) or _V2.match(entry.name))):
                found.append(entry)
    return found


def db_path(profile: Path) -> Optional[Path]:
    """那枚加密库。旧形状在目录根部，这一版在 `DBFiles/` 里——两种都认，因为**它确实换过位置**。"""
    for candidate in (profile / "DBFiles" / DB_NAME, profile / DB_NAME,
                      profile / "dingtalk_encrypt.db"):
        if candidate.is_file():
            return candidate
    return None


def _salt_of(profile: Path) -> str:
    config = profile / "user_config"
    if not config.is_file():
        raise LocalStoreUnavailable("local_store_missing", f"{profile.name} 里没有 user_config")
    try:
        return str(json.loads(base64.b64decode(config.read_bytes()))["salt"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise LocalStoreUnavailable("local_store_missing",
                                    f"user_config 读不出 salt：{type(exc).__name__}") from None


def derive_key(uid: str, salt: str) -> bytes:
    """V3 那把（PBKDF2 + MD5 十六进制前 16 个**字符**）。"""
    derived = hashlib.pbkdf2_hmac("sha1", f"{uid}{salt}".encode(), PBKDF2_SALT, PBKDF2_ITERATIONS, 32)
    return hashlib.md5(derived).hexdigest()[:16].encode("ascii")


def derive_key_v2(uid: str) -> bytes:
    """V2 那把：`MD5(uid).hex[:16]`（V2 不需要 salt）。"""
    return hashlib.md5(uid.encode()).hexdigest()[:16].encode("ascii")


def uid_candidates(roots: Optional[Iterable[Path]] = None, *, limit: int = 3000) -> list[str]:
    """候选 UID：环境变量 → 客户端日志里那三个模式 → 日志里的裸数字。

    顺序是有意义的：`real_uid=` 那一条几乎唯一，裸数字要几千个，靠"解一页看魔法字节"来判。
    """
    from_log: list[str] = []
    env = (os.environ.get("DINGTALK_UID") or "").strip()
    if re.fullmatch(r"\d{8,14}", env):
        from_log.append(env)

    root_paths = data_roots() if roots is None else roots
    for root in root_paths:
        log_dir = root / "log"
        if not log_dir.is_dir():
            continue
        for log in sorted(log_dir.rglob("*")):
            if not log.is_file() or log.stat().st_size > 60_000_000:
                continue
            try:
                text = log.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for pattern in _UID_PATTERNS:
                from_log.extend(pattern.findall(text))
            from_log.extend(_BARE.findall(text))

    unique = list(dict.fromkeys(from_log))
    return unique[:limit]


@dataclass(frozen=True)
class LocalStore:
    """一枚**解好密的**只读副本。故意不带密钥字段：谁 `repr` 它都不会漏出钥匙。"""

    connection: sqlite3.Connection
    profile: str
    conversations: int

    def close(self) -> None:
        self.connection.close()


def local_dir() -> Path:
    override = (os.environ.get("PLOBI_DINGTALK_LOCAL_DIR") or "").strip()
    if override:
        return Path(override)
    from plobi_constants import get_plobi_home

    return get_plobi_home() / "plobi" / "dingtalk-local"


def _decrypt_pages(source: Path, key: bytes, target: Path) -> int:
    """整页 ECB 解一遍（从偏移 0 开始，**没有跳过的文件头**——这一点也是实测：
    页 0 就是加密的 SQLite 魔法）。返回页数。"""
    data = source.read_bytes()
    target.parent.mkdir(parents=True, exist_ok=True)
    pages = 0
    with target.open("wb") as handle:
        for offset in range(0, len(data), PAGE):
            handle.write(_decrypt_block(key, data[offset:offset + PAGE]))
            pages += 1
    return pages


def _decrypt_block(key: bytes, block: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    out = decryptor.update(block) + decryptor.finalize()
    return out[:len(block)]


def open_local_store(*, profiles_dir: Optional[Iterable[Path]] = None,
                     candidates: Optional[list[str]] = None,
                     decrypt_page: Any = None) -> LocalStore:
    """找到钥匙、解出副本、开一个只读连接。**任何一步走不通就抛 `LocalStoreUnavailable`**。

    `decrypt_page` 是注入位（测试用一枚自己造的明文库加密成"假密文"，全程零真数据）。
    """
    profile_list = list(profiles(profiles_dir))
    if not profile_list:
        raise LocalStoreUnavailable("client_not_logged_in",
                                    "本机没有已登录的钉钉资料目录（登录一次客户端才会出现）")

    source = db_path(profile_list[0])
    if source is None:
        raise LocalStoreUnavailable("local_store_missing",
                                    f"{profile_list[0].name} 里找不到 {DB_NAME}")

    page_reader = decrypt_page or _decrypt_pages
    salt = _salt_of(profile_list[0])
    is_v3 = bool(_V3.match(profile_list[0].name))
    uid_list = uid_candidates() if candidates is None else candidates
    if not uid_list:
        raise LocalStoreUnavailable("local_store_missing",
                                    "客户端日志里找不出 UID，而 _v3 的目录名不是 UID")

    last_error: Optional[str] = None
    for uid in uid_list:
        # V3 要 salt，V2 不要——两支分开走，因为这台机器上**目录名形状就是这两族的判据**。
        key = derive_key(uid, salt) if is_v3 else derive_key_v2(uid)
        target = local_dir() / f"{profile_list[0].name}--{hashlib.md5(uid.encode()).hexdigest()[:8]}.db"
        try:
            page_reader(source, key, target)
        except OSError as exc:
            last_error = f"解密副本写不下去：{type(exc).__name__}"
            continue
        with target.open("rb") as handle:
            head = handle.read(16)
        if head != b"SQLite format 3\x00":
            target.unlink(missing_ok=True)
            last_error = "钥匙不对（页头不是 SQLite）"
            continue
        connection = sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True)
        try:
            conversations = connection.execute("SELECT count(*) FROM tbconversation").fetchone()[0]
        except sqlite3.Error:
            connection.close()
            target.unlink(missing_ok=True)
            last_error = "解开了但没有会话表——这不是我们认的那枚库"
            continue
        return LocalStore(connection=connection, profile=profile_list[0].name,
                          conversations=int(conversations))

    raise LocalStoreUnavailable("local_store_missing", last_error or "没试出可用的钥匙")


def local_iso_from_epoch(value: object) -> str:
    """库里那一格 epoch（**毫秒**，这台现核 178 扇全是 integer）→ 带本地时区的 ISO。

    形状要与微信那侧 `/api/v1/session` 的 `nTime`（`2026-10-26T15:48:03+08:00`）**一致**：
    两族共用一份名单组件，时间给两种形状就是让界面猜一次。认不出来就回空串
    （空串在界面上排到最后、什么时间都不画），不拿"现在"去顶一个假读数。
    """
    from datetime import datetime

    try:
        number = int(str(value or "").strip())
    except (TypeError, ValueError):
        return ""
    if number <= 0:
        return ""
    seconds = number / 1000 if number > 100_000_000_000 else float(number)
    try:
        return datetime.fromtimestamp(seconds).astimezone().isoformat(timespec="seconds")
    except (OSError, OverflowError, ValueError):
        return ""


def list_conversations(store: LocalStore, *, limit: int = 200) -> list[dict]:
    """面上/排障用的那份名单：**只有 cid、标题、群还是私聊、最后一条时间**，不取正文。

    `type` 那一格交的是**名单那一档的词**（`group`/`private`，见
    `chatlog/client.py` 的 `ROSTER_KINDS` 那段——两族共用一张词表，别各造一套）。
    `last_at` 是归一过的 ISO；`last_modify` 保留库里那个原始数（排障要看它到底存了什么）。
    **这一格不是"最后一条"的权威时间**：读的是主库，最新那几分钟可能还在 `-wal` 里
    （为什么没合并见本档顶部那段实测）。
    """
    rows = store.connection.execute(
        "SELECT cid, title, type, lastModify FROM tbconversation ORDER BY lastModify DESC LIMIT ?",
        (limit,)).fetchall()
    return [{"cid": str(cid), "title": str(title or ""),
             "type": "group" if int(t or 0) == 2 else "private",
             "last_at": local_iso_from_epoch(last), "last_modify": str(last or "")}
            for cid, title, t, last in rows]


TEXT_TYPE = 1
#: 这些类型的正文这一版**不解析**（卡片/文件/图片/小程序各是一套自己的 JSON），
#: 但行照样进池——只是 `text` 留空、`content_type` 说清是哪一类。把"没解析"写成"没有内容"
#: 就是下一颗假读数。
KNOWN_TYPES = {1: "text", 2: "image", 102: "reply", 501: "file", 2950: "card", 3100: "miniapp"}


def recent_messages(store: LocalStore, cids: Iterable[str], *, limit: int = 500,
                    since_ms: int = 0) -> list[dict]:
    """**只读被点名的那几扇会话**。一条都没点名就一条都不取（隐私默认关在这一层的落点）。"""
    wanted = [cid for cid in cids if str(cid).strip()]
    if not wanted:
        return []

    shards = [row[0] for row in store.connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'tbmsg\\_%' ESCAPE '\\'"
    ).fetchall()]
    out: list[dict] = []
    for shard in shards:
        placeholders = ",".join("?" for _ in wanted)
        try:
            rows = store.connection.execute(
                f'SELECT cid, mid, senderId, createdAt, contentType, content, recallStatus, atIds '
                f'FROM "{shard}" WHERE cid IN ({placeholders}) AND createdAt > ? '
                f'ORDER BY createdAt LIMIT ?', (*wanted, since_ms, limit)).fetchall()
        except sqlite3.Error:
            continue
        for cid, mid, sender, created, content_type, content, recall, at_ids in rows:
            if int(recall or 0):
                continue  # 已撤回的不再进池
            out.append({"cid": str(cid), "mid": str(mid), "sender_id": str(sender or ""),
                        "created_at": str(created or ""), "content_type": int(content_type or 0),
                        "text": _text_of(content, int(content_type or 0)),
                        "at_ids": str(at_ids or ""), "shard": shard})
    out.sort(key=lambda row: row["created_at"])
    return out[:limit]


def _text_of(content: Any, content_type: int) -> str:
    if content_type != TEXT_TYPE or not content:
        return ""
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError):
        return ""
    if isinstance(parsed, dict):
        return str(parsed.get("text") or "")
    return ""


def copy_out(store_db: Path, destination: Path) -> Path:
    """给排障用的一手：把**已解密的副本**搬去指定位置（源文件永远不经这一手）。"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(store_db, destination)
    return destination
