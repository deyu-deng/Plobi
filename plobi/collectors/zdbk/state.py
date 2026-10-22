"""`zdbk` 的落盘状态：上一轮**什么时候真打过**。

节流那一格（`min_minutes_between_pulls`）必须有依据才知道"够不够久"，而依据不能是内存——
每天那一轮是新进程。形状照 `zju/config.py` 那一对（配置一份、状态一份，状态文件不在
仓里也不进 Docs）。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

STATE_FILENAME = "zdbk_state.json"


def state_path() -> Path:
    from .collector import config_path

    return config_path().with_name(STATE_FILENAME)


def read_state(path: Path | str | None = None) -> dict[str, Any]:
    target = Path(path) if path else state_path()
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def read_last_pull_at(path: Path | str | None = None) -> float | None:
    try:
        return float(read_state(path).get("last_pull_at_epoch"))
    except (TypeError, ValueError):
        return None


def write_state(record: dict, path: Path | str | None = None) -> Path:
    target = Path(path) if path else state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(target.parent),
                                         prefix=f".{STATE_FILENAME}.", suffix=".tmp", delete=False)
    try:
        with handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)
        os.replace(handle.name, target)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise
    return target
