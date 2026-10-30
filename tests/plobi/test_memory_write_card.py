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


def test_trust_ramp_stops_asking_after_enough_adoptions(home):
    """用户 2026-10-30「开信任帐」：同一作用域连续采纳够 8 次后，不再逐条问。

    三条硬边界同时钉住：画像永不自动；删条目永不自动（哪怕账已经攒够）。
    """
    import json as _json

    from plobi.mind.proposal import AUTO_THRESHOLD, TrustLedger

    _config(home, True)
    ledger = TrustLedger()
    for _ in range(AUTO_THRESHOLD):
        ledger.record("adopted", "memory", "l0")

    out = _json.loads(memory_write(scope="memory", parts=["第九次之后直接落"]))
    assert out.get("staged") is None, out
    assert "第九次之后直接落" in (home / "memory" / "agents" / "l2-course-x" / "MEMORY.md").read_text(encoding="utf-8")

    # 画像：账攒得再多也要问
    for _ in range(AUTO_THRESHOLD * 2):
        ledger.record("adopted", "user", "l0")
    assert _json.loads(memory_write(scope="user", parts=["画像一条"])).get("staged") is True

    # 删条目：也要问（少了一条必须有人看见）
    staged = _json.loads(memory_write(scope="memory", parts=[]))
    assert staged.get("staged") is True, staged


def test_approval_lands_in_the_agent_the_paper_belongs_to(home):
    """真进程联调抓到的那一处：批的是 A 的纸，却写进默认 store（l1）的文件里。

    待批 payload 带 `agent_slug`；点头那一方必须按它重建 store——哪怕当前进程没有
    `PLOBI_AGENT_ID`（网关/HTTP 就是这样）。
    """
    import json as _json

    from plobi_cli.write_approval_commands import _apply_one
    from tools import write_approval as wa
    from tools.memory_tool import load_on_disk_store

    _config(home, True)
    staged = _json.loads(memory_write(scope="memory", parts=["属于这门课的一条"]))
    record = wa.get_pending(wa.MEMORY, staged["pending_id"])
    assert record["payload"].get("agent_slug") == "l2-course-x", record["payload"].keys()

    # 换一个"没有身份"的进程视角来点头（default store 的 slug 会是 l1）
    import os

    os.environ.pop("PLOBI_AGENT_ID", None)
    ok, msg = _apply_one(wa.MEMORY, record, load_on_disk_store())

    assert ok is True, msg
    assert "属于这门课的一条" in (home / "memory" / "agents" / "l2-course-x" / "MEMORY.md").read_text(encoding="utf-8")
    assert not (home / "memory" / "agents" / "l1" / "MEMORY.md").exists(), "别落到默认身份那份里"
