"""日记：``diary/YYYY-MM-DD-Weekday.md`` 的读与写（WP-VAULT-AGENTS-VIEW）。

用户 10-24 拍：日记就在 Plobi 里写，存原位置，保存时自动提交一笔。
文件名与旧日记保持同一形状（日期 + 英文星期），正文仍是纯 markdown，不加 frontmatter——
历史那 18 篇就是这么存的，新写的不许换格式把旧的分成两类。

只写这一篇：提交走 pathspec，不碰同一仓里别人/别条线还没提交的改动。
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from datetime import date as _date
from datetime import datetime
from pathlib import Path
from typing import Any

from .paths import MindUnavailable, real_target

logger = logging.getLogger(__name__)

DIARY_LOGICAL_ROOT = "Diary"
MAX_DIARY_BYTES = 512 * 1024


def _diary_root() -> Path:
    return real_target(DIARY_LOGICAL_ROOT)


def parse_day(value: str) -> _date:
    """严格 ``YYYY-MM-DD``；别的写法一律 ValueError（不猜用户想的是哪天）。"""
    parsed = datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    if parsed.strftime("%Y-%m-%d") != value.strip():
        raise ValueError(f"not a YYYY-MM-DD date: {value!r}")
    return parsed


def filename_for(day: _date) -> str:
    return f"{day.strftime('%Y-%m-%d')}-{day.strftime('%A')}.md"


def _entry_path(day: _date, root: Path | None = None) -> Path:
    return (root or _diary_root()) / filename_for(day)


def list_entries() -> dict[str, Any]:
    root = _diary_root()
    entries: list[dict[str, Any]] = []
    if root.is_dir():
        for path in root.glob("*.md"):
            stem = path.stem
            day = stem[:10]
            try:
                parsed = parse_day(day)
            except ValueError:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append(
                {
                    "date": day,
                    "weekday": parsed.strftime("%A"),
                    "filename": path.name,
                    "chars": stat.st_size,
                    "updated": datetime.fromtimestamp(stat.st_mtime).date().isoformat(),
                }
            )
    entries.sort(key=lambda row: row["date"], reverse=True)
    return {"root": str(root), "entries": entries, "today": _date.today().strftime("%Y-%m-%d")}


def read_entry(value: str) -> dict[str, Any]:
    day = parse_day(value)
    path = _entry_path(day)
    exists = path.is_file()
    text = ""
    updated = ""
    if exists:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            updated = datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()
        except OSError:
            exists = False
    return {
        "date": day.strftime("%Y-%m-%d"),
        "weekday": day.strftime("%A"),
        "filename": filename_for(day),
        "exists": exists,
        "text": text,
        "updated": updated,
    }


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )


def _commit_one(root: Path, relative: str, day: _date) -> dict[str, Any]:
    """只提交这一篇。返回 {committed, commit, detail}——失败不抛，交给界面说。"""
    inside = _git(root, "rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return {"committed": False, "commit": "", "detail": "diary root is not a git repository"}
    status = _git(root, "status", "--porcelain", "--", relative)
    if status.returncode != 0:
        return {"committed": False, "commit": "", "detail": status.stderr.strip() or "git status failed"}
    added = _git(root, "add", "--", relative)
    if added.returncode != 0:
        return {"committed": False, "commit": "", "detail": added.stderr.strip() or "git add failed"}
    # ``git status -- <file>`` reports nothing for a file inside a brand-new
    # directory (the whole dir is one untracked entry), so the "is there
    # anything to commit" question is asked of the index after `add`.
    staged = _git(root, "diff", "--cached", "--quiet", "--", relative)
    if staged.returncode == 0:
        return {"committed": False, "commit": "", "detail": "unchanged"}
    if staged.returncode != 1:
        return {"committed": False, "commit": "", "detail": staged.stderr.strip() or "git diff failed"}
    committed = _git(root, "commit", "-m", f"diary: {day.strftime('%Y-%m-%d')}", "--", relative)
    if committed.returncode != 0:
        return {
            "committed": False,
            "commit": "",
            "detail": (committed.stderr or committed.stdout).strip() or "git commit failed",
        }
    head = _git(root, "rev-parse", "--short", "HEAD")
    return {"committed": True, "commit": head.stdout.strip(), "detail": ""}


def save_entry(value: str, text: str, *, commit: bool = True, base_text: str | None = None) -> dict[str, Any]:
    day = parse_day(value)
    encoded = (text or "").encode("utf-8")
    if len(encoded) > MAX_DIARY_BYTES:
        raise ValueError(f"diary entry is too large ({len(encoded)} bytes > {MAX_DIARY_BYTES})")
    root = _diary_root()
    root.mkdir(parents=True, exist_ok=True)
    path = _entry_path(day, root)
    merged: list[str] = []
    if base_text is not None and path.is_file():
        try:
            merged = _lines_added_on_disk(path.read_text(encoding="utf-8", errors="replace"), base_text, text)
        except OSError:
            merged = []
        if merged:
            text = (text.rstrip("\n") + "\n\n" + "\n".join(merged) + "\n") if text.strip() else "\n".join(merged) + "\n"
    body = text if text.endswith("\n") else text + "\n"
    # 先写同目录临时文件再换名：写到一半崩掉不会留下半篇日记。
    handle, tmp = tempfile.mkstemp(prefix=".diary-", suffix=".tmp", dir=str(root))
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(body.encode("utf-8"))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    result = {"committed": False, "commit": "", "detail": ""}
    if commit:
        try:
            # git 的相对路径是相对人读面根的仓根，不是逻辑前缀 `Diary/`。
            result = _commit_one(root, path.relative_to(root).as_posix(), day)
        except Exception as exc:  # git 不可用不该吞掉已经落盘的那篇
            logger.warning("diary: commit skipped: %s", exc)
            result = {"committed": False, "commit": "", "detail": str(exc)}
    return {
        "date": day.strftime("%Y-%m-%d"),
        "weekday": day.strftime("%A"),
        "filename": path.name,
        "fsPath": str(path),
        "bytes": len(body.encode("utf-8")),
        "merged": len(merged),
        **result,
    }


def _lines_added_on_disk(disk: str, base_text: str, incoming: str) -> list[str]:
    """编辑器打开之后盘上多出来的行（就是浮层快记那几条），合并回这次要写的正文。

    不这么做会丢字：人开着今天这篇在写，中间按快捷键记了一句，编辑器保存是**整篇覆盖**，
    刚记的那句就没了。按行比对、保留盘上顺序，已经在新正文里的不重复塞。
    """
    known = {line.strip() for line in base_text.splitlines() if line.strip()}
    fresh = {line.strip() for line in incoming.splitlines() if line.strip()}
    return [
        line
        for line in disk.splitlines()
        if line.strip() and line.strip() not in known and line.strip() not in fresh
    ]


def append_entry(value: str, line: str, *, commit: bool = True) -> dict[str, Any]:
    """往当天那篇**末尾**接一行 ``- HH:MM 内容``（用户 10-26：「接在当天的日记之后」）。"""
    text = (line or "").strip()
    if not text:
        raise ValueError("a capture needs a non-empty line")
    if "\n" in text or "\r" in text:
        raise ValueError("a capture must be one line")
    day = parse_day(value)
    key = day.strftime("%Y-%m-%d")
    entry = read_entry(key)
    addition = f"- {datetime.now().strftime('%H:%M')} {text}"
    body = entry["text"]
    if body.strip():
        merged_text = body.rstrip("\n") + "\n" + addition + "\n"
    else:
        merged_text = addition + "\n"
    result = save_entry(key, merged_text, commit=commit)
    result["line"] = addition
    return result
