"""对话外那张小卡片的前提：`memory.write_approval` 开着时，**各作用域**的写都先进待确认层。

用户 2026-10-30 定的形态（"像请求运行命令那样"）。以前只有 global 那样做，自己那份是
直接落盘 ⇒ 卡片永远不出现。这条钉三件：开着时落盘为零、点头后整份重放、关着时照旧直接写。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tools.memory_tool import apply_memory_pending, load_on_disk_store, memory_write


@pytest.fixture()
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "memory").mkdir(parents=True)
    monkeypatch.setenv("PLOBI_HOME", str(home))
    monkeypatch.setenv("PLOBI_AGENT_ID", "l2-course-x")
    return home


def _config(home: Path, approval: bool) -> None:
    (home / "config.yaml").write_text(
        f"memory:\n  write_approval: {'true' if approval else 'false'}\n", encoding="utf-8"
    )


def test_approval_on_stages_scope_writes_and_approval_replays_them(home):
    _config(home, True)

    out = json.loads(memory_write(scope="memory", parts=["理论力学周三有实验"]))

    assert out.get("staged") is True, out
    target = home / "memory" / "agents" / "l2-course-x" / "MEMORY.md"
    assert not target.exists(), "待确认期间一个字节都不该落盘"

    from tools import write_approval as wa

    record = wa.get_pending(wa.MEMORY, out["pending_id"])
    assert record is not None
    applied = apply_memory_pending(record["payload"], load_on_disk_store("l2-course-x"))
    assert applied.get("success") is True, applied
    assert "理论力学周三有实验" in target.read_text(encoding="utf-8")


def test_approval_off_keeps_the_direct_write(home):
    _config(home, False)

    out = json.loads(memory_write(scope="memory", parts=["直接记下"]))

    assert out.get("staged") is None, out
    target = home / "memory" / "agents" / "l2-course-x" / "MEMORY.md"
    assert "直接记下" in target.read_text(encoding="utf-8")
