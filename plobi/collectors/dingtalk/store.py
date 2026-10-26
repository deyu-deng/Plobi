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

WAL 那一格：这一版**接了合并**（`_merge_wal_pages`）。前一版的说法是"合了也一样"，那是错的判断，
2026-10-26 用同一枚库重测：不合 ⇒ 聊天 156 条、最新停在 10-24；按提交组合并 ⇒ **246 条、
最新 10-26 21:54**，`PRAGMA integrity_check` 仍 `ok`。`calendar_v2.db` 更直接：基础库只有一页、
一张表都开不出来，21 张表（`calendar` 6 行、`calendar_event` 6 行）全在 `-wal` 里。
形状上的实测结论也记在这里，免得下一个人重走：**WAL 头与帧头是明文，帧里的页数据是密文，
而帧校验和不按 SQLite 那套累加和算**——所以合并的依据只能是盐值 + 提交标记。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

#: 推导常数：`PBKDF2-HMAC-SHA1(uid+salt, "666DingT", 1000, 32)` → `MD5(...)` 的**十六进制前 16 个
#: 字符本身**当 AES-128 密钥（ASCII 文本，不是那 16 字节原始摘要——这一点我实测错过：原始摘要那一支不通）。
PBKDF2_SALT = b"666DingT"
PBKDF2_ITERATIONS = 1000
PAGE = 4096
#: SQLite WAL 的两个魔数（末位那一位说校验和按大端还是小端算；这一版不算校验和，见 `_merge_wal_pages`）
_WAL_MAGICS = (0x377F0682, 0x377F0683)
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
    页 0 就是加密的 SQLite 魔法），并把那一枚 `-wal` 按提交组合并进副本。返回写出的页数。

    为什么必须合 WAL（这台 10-26 实测）：客户端把最新那批写入留在 `-wal` 里没checkpoint——
    不合的话聊天 156 条、最新一条停在 10-24，合完 **246 条、最新 10-26 21:54**，
    `PRAGMA integrity_check` 仍是 `ok`。`calendar_v2.db` 更狠：基础库就一页（0 张表），
    21 张表全在 WAL 里。
    """
    data = source.read_bytes()
    if not data:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")
        return 0
    page = _page_size_of(_decrypt_block(key, data[:min(len(data), PAGE) - min(len(data), PAGE) % 16]))
    pages: dict[int, bytes] = {}
    for offset in range(0, len(data) - page + 1, page):
        pages[offset // page + 1] = _decrypt_block(key, data[offset:offset + page])
    total = len(pages)
    wal = Path(str(source) + "-wal")
    if wal.is_file():
        try:
            total = _merge_wal_pages(pages, wal.read_bytes(), key, page, total)
        except (OSError, ValueError, struct.error):
            pass  # WAL 读不成就先交基础库那一版：少几帧比整族读不到好，也不该在这里抛
    target.parent.mkdir(parents=True, exist_ok=True)
    if 1 in pages:
        # 合完就把副本的"WAL 模式"那两个字节改回普通日志（页 1 头第 18、19 字节）：
        # 留着 2 的话 SQLite 连只读连接都要去建 `-wal`/`-shm`，于是我们的解密目录里
        # 长出一枚盐值对不上的空 WAL（这台 10-26 在 `PLOBI_HOME` 里现读到过），下一次读它的人
        # 就要再猜一次那枚 WAL 算不算数。副本本身已经含全部提交页，改这两个字节是无损的。
        pages[1] = pages[1][:18] + b"\x01\x01" + pages[1][20:]
    with target.open("wb") as handle:
        for number in range(1, total + 1):
            handle.write(pages.get(number, b"\x00" * page))
    # 副本自己那对 -wal/-shm 必须清掉：上一版留下过一枚对不上盐值的 WAL，留着就是让 SQLite 再猜一次
    for suffix in ("-wal", "-shm"):
        Path(str(target) + suffix).unlink(missing_ok=True)
    return total


def _page_size_of(head: bytes) -> int:
    """页长在解密后的页 1 头第 16 字节那两个字节里（值 1 表示 65536）。

    **只认 512–65536 之间的 2 的幂**，否则回 `PAGE`：钥匙不对时那两格是垃圾，
    垃圾值（比如 4660）不是 16 的倍数，一路切下去会在 AES 那里抛 `ValueError`——
    而"钥匙不对"应当是一句人话（`LocalStoreUnavailable`），不是一颗崩在解密里的异常。
    """
    try:
        raw = struct.unpack(">H", head[16:18])[0] if len(head) >= 18 else 0
    except struct.error:
        return PAGE
    size = 65536 if raw == 1 else raw
    return size if 512 <= size <= 65536 and size & (size - 1) == 0 else PAGE


def _merge_wal_pages(pages: dict[int, bytes], wal: bytes, key: bytes, page: int,
                     base_pages: int) -> int:
    """把 `-wal` 的帧按**提交组**盖进页表（就地改 `pages`），返回合并后的总页数。

    DingTalk 那个 VFS 的形状是实测的：**帧头与 WAL 头是明文，帧里的页数据是密文，
    而帧校验和不按 SQLite 那套累加和算**（10-26 逐帧对过：对密文算、对明文算都不对）。
    所以判"这一帧能不能要"的依据换成两条：盐值与 WAL 头一致 + 只在 `dbsize>0` 那帧落定一笔事务，
    **尾巴上没收完的那组整个丢掉**——不丢就会合出一棵引用了空页的树（`invalid page number`）。
    """
    if len(wal) < 32:
        return base_pages
    magic, _version, wal_page, _ckpt, salt_1, salt_2, _c0, _c1 = struct.unpack(">8I", wal[:32])
    if magic not in _WAL_MAGICS or wal_page != page:
        return base_pages
    total = base_pages
    pending: dict[int, bytes] = {}
    offset = 32
    while offset + 24 + page <= len(wal):
        header = wal[offset:offset + 24]
        page_no, db_size, frame_salt_1, frame_salt_2 = struct.unpack(">4I", header[:16])
        if (frame_salt_1, frame_salt_2) != (salt_1, salt_2):
            break  # 换了一代 WAL（checkpoint 之后盐值会重开），后面那些不属于这一份
        pending[page_no] = _decrypt_block(key, wal[offset + 24:offset + 24 + page])
        if db_size:  # 这一帧是一笔事务的最后一帧：整组此刻才可信
            pages.update(pending)
            pending = {}
            total = max(total, db_size)
        offset += 24 + page
    return total


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
    读的是**合过 `-wal` 的那份副本**，所以"最新几分钟还在 WAL 里没进主库"这一条不再是缺口
    （为什么必须合，见本档顶部那段实测）。
    """
    rows = store.connection.execute(
        "SELECT cid, title, type, lastModify FROM tbconversation ORDER BY lastModify DESC LIMIT ?",
        (limit,)).fetchall()
    return [{"cid": str(cid), "title": str(title or ""),
             "type": "group" if int(t or 0) == 2 else "private",
             "last_at": local_iso_from_epoch(last), "last_modify": str(last or "")}
            for cid, title, t, last in rows]


