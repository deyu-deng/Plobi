"""Read-only HTTP client for the local chatlog service (127.0.0.1:5030).

chatlog decrypts the WeChat database locally; we only ever read, and only for
whitelisted talkers. Response shapes vary between chatlog versions, so the
normalizer is deliberately tolerant and every field has a fallback.
"""

from __future__ import annotations

import hashlib
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0


class ChatlogUnavailable(RuntimeError):
    """chatlog is not reachable (service down, WeChat logged out, …)."""


class MediaNotFound(ChatlogUnavailable):
    """侧车回 404：**这一件它现在还不知道**。

    与「侧车没了」是两件事，必须分得开（10-28 实测到根因为止）：引擎的自动解密只吃
    `fsnotify` 的实时事件，微信把媒体索引的新行先写在 `hardlink.db-wal` 里，
    checkpoint 进主文件那一刻才触发重解——所以侧车读到的那份索引**永远滞后**，
    比它上次启动/上次 checkpoint 更新的那几件就是 404。字节这一腿本身是通的
    （索引里有的那几枚，实测回真 JPEG）。
    """


#: 引擎回的那一格里，非文本消息的**键藏在 `contents` 里**（不在 `content` 那句人话里）。
#: 下面这张表只登记本机量到过的形状：type 3=图、43=视频、34=语音、49=文件/链接/合并转发
#: （49 的 `subType` 5 是链接分享、57 是引用——引用里还套着一层 `contents`，那是被引的那条自己）。
#: 没有量到的写法一律交回空表，不猜一个键名出来（"键名不是语义"那条口径）。
_MEDIA_TYPES: dict[str, str] = {"3": "image", "34": "voice", "43": "video"}
_KEYS_BY_KIND: dict[str, tuple[str, ...]] = {
    "image": ("md5",),
    "video": ("md5", "rawmd5"),
    "voice": ("voice",),
    "file": ("md5", "filekey"),
}


def _one_media(kind: str, node: dict) -> Optional[dict]:
    keys = _KEYS_BY_KIND.get(kind, ())
    for name in keys:
        value = str(node.get(name) or "").strip()
        if value:
            return {"kind": kind, "key": value, "key_name": name,
                    "path": str(node.get("path") or ""), "name": str(node.get("title") or "")}
    return None


def media_of(raw: dict) -> list[dict]:
    """一条聊天记录里的媒体件（0..n 个）。返回 `[{kind, key, key_name, path, name}, …]`。

    `key` 就是引擎那四只口（`/image/{key}`、`/video/{key}`、`/voice/{key}`、`/file/{key}`）
    吃的那个键：图与视频是 **md5 的十六进制串**，语音是它自己的 voice id。
    """
    out: list[dict] = []
    contents = raw.get("contents")
    if not isinstance(contents, dict):
        return out

    kind = _MEDIA_TYPES.get(str(raw.get("type")))
    if kind:
        found = _one_media(kind, contents)
        if found:
            out.append(found)

    # 49 那一族里文件与"引用别条消息"都可能出现：文件直接取，引用套一层递归一次（只一层，防回环）。
    if str(raw.get("type")) == "49":
        file_found = _one_media("file", contents)
        if file_found:
            out.append(file_found)
        refer = contents.get("refer")
        if isinstance(refer, dict):
            nested = media_of({"type": str(refer.get("type")), "contents": refer.get("contents")})
            for item in nested:
                item["from_refer"] = True
                out.append(item)

    return out


@dataclass(frozen=True)
class ChatMessage:
    msg_id: str
    talker: str
    sender: str
    sent_at: str
    content: str
    # Whether the message was sent by the account owner, when chatlog tells us
    # (``isSend`` / ``isSelf`` / …). ``None`` means "chatlog did not say" — we
    # report that honestly rather than guessing, because "who sent it" is the
    # whole point of the extraction (WP-EXTRACT-CONTEXT).
    is_self: Optional[bool] = None
    #: 这条消息带的媒体件（`media_of` 的量法）。空元组是一个读数：这条没有媒体，
    #: 或这一版的 `contents` 长得不认识——两种都**不许**被读成"取失败了"。
    media: tuple[dict, ...] = ()

    @property
    def is_empty(self) -> bool:
        """"这条能不能当一句话读"。**故意不看 `media`**：媒体那一条腿今天还没接进落点，
        把只有图没有字的消息放进现有那条链，会得到一批空正文的行——那是假"采到了"。
        接媒体那一只口的时候，这一格与它的调用方一起改（见 `tasks/wechat-media-task.md` §1 第 5 条）。
        """
        return not self.content.strip()


