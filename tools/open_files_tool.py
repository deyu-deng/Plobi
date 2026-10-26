"""Read-only view of files the human has open in the Plobi desktop.

The desktop writes ``<root>/runtime/open-files.json`` whenever its right-rail
file tabs or dirty flags change. This tool lets an agent see that snapshot
before writing a file, so it can avoid clobbering unsaved human edits. It never
locks anything; the editor conflict/reload UI remains the enforcement layer.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List

from plobi_constants import get_default_plobi_root
from tools.registry import registry

SNAPSHOT_RELATIVE_PATH = Path("runtime") / "open-files.json"
STALE_AFTER_SECONDS = 600

LIST_OPEN_FILES_SCHEMA = {
    "name": "list_open_files",
    "description": (
        "List files currently open in the Plobi desktop's right rail, including which editor "
        "owns each file and whether the human has unsaved edits (dirty). Call this before "
        "overwriting or deleting a file the human may be editing. Read-only: it never saves, "
        "locks or changes anything. Office editors report dirty=null because their internal "
        "unsaved state is not exposed; treat that as 'unknown, ask before overwriting'."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}


def _snapshot_path() -> Path:
    return get_default_plobi_root() / SNAPSHOT_RELATIVE_PATH


def _empty(reason: str) -> str:
    return json.dumps({"files": [], "note": reason}, ensure_ascii=False)


def _handle_list_open_files(args: Any = None, **_kwargs: Any) -> str:
    path = _snapshot_path()

    if not path.is_file():
        return _empty(
            "No desktop session snapshot. The human may not be using the Plobi desktop, "
            "or the desktop has not opened a file yet."
        )

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty("The desktop session snapshot could not be read.")

    files: List[Dict[str, Any]] = []
    for item in payload.get("files") or []:
        if not isinstance(item, dict) or not item.get("path"):
            continue
        files.append(
            {
                "active": bool(item.get("active")),
                "dirty": item.get("dirty"),
                "editor": item.get("editor"),
                "path": item.get("path"),
                "title": item.get("title"),
            }
        )

    note = (
        "dirty=true means the human has unsaved edits in the text editor; do not overwrite "
        "without their decision. dirty=null means the editor does not expose dirty state; "
        "ask before replacing it."
    )
    updated_at = payload.get("updatedAt") or ""

    try:
        from datetime import datetime, timezone

        stamp = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
        age = time.time() - stamp.timestamp()
        if age > STALE_AFTER_SECONDS:
            note += " This snapshot is older than 10 minutes; ask the human which files are still open."
    except (TypeError, ValueError, OSError):
        pass

    return json.dumps(
        {
            "agentId": payload.get("agentId") or "",
            "files": files,
            "note": note,
            "updatedAt": updated_at,
            "workspace": payload.get("workspace") or "",
        },
        ensure_ascii=False,
    )


registry.register(
    name="list_open_files",
    toolset="file",
    schema=LIST_OPEN_FILES_SCHEMA,
    handler=_handle_list_open_files,
    emoji="📂",
    max_result_size_chars=20_000,
)
