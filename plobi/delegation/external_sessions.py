"""外部软件自己的会话：自动发现、按 L2 归属过滤、读得到、说得上话（WP-EXTERNAL-SESSIONS）。

用户 10-27 要的是「L2 拥有和用户一样的会话列表」：自动认出本机上各家 AI 软件的对话，
由 L2 自己判断哪条与它有关，然后在左栏按应用分组展开。

**只在 Plobi 这一侧做**：Qoder 桌面 App 的索引是另一套，实测不认 CLI 写的会话，
所以不假装能驱动它的窗口。

已核过的存放形状（10-27 现读，11-08 再核一遍）：

- ``qoder``      ``~/.qoder/projects/<项目键>/<会话号>.jsonl`` + ``<会话号>/state.json``
- ``codebuddy``  ``~/.codebuddy/projects/<项目键>/<会话号>.jsonl``（记录顶层带 ``cwd``）
- ``marvis``     ``~/.marvis/database/data.db`` 的 ``conversations`` / ``messages`` 两张表（只读）
- ``cursor``     ``state.vscdb`` 里的 ``composerData:<id>``（只读）——**这台机器上没留下可读的对话**
  （库在，6 条 composer 全是草稿或已归档；说「这台没留下」，不说「它没有对话」）
- ``agy``        ``~/.gemini/antigravity-cli/brain/<会话号>/…/transcript.jsonl``（只读）

两处**照实**（不许猜）：

- **Marvis 不记这条会话在哪个目录**：库里（``conversations.metadata``／``messages.metadata``／
  工具调用参数）三处都没有工作目录这一格，AppData 下那圈目录里也只有产物 ⇒ ``project`` 留空，
  归属留给 L2 按标题判（``related=None``＝待判断），**不从产物名反推**（那是猜）。
- **Cursor 在这台机器上没留下可读的对话** ⇒ 来源那一格照实写「没留下」，不假装读得到。

三条判据：

- 发现：扫每家的固定根目录，不扫盘；项目路径只从会话自己的记录里读，或从名册登记里比。
- 过滤：L2 的三个来源依次是「它自己标过的（sidecar）」→「项目目录命中」→「标题里点到它的名字」；
  会话自己写明在**别的目录**的记 ``related=False``（这是筛完，不是待判），
  只有连目录都没有（如 Marvis）的才留 ``None``（待判断），不冒充「不相关」。
- 说：``qodercli --resume <id> -p <text>`` —— 这条旗标实测过（10-23、10-27 各一次真回话）。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

VENDOR_QODER = "qoder"
VENDOR_CODEBUDDY = "codebuddy"
VENDOR_MARVIS = "marvis"
VENDOR_CURSOR = "cursor"
VENDOR_AGY = "agy"

#: 这一版能真的把话送进去的只有 Qoder：其余两家只读。不是漏做，是没实测过回跳旗标。
#: 能力表在 `external_capabilities.py` 里，这里只留一份**兼容旧调用**的派生集合。
def _known_vendors() -> tuple[str, ...]:
    from plobi.delegation.external_capabilities import vendor_names

    return vendor_names()


def capabilities_for(vendor: str):
    """这一家的能力位；不认识就返回 None。"""
    from plobi.delegation.external_capabilities import capabilities

    return capabilities(vendor)


#: 能真的把话送进去的那几家从能力表派生，不在这里再列第二份。
#: 今天只有 Qoder；其余只读，不是漏做，是没实测过回跳旗标。
SENDABLE = frozenset(
    v for v in _known_vendors()
    if (spec := capabilities_for(v)) is not None and spec.send
)

#: 会话号会拼进文件名/argv/DB 查询，先于任何拼接校一遍。``_`` 留给 Marvis 的 ``conv_…``。
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

MAX_MESSAGE_CHARS = 8000
MAX_MESSAGES = 300

#: 读回来的正文里，**单条**的上限（Marvis 有几万字的一条，整段塞进列表页会卡）。
MAX_TRANSCRIPT_CHARS = 8000
#: 截断时留的那句人话（只说「截了」，不解释机制）。
TRUNCATED_MARK = "……（后面截断了）"


# ── 各家的根目录 ──────────────────────────────────────────────────────────

def qoder_root() -> Path:
    override = os.environ.get("QODER_CONFIG_DIR", "").strip()
    base = Path(os.path.expanduser(override)) if override else Path.home() / ".qoder"

    return base / "projects"


def codebuddy_root() -> Path:
    override = os.environ.get("CODEBUDDY_CONFIG_DIR", "").strip()
    base = Path(os.path.expanduser(override)) if override else Path.home() / ".codebuddy"

    return base / "projects"


def marvis_db() -> Path:
    override = os.environ.get("MARVIS_HOME", "").strip()
    base = Path(os.path.expanduser(override)) if override else Path.home() / ".marvis"

    return base / "database" / "data.db"


def cursor_root() -> Path:
    return Path.home() / ".cursor" / "projects"


def cursor_db() -> Path:
    """Cursor 的 composer 存在 VS Code 那套 ``state.vscdb`` 里（只读打开）。"""
    override = os.environ.get("CURSOR_HOME", "").strip()
    if override:
        return Path(os.path.expanduser(override)) / "User" / "globalStorage" / "state.vscdb"
    candidates = []
    for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("APPDATA")):
        if base:
            candidates.append(Path(base) / "Cursor" / "User" / "globalStorage" / "state.vscdb")
    candidates.append(Path("D:/Data/AppData/Cursor/User/globalStorage/state.vscdb"))
    candidates.append(Path.home() / ".cursor" / "User" / "globalStorage" / "state.vscdb")
    for candidate in candidates:
        if candidate.is_file():
            return candidate

    return candidates[0]


def agy_db() -> Path:
    """agy（Antigravity CLI）的会话摘要库。"""
    override = os.environ.get("AGY_HOME", "").strip()
    base = Path(os.path.expanduser(override)) if override else Path.home() / ".gemini" / "antigravity-cli"

    return base / "conversation_summaries.db"



def project_key(path: str) -> str:
    """绝对路径 → 那两家按项目分目录的键（所有非字母数字都变 ``-``）。"""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path or "").strip())


# ── 通用小工具 ────────────────────────────────────────────────────────────

def _records(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    out.append(record)
    except OSError:
        return []

    return out


def _records_head(path: Path, limit: int = 200) -> list[dict[str, Any]]:
    """列表只要「标题 + 项目」：读前 200 行就够，别为了列一行把整份历史吞进来。"""
    out: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for index, line in enumerate(handle):
                if index >= limit:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    out.append(record)
    except OSError:
        return []

    return out


#: 每家会话文件的解析缓存：``path -> (mtime, meta)``。首次扫一遍，之后只看变过的文件。
_META_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_ALL_CACHE: dict[str, Any] = {"at": 0.0, "rows": []}
_ALL_TTL = 15.0


def _cached_meta(path: Path, build) -> dict[str, Any]:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return build()
    key = str(path)
    hit = _META_CACHE.get(key)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    meta = build()
    _META_CACHE[key] = (mtime, meta)

    return meta


def _block_text(content: Any) -> str:
    """一层 jsonl 里的正文：字符串、或块数组（``text`` / ``input_text`` / ``output_text``）。"""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") in ("text", "input_text", "output_text"):
                value = block.get("text")
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())

        return "\n".join(parts).strip()

    return ""


def _mtime_iso(path: Path) -> str:
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(path.stat().st_mtime))
    except OSError:
        return ""


def _local_stamp(value: Any) -> str:
    """各家的时间戳形状不一样（ISO 带时区／ISO 带 ``Z``／空格分隔），统一收成**本地时间串**。

    **为什么非转不可**：排序是拿字符串比的（屏上也是按字符串排的），带时区偏移的 ISO 串
    与本地串混在一起比，时区那一截会把序比错——Marvis 的 ``+00:00`` 串就是这样排反的。
    读不出来就原样返回（宁可原样，不编一个时间）。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return text
    if moment.tzinfo is not None:
        moment = moment.astimezone()

    return moment.strftime("%Y-%m-%dT%H:%M:%S")


