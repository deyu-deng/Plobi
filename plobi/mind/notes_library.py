"""知识库：``Notes`` 的目录、读取与**人工写入**（WP-VAULT-AGENTS-VIEW / WP-NOTES-WORKBENCH）。

用户把那面墙定成「知识库」，里面**只放笔记**——研究/课程的文档不进这里，只有标题的壳也不进（用户 10-27：「只留有内容的」）。
扫、给元数据、按路径读一篇正文，以及人在笔记栏里编辑后整篇存回（2026-11-04：「Plobi 也可以编辑，
我以后不想打开 Obsidian 了」）。不搬文件、不删笔记、不建主题以外的东西。

路径安全：对外只认 ``Notes/...`` 这种逻辑路径（旧的 ``Surface/notes/...`` 也认，进来先归一），
读**和写**之前都再解析一次并确认落点在 ``<HUMAN_ROOT>/notes`` 之内——前端传来的字符串永远当不可信输入。

注意 :mod:`plobi.mind.notes_contract` 那道闸**只管 Agent 写**，明写「不管人自己写」；
所以 :func:`save_note` 不调用 ``validate_note``。人写的笔记可以没有 frontmatter、可以有多个
一级标题，那是人的自由；但路径越界、写坏文件、覆盖别人刚改过的字，这里一律拦。
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .notes_contract import INDEX_FILE_NAMES
from .paths import MindUnavailable, normalize_logical, real_target

logger = logging.getLogger(__name__)

NOTES_LOGICAL_ROOT = "Notes"
MAX_NOTES = 800
EXCERPT_CHARS = 120
# 一条「是不是壳」的形状线，不是内容好坏的评分线：只有标题、或标题下一句「待补」的那种不进屏。
# 真正薄的那 9 篇是用户 2026-10-27 逐档勾掉删的（surface `5ef55ab`），别指望这条线替那次判断。
# 这台现存笔记最低那一档是 1890 净字（ArkTS），所以这条线今天一篇都不藏。
MIN_NOTE_NET_CHARS = 30


def _net_chars(text: str) -> int:
    """正文净字：去掉 frontmatter、围栏代码块与标题行，只数真写了的字。

    讲 Markdown／Obsidian 的那几篇会有几十行示范代码，按文件字节判会把壳判成有内容。
    """
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end >= 0:
            body = text[end + 4 :]
    total = 0
    inside = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped[:3] in ("```", "~~~"):
            inside = not inside
            continue
        if inside or not stripped or stripped.startswith("#"):
            continue
        total += len(stripped)
    return total


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
    scanned = skipped = hidden = 0
    for path in sorted(root.rglob("*.md")):
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        if path.name in INDEX_FILE_NAMES:
            continue  # 目录件不是笔记——混进来会在知识库上多出一个「未分类」
        scanned += 1
        if len(notes) >= MAX_NOTES:
            skipped += 1
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            skipped += 1
            continue
        if _net_chars(text) < MIN_NOTE_NET_CHARS:
            hidden += 1  # 壳不进屏，但数得出来：藏了没藏是要能核的一句
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
        "totals": {"scanned": scanned, "listed": len(notes), "skipped": skipped, "hidden": hidden},
    }


def _safe_note_target(logical: str) -> Path | None:
    """纯路径校验：逻辑前缀 / .md / 无 ``..`` 与隐藏段 / 落点仍在 notes 根内。

    读和写共用**同一份**判据——两处各写一套，早晚漂成「读得到但写不进去」或反过来。
    前端传来的字符串永远当不可信输入。
    """
    raw = normalize_logical((logical or "").strip().replace("\\", "/"))
    if not raw.startswith(NOTES_LOGICAL_ROOT + "/") or not raw.endswith(".md"):
        return None
    parts = raw.split("/")
    if any(part in ("", ".", "..") or part.startswith(".") for part in parts):
        return None
    root = _notes_root()
    try:
        candidate = real_target(raw).resolve()
        candidate.relative_to(root.resolve())
    except (MindUnavailable, ValueError, OSError):
        return None
    return candidate


def resolve_note(logical: str) -> Path | None:
    """把 ``Notes/...`` 解析成盘上的文件；越界或不是 md 一律 None。"""
    candidate = _safe_note_target(logical)
    return candidate if candidate is not None and candidate.is_file() else None


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


# 现存最长的一篇约 208 KB（《人工智能基础》），留足余量但不许把几个 GB 塞进来。
MAX_NOTE_BYTES = 4 * 1024 * 1024
_GIT_TIMEOUT = 30


class NoteConflict(RuntimeError):
    """编辑器打开之后，盘上这篇被别的程序改过了——不许整篇覆盖掉那些字。"""


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )


def _commit_note(root: Path, relative: str) -> dict[str, Any]:
    """只提交这一篇。git 不可用／没变化都不算保存失败，落盘已经完成了。"""
    blank = {"committed": False, "commit": "", "detail": ""}
    try:
        inside = _git(root, "rev-parse", "--is-inside-work-tree")
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return {**blank, "detail": "notes root is not a git repository"}
        added = _git(root, "add", "--", relative)
        if added.returncode != 0:
            return {**blank, "detail": added.stderr.strip() or "git add failed"}
        staged = _git(root, "diff", "--cached", "--quiet", "--", relative)
        if staged.returncode == 0:
            return {**blank, "detail": "unchanged"}
        if staged.returncode != 1:
            return {**blank, "detail": staged.stderr.strip() or "git diff failed"}
        done = _git(root, "commit", "-m", f"note: {relative}", "--", relative)
        if done.returncode != 0:
            return {**blank, "detail": (done.stderr or done.stdout).strip() or "git commit failed"}
        head = _git(root, "rev-parse", "--short", "HEAD")
        return {"committed": True, "commit": head.stdout.strip(), "detail": ""}
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("notes: commit skipped: %s", exc)
        return {**blank, "detail": str(exc)}


def save_note(
    logical: str,
    text: str,
    *,
    base_text: str | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """把人在笔记栏里写的整篇存回 ``Notes/...``；文件不存在就是新建。

    ``base_text`` 是编辑器打开时那份原文。带上它，保存前先比一次盘上现状：
    长笔记**不做**日记那种「按行合并」——2919 行的文章按行猜哪些是别人新加的，
    猜错的代价是丢字，所以盘上不一致就直接拒绝，让人自己决定怎么处理。

    不调用 :func:`plobi.mind.notes_contract.validate_note`：那道闸管的是 Agent 写，
    模块头注里明写「不管人自己写」。人写的笔记可以没有 frontmatter、可以有多个
    一级标题；这里只管路径安全、原子写、不覆盖别人的改动。
    """
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    path = _safe_note_target(logical)
    if path is None:
        raise ValueError(f"note path is outside {NOTES_LOGICAL_ROOT}/ or is not a .md file")
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_NOTE_BYTES:
        raise ValueError(f"note is too large ({len(encoded)} bytes > {MAX_NOTE_BYTES})")

    existed = path.is_file()
    if base_text is not None:
        if existed:
            try:
                disk = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                raise ValueError(f"cannot re-read {path} before saving: {exc}") from exc
            if disk != base_text:
                raise NoteConflict("笔记在别处被改过了，先重新打开再看一眼")
        elif base_text.strip():
            # 前端以为在编辑一篇，盘上却没有——多半是被删/被改名了。
            raise NoteConflict("这篇笔记已经不在盘上了，先重新打开再看一眼")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"cannot create note directory: {exc}") from exc

    # 原样写回，不补末尾换行、不改 frontmatter、不碰 [[wikilink]] 与代码块。
    handle, tmp = tempfile.mkstemp(prefix=".note-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(encoded)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    root = _notes_root().resolve()
    result = _commit_note(root, path.relative_to(root).as_posix()) if commit else {
        "committed": False,
        "commit": "",
        "detail": "",
    }
    return {
        "path": logical.strip().replace("\\", "/"),
        "fsPath": str(path),
        "bytes": len(encoded),
        "created": not existed,
        **result,
    }


def note_template(title: str) -> str:
    """新建笔记的起手式：字段给齐，但**不替人决定**归属与关联。"""
    clean = (title or "").strip() or "未命名"
    return f"---\ntitle: {clean}\ntags: []\nbelongs: []\n---\n\n# {clean}\n\n"