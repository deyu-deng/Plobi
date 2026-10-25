"""知识库：``vault/notes`` 的只读目录（WP-VAULT-AGENTS-VIEW，用户 10-24 重排）。

用户把那面墙定成「知识库」，里面**只放笔记**——研究/课程的文档不进这里。
这个模块只做三件事：扫、给元数据、按路径读一篇正文；不建目录、不搬文件、不改笔记。

路径安全：对外只认 ``Vault/notes/...`` 这种逻辑路径，读之前再解析一次并确认落点在
``<VAULT_ROOT>/notes`` 之内——前端传来的字符串永远当不可信输入。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .paths import MindUnavailable, real_target

logger = logging.getLogger(__name__)

NOTES_LOGICAL_ROOT = "Vault/notes"
MAX_NOTES = 800
EXCERPT_CHARS = 120


def _notes_root() -> Path:
    return real_target(NOTES_LOGICAL_ROOT)


def _frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    try:
        parsed = yaml.safe_load(text[3:end])
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _title_of(text: str, stem: str, meta: dict[str, Any]) -> str:
    title = str(meta.get("title") or "").strip()
    if title:
        return title
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return stem


def _excerpt_of(text: str) -> str:
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end >= 0:
            body = text[end + 4 :]
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ">", "```", "---", "|", "![")):
            continue
        return line if len(line) <= EXCERPT_CHARS else line[: EXCERPT_CHARS - 1] + "…"
    return ""


def _updated_of(path: Path, meta: dict[str, Any]) -> str:
    stamped = str(meta.get("updated") or "").strip()
    if stamped:
        return stamped
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()
    except OSError:
        return ""


def _tags_of(meta: dict[str, Any]) -> list[str]:
    raw = meta.get("tags")
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.replace("，", ",").split(",")]
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(tag).strip() for tag in raw if str(tag).strip()]


def _belongs_of(meta: dict[str, Any]) -> list[str]:
    raw = meta.get("belongs")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(name).strip() for name in raw if str(name).strip()]


def _relative_of(path: Path, root: Path) -> str:
    return f"{NOTES_LOGICAL_ROOT}/{path.relative_to(root).as_posix()}"


def build_library() -> dict[str, Any]:
    """全部笔记的元数据 + 主题分组。读不到根时抛 MindUnavailable。"""
    root = _notes_root()
    if not root.is_dir():
        raise MindUnavailable(f"notes root does not exist: {root}")

    notes: list[dict[str, Any]] = []
    topics: dict[str, int] = {}
    scanned = skipped = 0
    for path in sorted(root.rglob("*.md")):
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        scanned += 1
        if len(notes) >= MAX_NOTES:
            skipped += 1
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            skipped += 1
            continue
        meta = _frontmatter(text)
        rel = path.relative_to(root)
        topic = rel.parts[0] if len(rel.parts) > 1 else ""
        label = topic or "未分类"
        topics[label] = topics.get(label, 0) + 1
        notes.append(
            {
                "path": _relative_of(path, root),
                "title": _title_of(text, path.stem, meta),
                "topic": label,
                "tags": _tags_of(meta),
                "belongs": _belongs_of(meta),
                "updated": _updated_of(path, meta),
                "chars": len(text),
                "excerpt": _excerpt_of(text),
            }
        )

    notes.sort(key=lambda note: (note["topic"], note["title"].lower()))
    return {
        "root": str(root),
        "notes": notes,
        "topics": [{"name": name, "count": count} for name, count in sorted(topics.items())],
        "tags": sorted({tag for note in notes for tag in note["tags"]}),
        "totals": {"scanned": scanned, "listed": len(notes), "skipped": skipped},
    }


def resolve_note(logical: str) -> Path | None:
    """把 ``Vault/notes/...`` 解析成盘上的文件；越界或不是 md 一律 None。"""
    raw = (logical or "").strip().replace("\\", "/")
    if not raw.startswith(NOTES_LOGICAL_ROOT + "/") or not raw.endswith(".md"):
        return None
    if ".." in raw.split("/"):
        return None
    root = _notes_root()
    try:
        candidate = real_target(raw).resolve()
        candidate.relative_to(root.resolve())
    except (MindUnavailable, ValueError, OSError):
        return None
    return candidate if candidate.is_file() else None


def read_note(logical: str) -> dict[str, Any] | None:
    path = resolve_note(logical)
    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return {
        "path": logical.strip().replace("\\", "/"),
        "fsPath": str(path),
        "text": text,
        "chars": len(text),
    }