TEXT_TYPE = 1
#: 非文本那几类各自**实测**长什么样（10-26 逐类扒过这台 155 条样本，键路径抄在下面）。
#: 一张表说清"哪些已经解得出、哪些还解不出"——把"没解析"写成"没有内容"就是下一颗假读数。
#: 屏上不许出现英文键名，所以另有一张给人看的中文名词表（`content_label`）。
KNOWN_TYPES = {1: "text", 2: "image", 102: "link", 500: "space_file", 501: "file",
               1200: "markdown", 1201: "action_card", 1600: "ding", 2900: "card",
               2950: "card", 3100: "miniapp"}
_LABELS = {"text": "消息", "image": "图片", "link": "链接", "space_file": "群文件",
           "file": "文件", "markdown": "卡片", "action_card": "卡片", "ding": "DING",
           "card": "卡片", "miniapp": "小程序", "other": "非文本消息"}
#: 每一类先取哪几个字段当"这句话"（顺序 = 实测里哪个字段真是人看的那一格；
#: `LastMessageI18n` 是互动卡片那句"最后一条"的多语言表，`_pick` 那一头再挑一种语言）。
_TEXT_KEYS = ("title", "LastMessageI18n", "text", "f_name", "dingContent", "biz_custom_title",
              "interactiveCardLastMessage", "searchDesc", "desc", "markdown", "cname")