def _clamp_text(text: str, limit: int = MAX_TRANSCRIPT_CHARS) -> str:
    """超长的一条截掉（几万字的一条会卡住列表页）。"""
    if len(text) <= limit:
        return text

    return text[:limit] + TRUNCATED_MARK


def _entry(directory: Path, jsonl: Path, *, project: str, title: str, updated: str,
           sendable: bool) -> dict[str, Any]:
    return {
        "id": jsonl.stem,
        "project": project,
        "sendable": sendable,
        "title": (title or jsonl.stem).splitlines()[0][:120],
        "updated_at": _local_stamp(updated) or _mtime_iso(jsonl),
        "vendor": "",
    }


# ── Qoder ─────────────────────────────────────────────────────────────────

def _qoder_project(records: Sequence[dict[str, Any]]) -> str:
    for record in records:
        if record.get("type") == "workspace-directories":
            directories = record.get("directories")
            if isinstance(directories, list):
                for item in directories:
                    if isinstance(item, str) and item.strip():
                        return item.strip()

    return ""


def _qoder_title(records: Sequence[dict[str, Any]]) -> str:
    for record in records:
        if record.get("type") != "user":
            continue
        message = record.get("message")
        text = _block_text(message.get("content") if isinstance(message, dict) else record.get("content"))
        if text:
            return text

    return ""


def _qoder_updated(session_dir: Path, jsonl: Path) -> str:
    try:
        payload = json.loads((session_dir / "state.json").read_text(encoding="utf-8"))
        value = payload.get("updatedAt") or payload.get("createdAt")
        if isinstance(value, str) and value.strip():
            return value.strip()
    except (OSError, json.JSONDecodeError):
        pass

    return _mtime_iso(jsonl)


def _qoder_sessions() -> list[dict[str, Any]]:
    root = qoder_root()
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for directory in root.iterdir():
        if not directory.is_dir():
            continue
        for jsonl in directory.glob("*.jsonl"):
            if not SESSION_ID_RE.match(jsonl.stem):
                continue

            def build(jsonl=jsonl, directory=directory) -> dict[str, Any]:
                records = _records_head(jsonl)
                project = _qoder_project(records)

                return {
                    **_entry(directory, jsonl, project=project, title=_qoder_title(records),
                             updated=_qoder_updated(directory / jsonl.stem, jsonl), sendable=bool(project)),
                    "vendor": VENDOR_QODER,
                }

            out.append(_cached_meta(jsonl, build))

    return out


# ── CodeBuddy ─────────────────────────────────────────────────────────────

def _codebuddy_sessions() -> list[dict[str, Any]]:
    root = codebuddy_root()
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for directory in root.iterdir():
        if not directory.is_dir():
            continue
        for jsonl in directory.glob("*.jsonl"):
            if not SESSION_ID_RE.match(jsonl.stem):
                continue

            def build(jsonl=jsonl) -> dict[str, Any]:
                records = _records_head(jsonl)
                project = ""
                title = ""
                for record in records:
                    if not project and isinstance(record.get("cwd"), str) and record["cwd"].strip():
                        project = record["cwd"].strip()
                    if not title and record.get("role") == "user" and record.get("type") == "message":
                        title = _block_text(record.get("content"))

                return {
                    **_entry(directory, jsonl, project=project, title=title,
                             updated=_mtime_iso(jsonl), sendable=False),
                    "vendor": VENDOR_CODEBUDDY,
                }

            out.append(_cached_meta(jsonl, build))

    return out


