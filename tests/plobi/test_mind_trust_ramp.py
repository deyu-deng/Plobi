"""写脑那道闸的「会移动的门槛」：ramp、驳回归零、永不自动那三样（裁定 199）。

用户 10-23 四拍：除永不自动外全开／连续 8 次／驳回归零重攒／永不自动＝日记＋画像＋删除。
自动档还要 config 单独点头（`mind_write_auto_approve`，出厂关）——这是这台机器第一次
把「从此它可以不问就写」交给系统，默认关不是保守，是不让一次代码改动替用户做那个决定。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plobi.mind import note_manage as NM
from plobi.mind import proposal as L1
from plobi.mind.writer import MindWriter, WriteRequest

AGENDA = "Mind/wiki/daily/2026-10-23.md"


@pytest.fixture()
def roots(tmp_path, monkeypatch):
    mind = tmp_path / "Mind"
    surface = tmp_path / "Surface"
    (surface / "dossiers" / "projects" / "Plobi").mkdir(parents=True)
    (surface / "diary").mkdir()
    (surface / "meta" / "profile").mkdir(parents=True)
    mind.mkdir()
    home = tmp_path / "home"
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("SURFACE_ROOT", str(surface))
    monkeypatch.setenv("PLOBI_HOME", str(home))
    return surface, home


def _writer(auto: bool) -> MindWriter:
    return MindWriter(require_approval=None, commit=False, auto_approve=auto)


def test_diary_and_persona_are_never_auto(roots, tmp_path):
    """攒再多也不放行：日记与画像的「改写过去」不在自动档里（永不自动）。"""
    surface, home = roots
    ledger = L1.TrustLedger(path=home / "l1-trust.json")
    for _ in range(30):
        ledger.record("adopted", "diary", "l1")
        ledger.record("adopted", "persona", "l1")
        ledger.record("adopted", L1.category_of(AGENDA), "l1")

    assert _writer(True)._gate_refusals(
        [WriteRequest("Surface/diary/2026-04-16-Thursday.md", "追加一段")]
    ) != []
    assert _writer(True)._gate_refusals(
        [WriteRequest("Surface/meta/profile/profile.md", "改画像")]
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
    """「删除」这一样：它现在**有**那条腿了，但**永不自动**。

    钉的是判据接口，不是行为。删除与改名住在 ``plobi.mind.note_manage``（人读面那一族），
    **不是** L1 的写腿 ⇒ ``L1.MODES`` 里仍然只有「写内容」那几种（``Change`` 装上未知模式
    就报错 ⇒ 删除压根进不了 L1 那条路；``never_auto`` 对未知模式本来就拒）。
    真正让删除安全的是 note_manage 那一族**没有自动档**：``NEVER_AUTO_KINDS`` 里有它，
    Agent 调它只会拿到一张待批的纸（用户 10-23：永不自动＝日记＋画像＋删除）。
    """
    assert L1.never_auto("Surface/diary/x.md", "overwrite") == "rewrites-the-user's-own-record"
    assert L1.never_auto("Mind/wiki/daily/a.md", "overwrite") == ""
    assert "delete" not in L1.MODES, "L1 的写腿仍然只写内容——删除走 note_manage 那一族"
    assert L1.never_auto("Notes/x.md", "delete") != "", "未知模式本来就拒"
    assert set(NM.NEVER_AUTO_KINDS) >= {"delete", "rename"}, "删除与改名永不自动"
