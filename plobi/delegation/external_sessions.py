"""外部软件自己的会话：自动发现、按 L2 归属过滤、读得到、说得上话（WP-EXTERNAL-SESSIONS）。

用户 10-27 要的是「L2 拥有和用户一样的会话列表」：自动认出本机上各家 AI 软件的对话，
由 L2 自己判断哪条与它有关，然后在左栏按应用分组展开。

**只在 Plobi 这一侧做**：Qoder 桌面 App 的索引是另一套，实测不认 CLI 写的会话，
所以不假装能驱动它的窗口。

已核过的存放形状（10-27 现读）：

- ``qoder``      ``~/.qoder/projects/<项目键>/<会话号>.jsonl`` + ``<会话号>/state.json``
- ``codebuddy``  ``~/.codebuddy/projects/<项目键>/<会话号>.jsonl``（记录顶层带 ``cwd``）
- ``marvis``     ``~/.marvis/database/data.db`` 的 ``conversations`` / ``messages`` 两张表（只读）
- ``cursor`` / ``agy``  还没找到可读的会话文件——**照实报**，不画空壳

三条判据：

- 发现：扫每家的固定根目录，不扫盘；项目路径只从会话自己的记录里读，或从名册登记里比。
- 过滤：L2 的三个来源依次是「它自己标过的（sidecar）」→「项目目录命中」→「标题里点到它的名字」；
  都不中的是 ``related=None``（待判断），不冒充「不相关」。
- 说：``qodercli --resume <id> -p <text>`` —— 这条旗标实测过（10-23、10-27 各一次真回话）。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

VENDOR_QODER = "qoder"
VENDOR_CODEBUDDY = "codebuddy"
VENDOR_MARVIS = "marvis"
VENDOR_CURSOR = "cursor"
VENDOR_AGY = "agy"

#: 这一版能真的把话送进去的只有 Qoder：其余两家只读。不是漏做，是没实测过回跳旗标。
SENDABLE = frozenset({VENDOR_QODER})

#: 会话号会拼进文件名/argv/DB 查询，先于任何拼接校一遍。``_`` 留给 Marvis 的 ``conv_…``。
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

MAX_MESSAGE_CHARS = 8000
MAX_MESSAGES = 300


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


def _entry(directory: Path, jsonl: Path, *, project: str, title: str, updated: str,
           sendable: bool) -> dict[str, Any]:
    return {
        "id": jsonl.stem,
        "project": project,
        "sendable": sendable,
        "title": (title or jsonl.stem).splitlines()[0][:120],
        "updated_at": updated or _mtime_iso(jsonl),
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
        finally:
            con.close()
    except sqlite3.Error:
        return []
    for conversation_id, title, updated_at in rows:
        if not isinstance(conversation_id, str) or not SESSION_ID_RE.match(conversation_id):
            continue
        out.append({
            "id": conversation_id,
            "project": "",
            "sendable": False,
            "title": str(title or conversation_id)[:120],
            "updated_at": str(updated_at or ""),
            "vendor": VENDOR_MARVIS,
        })

    return out


# ── 发现与来源 ────────────────────────────────────────────────────────────

def all_sessions() -> list[dict[str, Any]]:
    now = time.time()
    if _ALL_CACHE["rows"] and now - float(_ALL_CACHE["at"]) < _ALL_TTL:
        return list(_ALL_CACHE["rows"])
    sessions = [*_qoder_sessions(), *_codebuddy_sessions(), *_marvis_sessions()]
    sessions.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    _ALL_CACHE["rows"] = sessions
    _ALL_CACHE["at"] = now

    return list(sessions)


def session_sources() -> list[dict[str, Any]]:
    """设置那一屏要看的：这台上每家外部软件、会话在哪、读不读得动、能不能说。"""
    counts = {VENDOR_QODER: 0, VENDOR_CODEBUDDY: 0, VENDOR_MARVIS: 0}
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

    return [
        source(VENDOR_QODER, qoder_root(), readable=True, reason=""),
        source(VENDOR_CODEBUDDY, codebuddy_root(), readable=True, reason=""),
        source(VENDOR_MARVIS, marvis_db().parent, readable=True, reason=""),
        source(VENDOR_CURSOR, cursor_root(), readable=False,
               reason="桌面版把对话放进 workspaceStorage 的 state.vscdb 里，还没接"),
        source(VENDOR_AGY, Path(""), readable=False, reason="还没找到它的会话存放"),
    ]


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
        if vendor not in (VENDOR_QODER, VENDOR_CODEBUDDY, VENDOR_MARVIS, VENDOR_CURSOR, VENDOR_AGY):
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
    for path in _project_paths(entry):
        base = _norm(path)
        if project and (project == base or project.startswith(f"{base}/")):
            return True, "path"

    haystack = f"{session.get('title') or ''} {session.get('project') or ''}".lower()
    for word in _identity_words(entry):
        if word in haystack:
            return True, "title"

    return None, "unknown"


def sessions_for_agent(agent_name: str) -> dict[str, Any]:
    entry = _entry_for(agent_name)
    if entry is None:
        return {"projects": [], "reason": f"名册里没有 {agent_name}", "sessions": [], "sources": session_sources()}

    marks = load_marks(entry.name if hasattr(entry, "name") else agent_name)
    sessions = []
    for session in all_sessions():
        related, source = _related(entry, session, marks)
        sessions.append({**session, "related": related, "related_source": source})
    # 先按时间倒序，再按档位稳定排序：同一档里仍然是新的在前。
    sessions.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    sessions.sort(key=lambda item: {True: 0, None: 1, False: 2}[item["related"]])

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
            rows = con.execute(
                "select role, content, created_at from messages where conversation_id = ? "
                "and role in ('user','assistant') and content is not null "
                "order by cast(message_seq as integer)",
                (session_id,),
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []

    return [{"role": role, "text": str(content), "ts": str(created or "")} for role, content, created in rows if str(content).strip()]


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
    if vendor != VENDOR_QODER:
        return {"ok": False, "status": 400, "error": "这家还不能从 Plobi 里说话（只接了 qoder）"}

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