# ── Marvis（SQLite，只读）──────────────────────────────────────────────────

def _marvis_first_user_line(con: sqlite3.Connection, conversation_id: str) -> str:
    """Marvis 的会话名多半是**项目名**（Plobi／SRTP／Quota），不是人说的第一句话。

    屏上先看第一句话（跟别家同一形状），项目名另放一格（``source_title``），不丢信息。
    读不到第一句就交回空串，让调用方退回会话名。
    """
    try:
        row = con.execute(
            "select content from messages where conversation_id = ? and role = 'user' "
            "and content is not null order by message_seq limit 1",
            (conversation_id,),
        ).fetchone()
    except sqlite3.Error:
        return ""
    text = str(row[0] if row else "").strip()
    if not text:
        return ""

    return text.splitlines()[0][:120]


def _marvis_sessions() -> list[dict[str, Any]]:
    db = marvis_db()
    if not db.is_file():
        return []
    out: list[dict[str, Any]] = []
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "select conversation_id, title, updated_at from conversations order by updated_at desc"
            ).fetchall()
            spoken = {row[0]: _marvis_first_user_line(con, row[0]) for row in rows}
        finally:
            con.close()
    except sqlite3.Error:
        return []
    for conversation_id, title, updated_at in rows:
        if not isinstance(conversation_id, str) or not SESSION_ID_RE.match(conversation_id):
            continue
        out.append({
            "id": conversation_id,
            # Marvis **不记这条会话在哪个目录**（11-08 现读：库里的 metadata、工具调用参数、
            # AppData 下那圈目录，三处都没有工作目录这一格）⇒ 留空，归属交给 L2 按标题判。
            # **不从产物名反推**——那是猜，猜出来的目录会把「有关/无关」判反。
            "project": "",
            "sendable": False,
            "title": spoken.get(conversation_id, "") or str(title or conversation_id)[:120],
            # Marvis 自己的会话名（多半是项目名）：屏上先看 title，这一格不丢信息。
            "source_title": str(title or "").splitlines()[0][:120] if title else "",
            "updated_at": _local_stamp(updated_at),
            "vendor": VENDOR_MARVIS,
        })

    return out


# ── Cursor（state.vscdb 里的 composerData，只读）───────────────────────────

def _file_uri_to_path(uri: str) -> str:
    """``file:///d%3A/Projects/Plobi`` → ``d:\\Projects\\Plobi``；读不出就原样。"""
    from urllib.parse import unquote

    text = unquote(str(uri or "").strip())
    if text.lower().startswith("file:///"):
        text = text[8:]
    text = text.replace("/", "\\")
    if len(text) >= 2 and text[1] == ":":
        text = text[0].upper() + text[1:]

    return text


def _cursor_workspace_path(db: Path, workspace_id: str) -> str:
    if not workspace_id:
        return ""
    candidate = db.parent.parent / "workspaceStorage" / workspace_id / "workspace.json"
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    for key in ("folder", "workspace", "configuration"):
        value = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(value, str) and value.strip():
            return _file_uri_to_path(value)

    return ""


