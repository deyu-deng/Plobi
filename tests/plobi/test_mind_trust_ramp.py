"""写脑那道闸的「会移动的门槛」：ramp、驳回归零、永不自动那三样（裁定 199）。

用户 10-23 四拍：除永不自动外全开／连续 8 次／驳回归零重攒／永不自动＝日记＋画像＋删除。
自动档还要 config 单独点头（`mind_write_auto_approve`，出厂关）——这是这台机器第一次
把「从此它可以不问就写」交给系统，默认关不是保守，是不让一次代码改动替用户做那个决定。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plobi.mind import proposal as L1
from plobi.mind.writer import MindWriter, WriteRequest

AGENDA = "Vault/projects/Plobi/ai-daily/2026-10-23/agenda.md"


@pytest.fixture()
def roots(tmp_path, monkeypatch):
    mind = tmp_path / "Mind"
    vault = tmp_path / "Vault"
    (vault / "projects" / "Plobi").mkdir(parents=True)
    (vault / "diary").mkdir()
    (vault / "meta" / "profile").mkdir(parents=True)
    mind.mkdir()
    home = tmp_path / "home"
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("VAULT_ROOT", str(vault))
    monkeypatch.setenv("PLOBI_HOME", str(home))
    return vault, home


def _writer(auto: bool) -> MindWriter:
    return MindWriter(require_approval=None, commit=False, auto_approve=auto)


def test_diary_and_persona_are_never_auto(roots, tmp_path):
    """攒再多也不放行：日记与画像的「改写过去」不在自动档里（永不自动）。"""
    vault, home = roots
    ledger = L1.TrustLedger(path=home / "l1-trust.json")
    for _ in range(30):
        ledger.record("adopted", "diary", "l1")
        ledger.record("adopted", "persona", "l1")
        ledger.record("adopted", L1.category_of(AGENDA), "l1")

    assert _writer(True)._gate_refusals(
        [WriteRequest("Vault/diary/2026-04-16-Thursday.md", "追加一段")]
    ) != []
    assert _writer(True)._gate_refusals(
        [WriteRequest("Vault/meta/profile/profile.md", "改画像")]
    ) != []
    # 同一格攒满 30 次 ⇒ 不再拦（这才叫「会移动的门槛」）。类别由路径定，别猜名字。
    assert _writer(True)._gate_refusals([WriteRequest(AGENDA, "摘要")]) == []


def test_auto_stays_off_until_the_config_says_yes(roots):
    """ramp 到位、但没开自动档 ⇒ 仍然先问。默认值是「先问」，不是「自动」。"""
    _vault, home = roots
    ledger = L1.TrustLedger(path=home / "l1-trust.json")
    for _ in range(30):
        ledger.record("adopted", L1.category_of(AGENDA), "l1")

    refusals = _writer(False)._gate_refusals([WriteRequest(AGENDA, "摘要")])
    assert refusals and "自动档没开" in refusals[0], refusals


def test_one_rejection_falls_back_to_asking(roots):
    """驳回归零：攒满 8 次那一类被驳一次，立刻回到每次都问（且账上留得下原因）。"""
    _vault, home = roots
    ledger = L1.TrustLedger(path=home / "l1-trust.json")
    cat = L1.category_of(AGENDA)
    for _ in range(L1.AUTO_THRESHOLD):
        ledger.record("adopted", cat, "l1")
    assert _writer(True)._gate_refusals([WriteRequest(AGENDA, "摘要")]) == []

    ledger.record("rejected", cat, "l1", "周次写错")
    assert _writer(True)._gate_refusals([WriteRequest(AGENDA, "摘要")]) != []
    assert L1.TrustLedger(path=home / "l1-trust.json").entry(cat, "l1").rejected == 1


def test_delete_style_ops_have_no_automatic_route_yet(roots):
    """「删除」这一样：还没有删除操作，等它出现时必须走 never_auto，别自动替用户删东西。

    钉的是判据接口，不是行为：`never_auto` 对未知路径返回空串（=可以自动），
    所以真正拦删除的地方是「writer 里没有 delete 这条腿」。这条测试保证哪天有人加删除，
    必须同时给 never_auto 加一条，否则这里会红。
    """
    assert L1.never_auto("Vault/diary/x.md", "overwrite") == "rewrites-the-user's-own-record"
    assert L1.never_auto("Vault/projects/Plobi/ai-daily/a.md", "overwrite") == ""
    assert "delete" not in L1.MODES, "加了删除操作就得同步给 never_auto 补一条，不能让它自动"