_I18N_KEYS = ("zh_CN", "zh-CN", "zh", "zh_Hans", "en_US", "default")


def content_label(content_type: int) -> str:
    """给人看的那一个名词（屏上原来打的是 `（card）` 这种英文键名）。"""
    return _LABELS.get(KNOWN_TYPES.get(int(content_type or 0), "other"), _LABELS["other"])


def _loads(raw: Any) -> Any:
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _pick(node: Any) -> str:
    """把"这一格到底是不是给人看的那句"这件事一次算清：dict 走语言表，字符串可能是又一层 JSON。"""
    if isinstance(node, str) and node[:1] in "[{":
        node = _loads(node)
    if isinstance(node, dict):
        for key in _I18N_KEYS:
            if isinstance(node.get(key), str) and node[key].strip():
                return node[key].strip()
        return next((value.strip() for value in node.values()
                     if isinstance(value, str) and value.strip()), "")
    return node.strip() if isinstance(node, str) else ""


def _human_size(raw: Any) -> str:
    try:
        number = float(str(raw).strip())
    except (TypeError, ValueError):
        return ""
    if number <= 0:
        return ""
    for unit, edge in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if number >= edge:
            return f"{number / edge:.1f} {unit}"
    return f"{int(number)} B"


def _attachment_texts(parsed: dict) -> list[str]:
    """`attachments[].extension` 那一层套娃是这台的真形状：外面是列表，每格再解一次 JSON。"""
    out: list[str] = []
    for item in parsed.get("attachments") or []:
        if not isinstance(item, dict):
            continue
        body = _loads(item.get("extension")) if isinstance(item.get("extension"), str) else item.get("extension")
        if not isinstance(body, dict):
            body = {}
        name = next((_pick(body.get(key)) for key in _TEXT_KEYS if _pick(body.get(key))), "")
        if name:
            size = _human_size(body.get("f_size") or item.get("size"))
            out.append(f"{name}（{size}）" if size else name)
    return out


def describe_of(content: Any, content_type: int) -> str:
    """这一条消息**给人读的那一句**。解析不出来就回空串，由调用方落回中文名词。

    文本类仍是库里那句原文（一个字节不改）；其余每一类只从实测有的那几格取，
    不猜、不编——猜出来的正文是假读数，比空白更贵。
    """
    parsed = _loads(content)
    if not isinstance(parsed, dict):
        return ""
    if int(content_type or 0) == TEXT_TYPE:
        return str(parsed.get("text") or "")
    pieces = _attachment_texts(parsed)
    if not pieces:  # 图片那一类把名字与尺寸放在**顶层**，不在 attachments 里
        name = next((_pick(parsed.get(key)) for key in ("filename", "title", "text", "nick")
                     if _pick(parsed.get(key))), "")
        size = _human_size(parsed.get("picSize") or parsed.get("size"))
        if name:
            pieces = [f"{name}（{size}）" if size else name]
    return " ".join(pieces[:3])


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
                        "text": describe_of(content, int(content_type or 0)),
                        "at_ids": str(at_ids or ""), "shard": shard})
    out.sort(key=lambda row: row["created_at"])
    return out[:limit]


def copy_out(store_db: Path, destination: Path) -> Path:
    """给排障用的一手：把**已解密的副本**搬去指定位置（源文件永远不经这一手）。"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(store_db, destination)
    return destination
