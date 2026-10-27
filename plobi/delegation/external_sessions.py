"""外部软件自己的会话：列出来、读得到、说得上话（WP-EXTERNAL-SESSIONS）。

用户 10-27 要的是「L2 拥有和用户一样的会话列表」：看得见机器上跟它项目有关的对话、
能在里面说话、以后还能改名/删除。**在 Plobi 这一侧做**——Qoder 桌面 App 的索引是另一套，
我们实测过 CLI 写进去的会话它不显示，所以不假装能同步它的窗口。

首批只做 Qoder：它的会话是盘上的文件，形状已核过（10-27）：

    ~/.qoder/projects/<项目键>/<会话号>.jsonl          # transcript，一行一条记录
    ~/.qoder/projects/<项目键>/<会话号>/state.json     # createdAt / updatedAt

项目键 = 绝对路径里所有非字母数字字符换成 ``-``（``D:\\Projects\\Sindri`` → ``D--Projects-Sindri``）。

三条判据写在这里，路由层只做参数校验与转发：

- 列表：按**名册登记的项目目录**找会话，不接受请求方递来的任意路径（路径穿越的口子不开）。
- 读：只读那个会话文件里的 user / assistant 文本；thinking、工具调用、附件都不是「对话」。
- 说：``qodercli --resume <id> -p <text>`` —— 这条旗标实测过（10-23：问上一轮读到的标题，
  回的正是原来那行）。环境变量按通道那张表洗，别让桌面进程的 SDK 变量把它打回 ``sdk_invalid_args``。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Optional, Sequence

VENDOR_QODER = "qoder"
SUPPORTED = (VENDOR_QODER,)

#: 会话号会拼进文件名与 argv，先于任何路径拼接校一遍。
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

#: 一句话的上限：它不是文件上传口，超过这个长度的活该走外包 run。
MAX_MESSAGE_CHARS = 8000

#: 读 transcript 时最多回多少条（老的对话不截断，但别把整份历史塞进一次响应）。
MAX_MESSAGES = 300


def qoder_root() -> Path:
    """Qoder 的会话根。``QODER_CONFIG_DIR`` 优先（与 CLI 用的是同一个变量）。"""
    override = os.environ.get("QODER_CONFIG_DIR", "").strip()
    base = Path(os.path.expanduser(override)) if override else Path.home() / ".qoder"

    return base / "projects"


def project_key(path: str) -> str:
    """绝对路径 → Qoder 的目录名。大小写不敏感地用它（Windows 盘上大小写会变）。"""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path or "").strip())


def _session_dirs(project_paths: Sequence[str]) -> list[tuple[str, Path]]:
    root = qoder_root()
    if not root.is_dir():
        return []
    wanted = {}
    for path in project_paths:
        text = str(path or "").strip()
        if text:
            wanted.setdefault(project_key(text).lower(), text)
    found: list[tuple[str, Path]] = []

    for child in root.iterdir():
        if not child.is_dir():
            continue
        real = wanted.get(child.name.lower())
        if real:
            found.append((real, child))

    return found


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]

        return "\n".join(part for part in parts if part).strip()

    return ""


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


def _title_of(records: Sequence[dict[str, Any]], fallback: str) -> str:
    for record in records:
        if record.get("type") != "user":
            continue
        message = record.get("message")
        text = _text_of(message.get("content") if isinstance(message, dict) else record.get("content"))
        if text:
            return text.splitlines()[0][:120]

    return fallback


def _updated_at(session_dir: Path, jsonl: Path) -> str:
    state = session_dir / "state.json"
    try:
        payload = json.loads(state.read_text(encoding="utf-8"))
        value = payload.get("updatedAt") or payload.get("createdAt")
        if isinstance(value, str) and value.strip():
            return value.strip()
    except (OSError, json.JSONDecodeError):
        pass
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(jsonl.stat().st_mtime))
    except OSError:
        return ""


def list_sessions(project_paths: Sequence[str]) -> dict[str, Any]:
    """列出这些项目目录下的 Qoder 会话，新的在前。空表也是读数（带 reason）。"""
    projects = [str(path) for path in project_paths if str(path or "").strip()]
    root = qoder_root()
    if not projects:
        return {"sessions": [], "reason": "这个 Agent 名下没有登记项目目录", "root": str(root), "vendor": VENDOR_QODER}
    if not root.is_dir():
        return {"sessions": [], "reason": f"这台机器没有 Qoder 的会话目录（{root}）", "root": str(root), "vendor": VENDOR_QODER}

    sessions: list[dict[str, Any]] = []
    for project, directory in _session_dirs(projects):
        for jsonl in sorted(directory.glob("*.jsonl")):
            session_id = jsonl.stem
            if not SESSION_ID_RE.match(session_id):
                continue
            records = _records(jsonl)
            sessions.append({
                "vendor": VENDOR_QODER,
                "id": session_id,
                "project": project,
                "title": _title_of(records, session_id),
                "updated_at": _updated_at(directory / session_id, jsonl),
            })

    sessions.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)

    return {"sessions": sessions, "reason": "" if sessions else "这些项目下还没有 Qoder 会话", "root": str(root), "vendor": VENDOR_QODER}


def session_file(project_paths: Sequence[str], session_id: str) -> Optional[tuple[str, Path]]:
    """会话号 → (项目目录, 文件)。只认登记目录里的文件，别处的一律不读。"""
    if not SESSION_ID_RE.match(str(session_id or "")):
        return None
    for project, directory in _session_dirs(project_paths):
        candidate = directory / f"{session_id}.jsonl"
        if candidate.is_file():
            return project, candidate

    return None


def read_transcript(project_paths: Sequence[str], session_id: str) -> dict[str, Any]:
    located = session_file(project_paths, session_id)
    if located is None:
        return {"ok": False, "status": 404, "error": "这个 Agent 的项目里没有这条会话"}
    project, path = located

    messages: list[dict[str, Any]] = []
    for record in _records(path):
        kind = record.get("type")
        if kind not in ("user", "assistant"):
            continue
        message = record.get("message")
        content = message.get("content") if isinstance(message, dict) else record.get("content")
        text = _text_of(content)
        if not text:
            continue
        messages.append({
            "role": "user" if kind == "user" else "assistant",
            "text": text,
            "ts": str(record.get("timestamp") or ""),
        })

    return {
        "ok": True,
        "data": {
            "id": session_id,
            "messages": messages[-MAX_MESSAGES:],
            "project": project,
            "vendor": VENDOR_QODER,
        },
    }


def send_message(
    project_paths: Sequence[str],
    session_id: str,
    text: str,
    *,
    timeout: float = 600.0,
) -> dict[str, Any]:
    """把一句话送进它那条会话（续接，不是新开）。返回它的回话与花费读数。"""
    body = (text or "").strip()
    if not body:
        return {"ok": False, "status": 400, "error": "说点什么再发"}
    if len(body) > MAX_MESSAGE_CHARS:
        return {"ok": False, "status": 400, "error": f"一次最多 {MAX_MESSAGE_CHARS} 字"}

    located = session_file(project_paths, session_id)
    if located is None:
        return {"ok": False, "status": 404, "error": "这个 Agent 的项目里没有这条会话"}
    project, _path = located

    from agent import external_offload as channel

    binary = channel.resolve_command(channel.AGENT_QODER)
    if not binary:
        return {"ok": False, "status": 409, "error": "找不到 qoder 对应的可执行程序"}
    allowed, why = channel.cwd_is_allowed(project)
    if not allowed:
        return {"ok": False, "status": 409, "error": f"记录的项目目录当前不可用：{why}"}

    env = channel.scrub_child_env(channel.AGENT_QODER, dict(os.environ))
    argv = [binary, "--resume", session_id, "--output-format", "json", "-p", body]
    started = time.time()
    try:
        finished = subprocess.run(  # noqa: S603 — argv 来自固定模板 + 校验过的会话号
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


def _entry_for(agent_name: str):
    from plobi.agents.registry import AgentRegistry

    registry = AgentRegistry.load()
    return registry.agents.get(str(agent_name or "").strip())


def _project_paths(entry) -> list[str]:
    paths = list(getattr(entry, "project_paths", ()) or ())
    primary = str(getattr(entry, "project_path", "") or "").strip()
    if primary and primary not in paths:
        paths.insert(0, primary)

    return [str(path) for path in paths if str(path or "").strip()]


def sessions_for_agent(agent_name: str) -> dict[str, Any]:
    entry = _entry_for(agent_name)
    if entry is None:
        return {"projects": [], "reason": f"名册里没有 {agent_name}", "sessions": [], "vendor": VENDOR_QODER}
    projects = _project_paths(entry)
    payload = list_sessions(projects)

    return {"projects": projects, **payload}


def transcript_for_agent(agent_name: str, vendor: str, session_id: str) -> dict[str, Any]:
    if vendor != VENDOR_QODER:
        return {"ok": False, "status": 400, "error": "这家外部软件的会话还没接"}
    entry = _entry_for(agent_name)
    if entry is None:
        return {"ok": False, "status": 404, "error": f"名册里没有 {agent_name}"}

    return read_transcript(_project_paths(entry), session_id)


def send_for_agent(agent_name: str, vendor: str, session_id: str, text: str) -> dict[str, Any]:
    if vendor != VENDOR_QODER:
        return {"ok": False, "status": 400, "error": "这家外部软件的会话还没接"}
    entry = _entry_for(agent_name)
    if entry is None:
        return {"ok": False, "status": 404, "error": f"名册里没有 {agent_name}"}

    return send_message(_project_paths(entry), session_id, text)
