"""L1 那道闸：写脑先出计划＋每档 sha256＋对象身份，点头才落（裁定 156／198）。

钉的是关系，不是快照：没点头 ⇒ 一个字节都不落盘；点过头 ⇒ 内容与哈希对得上才落；
换了一张纸（内容变过／对象变过／过期）⇒ 拒；驳回 ⇒ 那一类立刻退回先问。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from plobi.mind import proposal as L1
from plobi.mind.paths import resolve_roots
from plobi.mind.writer import MindWriter


@pytest.fixture()
def roots(tmp_path, monkeypatch):
    mind = tmp_path / "Mind"
    vault = tmp_path / "Vault"
    (vault / "projects" / "Plobi").mkdir(parents=True)
    mind.mkdir()
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("VAULT_ROOT", str(vault))
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path / "home"))
    return mind, vault


def _stage(roots, content: str = "# 摘要\n", rel: str = "Vault/projects/Plobi/ai-daily/2026-10-23/agenda.md"):
    writer = MindWriter(require_approval=True, commit=False)
    return writer.write_one(rel, content), rel


def _pending_records(tmp_path) -> list[dict]:
    d = Path(os.environ["PLOBI_HOME"]) / "pending" / "memory"
    if not d.is_dir():
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.json"))]


def test_gate_stages_a_plan_and_writes_nothing(roots, tmp_path):
    result, rel = _stage(roots)

    assert result.ok is False
    assert result.staged, "没给计划摘要 = 这道闸没起作用"
    assert result.pending_id, "没进待确认层，人就没处点头"
    assert not (Path(os.environ["VAULT_ROOT"]) / "projects/Plobi/ai-daily/2026-10-23/agenda.md").exists()
    records = _pending_records(tmp_path)
    assert len(records) == 1 and records[0]["payload"]["kind"] == "mind_plan"
    plan = L1.Plan.from_json(records[0]["payload"]["plan"])
    assert plan.digest == result.staged
    assert plan.changes[0].logical_path == rel


def test_approving_lands_the_file_and_clears_the_pending(roots, tmp_path):
    result, _rel = _stage(roots)
    record = _pending_records(tmp_path)[0]
    plan = L1.Plan.from_json(record["payload"]["plan"])

    report = L1.apply_plan(
        plan, approved=plan.digest, roots=resolve_roots(),
        writer=MindWriter(require_approval=False, commit=False),
    )

    assert report.ok, report
    target = Path(os.environ["VAULT_ROOT"]) / "projects/Plobi/ai-daily/2026-10-23/agenda.md"
    assert target.read_text(encoding="utf-8") == "# 摘要\n"


def test_a_plan_whose_content_moved_under_it_is_refused(roots, tmp_path):
    """人先改过那一档 ⇒ 旧 sha256 对不上 ⇒ 拒，且不覆盖人写的东西。"""
    result, _rel = _stage(roots)
    record = _pending_records(tmp_path)[0]
    plan = L1.Plan.from_json(record["payload"]["plan"])
    target = Path(os.environ["VAULT_ROOT"]) / "projects/Plobi/ai-daily/2026-10-23"
    target.mkdir(parents=True, exist_ok=True)
    (target / "agenda.md").write_text("人先写的\n", encoding="utf-8")

    report = L1.apply_plan(
        plan, approved=plan.digest, roots=resolve_roots(),
        writer=MindWriter(require_approval=False, commit=False),
    )

    assert report.ok is False
    assert (target / "agenda.md").read_text(encoding="utf-8") == "人先写的\n"


def test_rejection_sends_that_class_back_to_asking(roots, tmp_path):
    """驳回不是「丢掉一张纸」：那一类进信任账，下一次仍然先问（且 streak 归零）。"""
    result, _rel = _stage(roots)
    plan = L1.Plan.from_json(_pending_records(tmp_path)[0]["payload"]["plan"])
    ledger = L1.TrustLedger(path=Path(os.environ["PLOBI_HOME"]) / "l1-trust.json")
    for cat in set(plan.categories.values()) or {"other"}:
        ledger.record("adopted", cat, "l1")

    L1.record_rejection(plan, ledger=ledger)

    for cat in set(plan.categories.values()) or {"other"}:
        assert L1.TrustLedger(path=ledger.path).should_ask(cat, "l1") is True