def _cursor_messages(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Cursor 的对话在 ``conversationMap`` 里；顺序以 ``fullConversationHeadersOnly`` 为准。"""
    cmap = data.get("conversationMap")
    if not isinstance(cmap, dict):
        return []
    order = [
        str(item.get("bubbleId"))
        for item in (data.get("fullConversationHeadersOnly") or [])
        if isinstance(item, dict) and item.get("bubbleId")
    ] or list(cmap.keys())
    messages: list[dict[str, Any]] = []
    for bubble_id in order:
        bubble = cmap.get(bubble_id)
        if not isinstance(bubble, dict):
            continue
        kind = bubble.get("type")
        role = "user" if kind in (1, "user") else "assistant" if kind in (2, "assistant") else ""
        if not role:
            continue
        text = bubble.get("text") or bubble.get("richText") or ""
        if isinstance(text, str) and text.strip():
            messages.append({"role": role, "text": text.strip(), "ts": ""})

    return messages


def _cursor_sessions() -> list[dict[str, Any]]:
    """Cursor 的对话在 ``composerData:<id>`` 里。

    **这台机器上读不出**（11-08 现读）：``composerHeaders`` 6 条全是 ``empty-window`` 的草稿
    或已归档，唯一那条真会话的 ``conversationMap`` 是空的 ⇒ 返回 0 条。
    这是**照实**：库在，但没有留下可读的对话——不是「它没有对话」，也不是「读坏了」。
    """
    db = cursor_db()
    if not db.is_file():
        return []
    out: list[dict[str, Any]] = []
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            headers = con.execute(
                "select composerId, workspaceId, createdAt, lastUpdatedAt, isArchived from composerHeaders"
            ).fetchall()
            for composer_id, workspace_id, created, updated, archived in headers:
                session_id = str(composer_id or "")
                if archived or not SESSION_ID_RE.match(session_id):
                    continue
                if session_id.startswith("draft-") or session_id == "empty-state-draft":
                    continue
                row = con.execute(
                    "select value from cursorDiskKV where key = ?", (f"composerData:{session_id}",)
                ).fetchone()
                if not row:
                    continue
                data = json.loads(row[0])
                messages = _cursor_messages(data)
                first_user = next((m["text"] for m in messages if m["role"] == "user"), "")
                if not first_user:
                    continue
                stamp = updated or created
                when = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(int(stamp) / 1000)) if stamp else ""
                out.append({
                    "id": session_id,
                    "project": _cursor_workspace_path(db, str(workspace_id or "")),
                    "sendable": False,
                    "title": first_user.splitlines()[0][:120],
                    "updated_at": when,
                    "vendor": VENDOR_CURSOR,
                })
        finally:
            con.close()
    except (sqlite3.Error, json.JSONDecodeError, ValueError):
        return []

    return out


# ── agy（Antigravity CLI，会话摘要库）──────────────────────────────────────

def _agy_sessions() -> list[dict[str, Any]]:
    db = agy_db()
    if not db.is_file():
        return []
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "select conversation_id, title, preview, last_modified_time, workspace_uris "
                "from conversation_summaries order by last_modified_time desc"
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []
    out: list[dict[str, Any]] = []
    for conversation_id, title, preview, updated, uris in rows:
        session_id = str(conversation_id or "")
        if not SESSION_ID_RE.match(session_id):
            continue
        project = ""
        try:
            parsed = json.loads(uris) if isinstance(uris, str) else uris
            if isinstance(parsed, list) and parsed:
                project = _file_uri_to_path(str(parsed[0]))
        except (json.JSONDecodeError, TypeError):
            project = ""
        out.append({
            "id": session_id,
            "project": project,
            "sendable": False,
            "title": str(title or preview or session_id)[:120],
            "updated_at": _local_stamp(updated),
            "vendor": VENDOR_AGY,
        })

    return out


def agy_brain_root() -> Path:
    """agy（Antigravity CLI）的会话正文根：``brain/<会话号>/.system_generated/logs/``。"""
    override = os.environ.get("AGY_HOME", "").strip()
    base = Path(os.path.expanduser(override)) if override else Path.home() / ".gemini" / "antigravity-cli"

    return base / "brain"


def _agy_user_text(content: str) -> str:
    """agy 的用户输入外面裹着几段元数据（``<USER_REQUEST>``／``<USER_SETTINGS_CHANGE>``…），
    只取人真正说的那一段；没有包裹就整段留用。"""
    text = str(content or "")
    if "<USER_REQUEST>" in text and "</USER_REQUEST>" in text:
        start = text.index("<USER_REQUEST>") + len("<USER_REQUEST>")
        end = text.index("</USER_REQUEST>", start)
        return text[start:end].strip()
    if text.startswith("<") and ">" in text:
        return ""
    return text.strip()


def _agy_transcript(session_id: str) -> list[dict[str, Any]]:
    """agy 的对话在 ``.system_generated/logs/transcript.jsonl``（一行一条 step）。"""
    path = agy_brain_root() / session_id / ".system_generated" / "logs" / "transcript.jsonl"
    if not path.is_file():
        return []
    messages: list[dict[str, Any]] = []
    for record in _records(path):
        kind = record.get("type")
        if kind == "USER_INPUT":
            text = _agy_user_text(record.get("content"))
            if text:
                messages.append({"role": "user", "text": text, "ts": str(record.get("created_at") or "")})
        elif kind == "PLANNER_RESPONSE":
            text = str(record.get("content") or "").strip()
            if text and text != "None":
                messages.append({"role": "assistant", "text": text, "ts": str(record.get("created_at") or "")})
    return messages


# ── 发现与来源 ────────────────────────────────────────────────────────────

def all_sessions() -> list[dict[str, Any]]:
    now = time.time()
    if _ALL_CACHE["rows"] and now - float(_ALL_CACHE["at"]) < _ALL_TTL:
        return list(_ALL_CACHE["rows"])
    sessions = [
        *_qoder_sessions(), *_codebuddy_sessions(), *_marvis_sessions(),
        *_cursor_sessions(), *_agy_sessions(),
    ]
    sessions.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    _ALL_CACHE["rows"] = sessions
    _ALL_CACHE["at"] = now

    return list(sessions)


def session_sources() -> list[dict[str, Any]]:
    """设置那一屏要看的：这台上每家外部软件、会话在哪、读不读得动、能不能说。"""
    counts = {VENDOR_QODER: 0, VENDOR_CODEBUDDY: 0, VENDOR_MARVIS: 0, VENDOR_CURSOR: 0, VENDOR_AGY: 0}
    for item in all_sessions():
        counts[item["vendor"]] = counts.get(item["vendor"], 0) + 1

    def source(vendor: str, root: Path, *, readable: bool, reason: str) -> dict[str, Any]:
        return {
            "readable": readable,
            "reason": reason,
            "root": str(root),
            "sessions": counts.get(vendor, 0),
            "sendable": vendor in SENDABLE,
            "vendor": vendor,
        }

    cursor = cursor_db()
    agy = agy_db()

    # Cursor：库在 ≠ 读得到。**照实说「这台没留下」**——不假装读得到，
    # 也不说成「它没有对话」（它装了、库在，只是里面没有可读的内容）。
    cursor_readable = cursor.is_file()
    cursor_reason = "" if cursor_readable else "没找到 Cursor 的 state.vscdb"
    if cursor_readable and not counts.get(VENDOR_CURSOR, 0):
        cursor_readable = False
        cursor_reason = "Cursor 这台机器上没留下可读的对话"

    return [
        source(VENDOR_QODER, qoder_root(), readable=True, reason=""),
        source(VENDOR_CODEBUDDY, codebuddy_root(), readable=True, reason=""),
        source(VENDOR_MARVIS, marvis_db().parent, readable=True, reason=""),
        source(VENDOR_CURSOR, cursor.parent,
               readable=cursor_readable,
               reason=cursor_reason),
        source(VENDOR_AGY, agy.parent,
               readable=agy.is_file(),
               reason="" if agy.is_file() else "没找到 agy 的会话摘要库"),
    ]


# ── Plobi 侧的会话账本：改名 / 置顶 / 归档 / 删除（回收站）────────────────
#
# 用户 10-28 拍：L2 要像他一样「支配」这些对话。账本只放**我们这一侧**——
# Qoder 桌面 App 的索引是另一套（实测不认 CLI 写的会话），所以名字只在 Plobi 里生效。
# 删除是**搬家**，永远不 rm：搬到 `$PLOBI_HOME/external_sessions/trash/…`，账本记原路径，可恢复。

INDEX_VERSION = 1


def index_file() -> Path:
    from plobi_constants import get_plobi_home

    return get_plobi_home() / "external_sessions" / "index.json"


def _load_index() -> dict[str, Any]:
    try:
        payload = json.loads(index_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_index(payload: dict[str, Any]) -> bool:
    path = index_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        return False

    return True


def _agent_index(payload: dict[str, Any], agent: str) -> dict[str, Any]:
    entries = payload.get(agent)
    if not isinstance(entries, dict):
        entries = {}
        payload[agent] = entries

    return entries


def _key(vendor: str, session_id: str) -> str:
    return f"{vendor}:{session_id}"


def _entry_for_session(entries: dict[str, Any], vendor: str, session_id: str) -> dict[str, Any]:
    entry = entries.get(_key(vendor, session_id))
    return entry if isinstance(entry, dict) else {}


def update_entry(
    agent: str, vendor: str, session_id: str, *,
    title: Optional[str] = None, pinned: Optional[bool] = None, archived: Optional[bool] = None,
    model: Optional[str] = None, permission_level: Optional[str] = None,
) -> dict[str, Any]:
    """改名 / 置顶 / 归档 / 会话预设。只记这一家这一条，别处不动。

    `model` / `permission_level` 是 **Plobi 侧的会话预设**（下次从 Plobi 续接时用），
    不是去改对方软件里那条会话。传空串＝清掉那一格；传 None＝不动。
    """
    if vendor not in _known_vendors():
        return {"ok": False, "status": 400, "error": "不认识这家软件"}
    if not SESSION_ID_RE.match(str(session_id or "")):
        return {"ok": False, "status": 400, "error": "会话号不是可执行形状"}
    payload = _load_index()
    entries = _agent_index(payload, agent)
    entry = dict(_entry_for_session(entries, vendor, session_id))
    if title is not None:
        entry["title"] = str(title).strip()[:120]
    if pinned is not None:
        entry["pinned"] = bool(pinned)
    if archived is not None:
        entry["archived"] = bool(archived)
    if model is not None:
        value = str(model).strip()
        if value:
            entry["model"] = value[:160]
        else:
            entry.pop("model", None)
    if permission_level is not None:
        value = str(permission_level).strip().lower()
        if value:
            entry["permission_level"] = value
        else:
            entry.pop("permission_level", None)
    entries[_key(vendor, session_id)] = entry
    if not _save_index(payload):
        return {"ok": False, "status": 500, "error": "账本写不进去（磁盘只读？）"}

    return {"ok": True, "data": {"entry": entry}}


def prefs_for_agent(agent_name: str, vendor: str, session_id: str) -> dict[str, Any]:
    """这条会话在 Plobi 侧记下的模型 / 权限预设（没记就是 None）。"""
    entry = _entry_for(agent_name)
    if entry is None:
        return {"model": None, "permission_level": None}
    key = entry.name if hasattr(entry, "name") else agent_name
    record = _entry_for_session(_agent_index(_load_index(), key), vendor, session_id)

    return {
        "model": record.get("model") or None,
        "permission_level": record.get("permission_level") or None,
    }


def set_prefs_for_agent(
    agent_name: str, vendor: str, session_id: str, *,
    model: Optional[str] = None, permission_level: Optional[str] = None,
) -> dict[str, Any]:
    """给一条会话写预设。先确认这条会话真的在名单里，再落到账本。"""
    from plobi.delegation.external_capabilities import supports, unsupported_note

    for action in (("model" if model is not None else None), ("permission" if permission_level is not None else None)):
        if action and not supports(vendor, action):
            return {"ok": False, "status": 400, "error": unsupported_note(vendor, action)}
    if not _in_agent_scope(agent_name, vendor, session_id):
        return {"ok": False, "status": 404, "error": "这条会话不在这个 Agent 的名单里"}
    if _locate(vendor, session_id) is None:
        return {"ok": False, "status": 404, "error": "没发现这条会话"}

    result = update_entry(agent_name, vendor, session_id, model=model, permission_level=permission_level)
    if not result.get("ok"):
        return result

    spec = capabilities_for(vendor)
    result["data"]["apply_on_send"] = bool(spec and spec.apply_on_send)
    result["data"]["note"] = (
        "预设已记下；续接时会自动带上。"
        if result["data"]["apply_on_send"]
        else "预设已记下，但这家续接时还不能自动应用——只作为 Plobi 侧的设置，不是改对方软件里那条会话。"
    )

    return result


def _vendor_root(vendor: str) -> Path:
    return {
        VENDOR_QODER: qoder_root(),
        VENDOR_CODEBUDDY: codebuddy_root(),
        VENDOR_AGY: agy_brain_root(),
    }.get(vendor, Path(""))


def _session_payload_paths(vendor: str, session_id: str) -> list[Path]:
    """这一条会话在本机上的落盘物：Qoder/CodeBuddy 是 jsonl(+state 目录)，agy 是 brain 目录。"""
    paths: list[Path] = []
    if vendor in (VENDOR_QODER, VENDOR_CODEBUDDY):
        root = _vendor_root(vendor)
        if not root.is_dir():
            return []
        for jsonl in root.glob(f"*/{session_id}.jsonl"):
            paths.append(jsonl)
            state = jsonl.parent / session_id
            if state.is_dir():
                paths.append(state)
    elif vendor == VENDOR_AGY:
        brain = agy_brain_root() / session_id
        if brain.is_dir():
            paths.append(brain)

    return paths


def delete_entry(agent: str, vendor: str, session_id: str) -> dict[str, Any]:
    """删除＝搬到回收站。数据库那两家（Marvis/Cursor）只隐藏，不动它们的库。"""
    if vendor not in _known_vendors():
        return {"ok": False, "status": 400, "error": "不认识这家软件"}
    if not SESSION_ID_RE.match(str(session_id or "")):
        return {"ok": False, "status": 400, "error": "会话号不是可执行形状"}
    if _locate(vendor, session_id) is None:
        return {"ok": False, "status": 404, "error": "没发现这条会话"}

    payload = _load_index()
    entries = _agent_index(payload, agent)
    paths = _session_payload_paths(vendor, session_id)
    moved: list[dict[str, str]] = []
    trash_dir: Optional[Path] = None
    if paths:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        trash_dir = index_file().parent / "trash" / vendor / f"{session_id}-{stamp}"
        try:
            trash_dir.mkdir(parents=True, exist_ok=True)
            root = _vendor_root(vendor).resolve()
            for path in paths:
                resolved = path.resolve()
                if root not in resolved.parents and resolved != root:
                    return {"ok": False, "status": 400, "error": "目标不在那家软件的目录里，拒绝搬"}
                target = trash_dir / resolved.name
                os.replace(resolved, target)
                moved.append({"from": str(path), "to": str(target)})
        except OSError as exc:
            return {"ok": False, "status": 500, "error": f"搬去回收站失败：{exc}"}

    entry = dict(_entry_for_session(entries, vendor, session_id))
    entry["deleted"] = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "moved": moved,
        "trash": str(trash_dir) if trash_dir else "",
        "hid_only": not paths,
    }
    entries[_key(vendor, session_id)] = entry
    if not _save_index(payload):
        return {"ok": False, "status": 500, "error": "账本写不进去（磁盘只读？）"}

    return {"ok": True, "data": {"deleted": entry["deleted"]}}


def restore_entry(agent: str, vendor: str, session_id: str) -> dict[str, Any]:
    payload = _load_index()
    entries = _agent_index(payload, agent)
    entry = dict(_entry_for_session(entries, vendor, session_id))
    deleted = entry.get("deleted")
    if not isinstance(deleted, dict):
        return {"ok": False, "status": 409, "error": "账本里没有这一条的删除记录"}
    for item in deleted.get("moved") or []:
        if not isinstance(item, dict):
            continue
        src = Path(str(item.get("to") or ""))
        dst = Path(str(item.get("from") or ""))
        try:
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.replace(src, dst)
        except OSError as exc:
            return {"ok": False, "status": 500, "error": f"搬回来失败：{exc}"}
    entry.pop("deleted", None)
    entries[_key(vendor, session_id)] = entry
    if not _save_index(payload):
        return {"ok": False, "status": 500, "error": "账本写不进去（磁盘只读？）"}

    return {"ok": True, "data": {"entry": entry}}


# ── L2 的归属过滤 ─────────────────────────────────────────────────────────

def _marks_file() -> Path:
    from plobi_constants import get_plobi_home

    return get_plobi_home() / "external_sessions" / "relevance.json"


def load_marks(agent: str) -> dict[str, bool]:
    try:
        payload = json.loads(_marks_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    marks = payload.get(agent) if isinstance(payload, dict) else None
    if not isinstance(marks, dict):
        return {}

    return {str(key): bool(value) for key, value in marks.items()}


def mark_sessions(agent: str, items: Iterable[dict[str, Any]]) -> int:
    """L2 自己的判断落进 sidecar（atomic replace）。返回写进几条。"""
    path = _marks_file()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            payload = {}
    except (OSError, json.JSONDecodeError):
        payload = {}
    current = payload.get(agent)
    if not isinstance(current, dict):
        current = {}
    written = 0
    for item in items:
        vendor = str(item.get("vendor") or "").strip()
        session_id = str(item.get("id") or "").strip()
        if vendor not in _known_vendors():
            continue
        if not SESSION_ID_RE.match(session_id):
            continue
        if not isinstance(item.get("related"), bool):
            continue
        current[f"{vendor}:{session_id}"] = item["related"]
        written += 1
    payload[agent] = current
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        return 0

    return written


def _norm(path: Any) -> str:
    return str(path or "").strip().replace("\\", "/").rstrip("/").lower()


def _entry_for(agent_name: str):
    from plobi.agents.registry import AgentRegistry

    return AgentRegistry.load().agents.get(str(agent_name or "").strip())


def _project_paths(entry) -> list[str]:
    paths = list(getattr(entry, "project_paths", ()) or ())
    primary = str(getattr(entry, "project_path", "") or "").strip()
    if primary and primary not in paths:
        paths.insert(0, primary)

    return [str(path) for path in paths if str(path or "").strip()]


def _identity_words(entry) -> list[str]:
    words = [str(getattr(entry, "name", "") or ""), str(getattr(entry, "display_name", "") or "")]
    for path in _project_paths(entry):
        tail = path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        if tail:
            words.append(tail)

    return [word.strip().lower() for word in words if len(word.strip()) >= 2]


def _related(entry, session: dict[str, Any], marks: dict[str, bool]) -> tuple[Optional[bool], str]:
    key = f"{session['vendor']}:{session['id']}"
    if key in marks:
        return marks[key], "l2"

    project = _norm(session.get("project"))
    project_paths = _project_paths(entry)
    for path in project_paths:
        base = _norm(path)
        if project and (project == base or project.startswith(f"{base}/")):
            return True, "path"

    haystack = f"{session.get('title') or ''} {session.get('project') or ''}".lower()
    for word in _identity_words(entry):
        if word in haystack:
            return True, "title"

    # 会话自己写明在哪个目录，而这个 Agent 的目录是另一处 ⇒ 不是它的活，不必留给人再判一遍。
    # 没有目录（Marvis）或 Agent 没登记目录的，仍然留给 L2 判。
    if project and project_paths:
        return False, "path-miss"

    return None, "unknown"


def sessions_for_agent(agent_name: str) -> dict[str, Any]:
    entry = _entry_for(agent_name)
    if entry is None:
        return {"projects": [], "reason": f"名册里没有 {agent_name}", "sessions": [], "sources": session_sources()}

    marks = load_marks(entry.name if hasattr(entry, "name") else agent_name)
    agent_key = entry.name if hasattr(entry, "name") else agent_name
    ledger = _agent_index(_load_index(), agent_key)
    sessions = []
    for session in all_sessions():
        record = _entry_for_session(ledger, session["vendor"], session["id"])
        if isinstance(record.get("deleted"), dict):
            # 删掉的是**读数**：它还在盘上（或只在账本里藏着），但这一屏不再列。
            continue
        related, source = _related(entry, session, marks)
        if isinstance(record.get("title"), str) and record["title"].strip():
            session = {**session, "title": record["title"].strip()}
        sessions.append({
            **session,
            "archived": bool(record.get("archived")),
            "pinned": bool(record.get("pinned")),
            "related": related,
            "related_source": source,
        })
    # 先按时间倒序，再按「置顶 → 档位」稳定排序：同一档里仍然是新的在前。
    sessions.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    sessions.sort(key=lambda item: (0 if item.get("pinned") else 1, {True: 0, None: 1, False: 2}[item["related"]]))

    return {
        "projects": _project_paths(entry),
        "reason": "" if sessions else "这台机器上还没发现可读的外部会话",
        "sessions": sessions,
        "sources": session_sources(),
    }


# ── 读 ────────────────────────────────────────────────────────────────────

def _locate(vendor: str, session_id: str) -> Optional[dict[str, Any]]:
    if not SESSION_ID_RE.match(str(session_id or "")):
        return None
    for session in all_sessions():
        if session["vendor"] == vendor and session["id"] == session_id:
            return session

    return None


def _in_agent_scope(agent_name: str, vendor: str, session_id: str) -> bool:
    entry = _entry_for(agent_name)
    if entry is None:
        return False
    marks = load_marks(entry.name if hasattr(entry, "name") else agent_name)
    session = _locate(vendor, session_id)
    if session is None:
        return False
    related, _source = _related(entry, session, marks)

    return related is not False


def _qoder_transcript(session_id: str) -> list[dict[str, Any]]:
    for jsonl in qoder_root().glob(f"*/{session_id}.jsonl"):
        messages: list[dict[str, Any]] = []
        for record in _records(jsonl):
            if record.get("type") not in ("user", "assistant"):
                continue
            message = record.get("message")
            text = _block_text(message.get("content") if isinstance(message, dict) else record.get("content"))
            if text:
                messages.append({"role": record["type"], "text": text, "ts": str(record.get("timestamp") or "")})

        return messages

    return []


def _codebuddy_transcript(session_id: str) -> list[dict[str, Any]]:
    for jsonl in codebuddy_root().glob(f"*/{session_id}.jsonl"):
        messages: list[dict[str, Any]] = []
        for record in _records(jsonl):
            if record.get("type") != "message" or record.get("role") not in ("user", "assistant"):
                continue
            text = _block_text(record.get("content"))
            if text:
                messages.append({"role": record["role"], "text": text, "ts": str(record.get("timestamp") or "")})

        return messages

    return []


def _marvis_transcript(session_id: str) -> list[dict[str, Any]]:
    db = marvis_db()
    if not db.is_file():
        return []
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            # 取**最后** MAX_MESSAGES 条就够（有一会话九千多条，整段读会卡住左栏）；
            # 反向取走索引（conversation_id, message_seq），不整表排序。
            rows = con.execute(
                "select role, content, created_at from messages where conversation_id = ? "
                "and role in ('user','assistant') and content is not null "
                "order by message_seq desc limit ?",
                (session_id, MAX_MESSAGES),
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []

    messages: list[dict[str, Any]] = []
    for role, content, created in reversed(rows):
        text = str(content or "").strip()
        if not text:
            continue
        messages.append({"role": role, "text": _clamp_text(text), "ts": _local_stamp(created)})

    return messages


def _cursor_transcript(session_id: str) -> list[dict[str, Any]]:
    db = cursor_db()
    if not db.is_file():
        return []
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            row = con.execute(
                "select value from cursorDiskKV where key = ?", (f"composerData:{session_id}",)
            ).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return []

    return _cursor_messages(json.loads(row[0])) if row else []


def read_transcript(vendor: str, session_id: str) -> dict[str, Any]:
    session = _locate(vendor, session_id)
    if session is None:
        return {"ok": False, "status": 404, "error": "没发现这条会话"}
    if vendor == VENDOR_QODER:
        messages = _qoder_transcript(session_id)
    elif vendor == VENDOR_CODEBUDDY:
        messages = _codebuddy_transcript(session_id)
    elif vendor == VENDOR_MARVIS:
        messages = _marvis_transcript(session_id)
    elif vendor == VENDOR_CURSOR:
        messages = _cursor_transcript(session_id)
    elif vendor == VENDOR_AGY:
        messages = _agy_transcript(session_id)
    else:
        return {"ok": False, "status": 400, "error": "这家外部软件的会话还读不了"}

    return {
        "ok": True,
        "data": {"id": session_id, "messages": messages[-MAX_MESSAGES:],
                 "project": session.get("project") or "", "vendor": vendor},
    }


# ── 说（这一版只有 Qoder）──────────────────────────────────────────────────

def send_message(vendor: str, session: dict[str, Any], text: str, *, timeout: float = 600.0) -> dict[str, Any]:
    body = (text or "").strip()
    if not body:
        return {"ok": False, "status": 400, "error": "说点什么再发"}
    if len(body) > MAX_MESSAGE_CHARS:
        return {"ok": False, "status": 400, "error": f"一次最多 {MAX_MESSAGE_CHARS} 字"}
    if vendor not in SENDABLE:
        usable = "、".join(sorted(SENDABLE)) or "（本机一家都接不上）"
        return {"ok": False, "status": 400, "error": f"这家还不能从 Plobi 里说话；现在能续接的：{usable}"}

    project = str(session.get("project") or "").strip()
    if not project:
        return {"ok": False, "status": 409, "error": "这条会话没记工作目录，不知道去哪儿续"}

    from agent import external_offload as channel

    binary = channel.resolve_command(channel.AGENT_QODER)
    if not binary:
        return {"ok": False, "status": 409, "error": "找不到 qoder 对应的可执行程序"}
    allowed, why = channel.cwd_is_allowed(project)
    if not allowed:
        return {"ok": False, "status": 409, "error": f"记录的项目目录当前不可用：{why}"}

    env = channel.scrub_child_env(channel.AGENT_QODER, dict(os.environ))
    argv = [binary, "--resume", session["id"], "--output-format", "json", "-p", body]
    started = time.time()
    try:
        finished = subprocess.run(  # noqa: S603 — 固定模板 + 校验过的会话号
            argv, cwd=project, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "status": 504, "error": f"它 {timeout:.0f}s 内没有答完"}
    except OSError as exc:
        return {"ok": False, "status": 500, "error": f"起不来：{exc}"}

    answer, usage, _session, _source = channel._parse_envelope(finished.stdout or "")
    cost, cost_status = channel._cost(usage)
    ok = finished.returncode == 0 and bool(answer)

    return {
        "ok": ok,
        "data": {
            "cost_usd": cost,
            "cost_status": cost_status,
            "credits": usage.get("total_credits"),
            "duration_seconds": round(time.time() - started, 2),
            "reply": answer,
        },
        "error": "" if ok else ((finished.stderr or "").strip()[:300] or f"exit={finished.returncode}"),
    }


# ── 路由层用的三个入口 ─────────────────────────────────────────────────────

def transcript_for_agent(agent_name: str, vendor: str, session_id: str) -> dict[str, Any]:
    if not _in_agent_scope(agent_name, vendor, session_id):
        return {"ok": False, "status": 404, "error": "这条会话不在这个 Agent 的名单里"}
    result = read_transcript(vendor, session_id)

    return result


def send_for_agent(agent_name: str, vendor: str, session_id: str, text: str) -> dict[str, Any]:
    if not _in_agent_scope(agent_name, vendor, session_id):
        return {"ok": False, "status": 404, "error": "这条会话不在这个 Agent 的名单里"}
    session = _locate(vendor, session_id)
    if session is None:
        return {"ok": False, "status": 404, "error": "没发现这条会话"}

    return send_message(vendor, session, text)


def mark_for_agent(agent_name: str, items: Iterable[dict[str, Any]]) -> dict[str, Any]:
    entry = _entry_for(agent_name)
    if entry is None:
        return {"ok": False, "status": 404, "error": f"名册里没有 {agent_name}"}
    written = mark_sessions(entry.name if hasattr(entry, "name") else agent_name, items)

    return {"ok": True, "data": {"written": written}}

def update_for_agent(agent_name: str, vendor: str, session_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    if not _in_agent_scope(agent_name, vendor, session_id):
        return {"ok": False, "status": 404, "error": "这条会话不在这个 Agent 的名单里"}

    return update_entry(agent_name, vendor, session_id,
                        title=fields.get("title"), pinned=fields.get("pinned"),
                        archived=fields.get("archived"))


def delete_for_agent(agent_name: str, vendor: str, session_id: str) -> dict[str, Any]:
    if not _in_agent_scope(agent_name, vendor, session_id):
        return {"ok": False, "status": 404, "error": "这条会话不在这个 Agent 的名单里"}

    return delete_entry(agent_name, vendor, session_id)


def restore_for_agent(agent_name: str, vendor: str, session_id: str) -> dict[str, Any]:
    if _entry_for(agent_name) is None:
        return {"ok": False, "status": 404, "error": f"名册里没有 {agent_name}"}

    return restore_entry(agent_name, vendor, session_id)