@dataclass(frozen=True)
class TalkerSession:
    """One conversation as chatlog's session list reports it.

    ``id`` is the talker id accepted by ``/api/v1/chatlog`` (``topicId`` /
    ``personID``); ``name`` is the human-readable label chatlog already
    carries (``topicName`` — group name or contact display name). It is the
    id when chatlog has no better label, so callers can render it verbatim.
    """

    id: str
    name: str


def _stable_id(talker: str, sent_at: str, content: str) -> str:
    digest = hashlib.sha1(f"{talker}|{sent_at}|{content}".encode("utf-8")).hexdigest()
    return f"cl_{digest[:16]}"


def _first(payload: dict, *names: str) -> Any:
    for name in names:
        if name in payload and payload[name] not in (None, ""):
            return payload[name]
    return None


# chatlog spells "did the account owner send this?" several ways across
# versions; accept any of them and read the value honestly.
_IS_SELF_KEYS = ("isSend", "isSelf", "is_self", "is_send", "send")
_IS_SELF_TRUE = {"true", "1", "yes", "y", "是", "self", "me", "自己"}
_IS_SELF_FALSE = {"false", "0", "no", "n", "否", "不是", "other", "别人", "他人"}


def _as_bool(value: Any) -> Optional[bool]:
    """Coerce a chatlog flag to a real bool, or ``None`` when it is not one.

    Returning ``None`` for anything ambiguous (an unknown string, a nested
    object) keeps us from inventing an answer we do not actually have.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _IS_SELF_TRUE:
            return True
        if text in _IS_SELF_FALSE:
            return False
    return None


def _parse_is_self(raw: dict) -> Optional[bool]:
    for key in _IS_SELF_KEYS:
        if key in raw:
            parsed = _as_bool(raw[key])
            if parsed is not None:
                return parsed
    return None


#: 裁 187① 那两列在采集侧的取值口径。**不新拍判据**：群聊的 username 一律带 `@chatroom`
#: （这台现读 `56085102642@chatroom` 这类），其余都是 1:1 那一类。`conversation` 就是
#: 那一扇窗口的 username（`/api/v1/session` 的 `SessionTable.username`，即 `TalkerSession.id`）。
GROUP_USERNAME_SUFFIX = "@chatroom"


def pool_columns(talker: Any) -> dict[str, str]:
    """这一条**本来该带上**的两列：`conversation` = 窗口 username，`visibility` = 私聊／群聊。

    拿不到 username 就返回两个空串——空串在池里的意思是「采它那一轮没记窗口」，
    **不是**「不属于任何窗口」，所以宁空勿猜（`plobi/pool/store.py` 顶上钉的就是这句）。
    `gh_xxx`／`brandsessionholder` 这两类既不是群、严格说也不是一对一私聊：
    按「别拍一个新的」先归 `private`，要不要开第三个值由池那一侧定，不在这儿偷偷加。
    """
    name = str(talker or "").strip()
    if not name:
        return {"visibility": "", "conversation": ""}
    return {
        "visibility": "group" if name.endswith(GROUP_USERNAME_SUFFIX) else "private",
        "conversation": name,
    }


def normalize_message(raw: Any, *, fallback_talker: str = "") -> Optional[ChatMessage]:
    """Map one chatlog record onto :class:`ChatMessage`, or ``None`` if unusable."""
    if not isinstance(raw, dict):
        return None

    content = _first(raw, "content", "Content", "msg", "message", "text")
    content = "" if content is None else str(content).strip()
    media = tuple(media_of(raw))
    if not content and not media:
        # 既没字也没件才丢。**只有图没有字的一条要留得下来**——否则媒体那一腿永远拿不到东西；
        # 而"能不能当一句话读"是另一格（`ChatMessage.is_empty`），现有那条链照旧不看媒体。
        return None

    talker = str(_first(raw, "talker", "Talker", "chatroom", "roomId") or fallback_talker or "")
    sender = str(_first(raw, "senderName", "sender", "Sender", "nickname", "from") or "")
    sent_at = str(_first(raw, "time", "Time", "createTime", "timestamp", "date") or "")

    raw_id = _first(raw, "id", "msgId", "MsgId", "seq", "Seq")
    msg_id = f"cl_{raw_id}" if raw_id is not None else _stable_id(talker, sent_at, content)

    return ChatMessage(
        msg_id=msg_id,
        talker=talker,
        sender=sender,
        sent_at=sent_at,
        content=content,
        is_self=_parse_is_self(raw),
        media=media,
    )


def _extract_records(payload: Any) -> Iterable[Any]:
    """chatlog answers with a bare list or a wrapper object depending on version."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "messages", "items", "list", "result"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


