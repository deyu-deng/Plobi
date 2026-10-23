"""日程附件：把用户选中的文件**复制进 Plobi 家目录**，再挂到那一条 events 行上。

为什么是复制而不是只存原路径：原文件被挪走/删掉、或换一台机器打开时，附件就成了一句
死链接。复制进 ``PLOBI_HOME/attachments/agenda/<event_id>/`` 后它随运行数据一起备份，
也不会因为用户整理桌面而消失。

边界只写在这一处：
- 只收**普通文件**，单个上限 50 MB（超过就明说，不偷偷截断）；
- 落点永远在 attachments 根下（删除时也先校验这一点，绝不 unlink 用户的原文件）；
- 失败时不留下半条元数据：先复制、再写行，写行失败就把副本删掉。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from . import store
from .store import AgendaValidationError, Event

#: 单个附件上限。超过就拒绝并给一句人话——静默截断比拒绝更难查。
MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024


def attachments_root() -> Path:
    """``PLOBI_HOME/attachments/agenda``；每次现读，测试能钉 PLOBI_HOME。"""
    from plobi_constants import get_plobi_home

    return get_plobi_home() / "attachments" / "agenda"


def _safe_name(name: str) -> str:
    text = Path(name).name.strip().replace("\x00", "")
    return text or "attachment"


def _inside_root(path: Path) -> bool:
    try:
        return path.resolve().is_relative_to(attachments_root().resolve())
    except (OSError, ValueError):
        return False


def add_attachment(
    conn: Any,
    event: Event,
    source_path: str,
    *,
    name: Optional[str] = None,
) -> Event:
    """复制 ``source_path`` 到这条日程自己的目录，并把元数据写回 events 行。"""
    source = Path(str(source_path or "").strip())
    if not source.is_file():
        raise AgendaValidationError(f"附件不是一个可读的文件：{source_path!r}")

    size = source.stat().st_size
    if size > MAX_ATTACHMENT_BYTES:
        raise AgendaValidationError(
            f"附件超过 {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB，换一个小一点的文件"
        )

    target_dir = attachments_root() / event.id
    target_dir.mkdir(parents=True, exist_ok=True)
    target_name = f"{uuid4().hex[:10]}-{_safe_name(name or source.name)}"
    target = target_dir / target_name

    try:
        shutil.copy2(source, target)
    except OSError as exc:
        raise AgendaValidationError(f"附件复制失败：{type(exc).__name__}: {exc}") from exc

    metadata = {
        "id": uuid4().hex,
        "name": _safe_name(name or source.name),
        "size": size,
        "path": str(target),
        "added_at": store.now_iso(),
    }
    try:
        updated = store.update_event(
            conn, event.id, attachments=[*(event.attachments or []), metadata]
        )
    except Exception:
        target.unlink(missing_ok=True)
        raise
    assert updated is not None
    return updated


def remove_attachment(conn: Any, event: Event, attachment_id: str) -> Event:
    """摘掉元数据；只有落点确实在 attachments 根下才删副本。"""
    target = next(
        (row for row in (event.attachments or []) if str(row.get("id")) == str(attachment_id)),
        None,
    )
    if target is None:
        raise AgendaValidationError(f"这条日程没有附件 {attachment_id!r}")

    remaining = [row for row in (event.attachments or []) if str(row.get("id")) != str(attachment_id)]
    updated = store.update_event(conn, event.id, attachments=remaining)
    assert updated is not None

    stored = Path(str(target.get("path") or ""))
    if stored and _inside_root(stored):
        stored.unlink(missing_ok=True)
        try:
            stored.parent.rmdir()
        except OSError:
            pass
    return updated


def delete_event_attachments(event_id: str) -> int:
    """删掉这条日程自己的附件目录（事件删除后调用）。返回删掉的副本数。"""
    folder = attachments_root() / event_id
    if not folder.is_dir() or not _inside_root(folder):
        return 0
    count = 0
    for child in folder.iterdir():
        if child.is_file() and _inside_root(child):
            child.unlink(missing_ok=True)
            count += 1
    try:
        folder.rmdir()
    except OSError:
        pass
    return count