class ChatlogClient:
    def __init__(self, base_url: str, *, timeout: float = DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # Seam for tests: everything network-facing funnels through here.
    def _get(self, path: str, params: dict[str, str]) -> Any:
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise MediaNotFound(f"chatlog has no such media: {path}") from exc
            raise ChatlogUnavailable(f"chatlog request failed: {exc}") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise ChatlogUnavailable(f"chatlog request failed: {exc}") from exc

        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise ChatlogUnavailable(f"chatlog returned non-JSON: {body[:120]}") from exc

    def media_info(self, kind: str, key: str) -> dict:
        """`/{kind}/{key}?info=1` → 那一件自己的元数据（`{type,key,path,name,size,modifyTime}`）。

        键是 `media_of` 交出来的那一个（图/视频＝md5 十六进制串，语音＝voice id）。
        """
        payload = self._get(f"/{kind}/{urllib.parse.quote(str(key), safe='')}", {"info": "1"})

        return payload if isinstance(payload, dict) else {}

    def media_bytes(self, kind: str, key: str) -> tuple[bytes, str]:
        """取回那一件的字节，返回 `(bytes, content_type)`。

        引擎那头会 302 到 `/data/{path}`，`.dat` 由它自己解成真的图/视频——**我们不碰密钥**，
        解密的活儿一直在侧车那一侧（这一只口只是把它已经解好的东西搬回来）。
        """
        return self._get_bytes(f"/{kind}/{urllib.parse.quote(str(key), safe='')}")

    def _get_bytes(self, path: str) -> tuple[bytes, str]:
        # Seam for tests: every byte-facing read funnels through here.
        url = f"{self.base_url}{path}"
        request = urllib.request.Request(url, headers={"Accept": "*/*"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read(), response.headers.get("Content-Type") or ""
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise MediaNotFound(f"chatlog has no such media: {path}") from exc
            raise ChatlogUnavailable(f"chatlog media fetch failed: {exc}") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise ChatlogUnavailable(f"chatlog media fetch failed: {exc}") from exc

    def healthy(self) -> bool:
        # chatlog v0.5.2 exposes only /health; older/newer builds may differ.
        try:
            self._get("/health", {})
        except ChatlogUnavailable:
            return False
        return True

    def fetch(self, talker: str, day: Optional[date] = None) -> list[ChatMessage]:
        """Fetch one talker's messages for one day.

        ``time`` and ``talker`` are both mandatory in the chatlog API; omitting
        the end date means "that single day".
        """
        if not talker:
            raise ValueError("talker is required — the whitelist must resolve first")

        target = (day or datetime.now().date()).isoformat()
        payload = self._get(
            "/api/v1/chatlog",
            {"time": target, "talker": talker, "format": "json"},
        )

        messages: list[ChatMessage] = []
        for raw in _extract_records(payload):
            message = normalize_message(raw, fallback_talker=talker)
            if message is not None and not message.is_empty:
                messages.append(message)
        return messages

    def list_talker_sessions(self, keyword: str = "", limit: int = 10000) -> list[TalkerSession]:
        """Enumerate conversations with their display names.

        chatlog exposes ``/api/v1/session`` which returns recent conversations;
        each item's ``topicId`` (or ``personID``) is the talker id accepted by
        ``/api/v1/chatlog``, and ``topicName`` is the human-readable label
        (group name / contact display name). Older builds used ``UserName`` /
        ``nickName`` — accept either. Order is preserved (most recent first).
        """
        try:
            payload = self._get(
                "/api/v1/session",
                {"format": "json", "keyword": keyword, "limit": str(limit)},
            )
        except ChatlogUnavailable:
            return []

        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return []

        seen: set[str] = set()
        result: list[TalkerSession] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            # chatlog v0.5.2 session items identify a conversation by `topicId`
            # (chatrooms) or `personID` (1:1 contacts); older builds used
            # `UserName`. Accept any of the three.
            uname = (
                item.get("topicId")
                or item.get("UserName")
                or item.get("userName")
                or item.get("talker")
                or item.get("personID")
            )
            if not uname or uname in seen:
                continue
            seen.add(uname)
            talker_id = str(uname)
            # Real display name from chatlog, falling back to the id so the
            # board never renders a blank row.
            label = (
                item.get("topicName")
                or item.get("remark")
                or item.get("nickName")
                or item.get("nickname")
                or talker_id
            )
            name = str(label).strip() or talker_id
            result.append(TalkerSession(id=talker_id, name=name))
        return result

    def list_talkers(self, keyword: str = "", limit: int = 10000) -> list[str]:
        """De-duplicated talker ids (order preserved). See ``list_talker_sessions``."""
        return [session.id for session in self.list_talker_sessions(keyword, limit)]
