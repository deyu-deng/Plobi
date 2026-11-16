"""边界层那一刀（`WP-MIND-BOUNDARY-POLICY`，规格 §3.3／§3.4）。

钉的是「什么时候可以闭嘴」这件事的**下限**，不是它多聪明：

1. 硬下限（日记／画像／删除）攒多少、有没有政策，都翻不过去；
2. 慢晋升（N=5）、快降级（一次驳回即归零）；
3. 熔断（7 天内 3 次驳回 ⇒ 全部交回人），且窗口外的旧账不算；
4. 静默必须留 trace；留不下痕的静默按必问办；
5. 否决器**只能收紧**——规则判必问时它压根不该被调用；
6. 政策候选永不自动生效：起草之后正典一个字不变。

零模型调用：判定全在规则里，`reviewer=` 只是一个可选的口子。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from plobi.mind import boundary as B
from plobi.mind import preferences as P
from plobi.mind import proposal as L1
from plobi.mind.consent_events import ConsentLedger
from plobi.mind.paths import real_target, resolve_roots
from plobi.mind.writer import MindWriter

DAY = 86400.0


@pytest.fixture()
def env(tmp_path, monkeypatch):
    mind = tmp_path / "Mind"
    surface = tmp_path / "Surface"
    (mind / "wiki" / "memory").mkdir(parents=True)
    (surface / "dossiers" / "projects").mkdir(parents=True)
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("HUMAN_ROOT", str(surface))
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PLOBI_CONSENT_DB", str(tmp_path / "state" / "consent.db"))
    return ConsentLedger(), tmp_path / "state" / "consent.db"


def _canon() -> Path:
    return real_target(P.PREFERENCES_LOGICAL, resolve_roots())


def _grant(category: str, *, state: str = "active") -> None:
    """模拟「人点头过一条政策」之后正典该有的样子（等价于 approve 之后的状态）。"""
    path = _canon()
    path.parent.mkdir(parents=True, exist_ok=True)
    base = path.read_text(encoding="utf-8") if path.is_file() else P._HEADER
    block = (
        f"\n- 类别 `{category}` 的写入：可以不问。\n"
        f"  <!-- pol:promote:{category} category:{category} state:{state} evidence:cev_seed -->\n"
    )
    if B.POLICY_SECTION not in base:
        base = base.rstrip("\n") + "\n\n" + B.POLICY_SECTION + "\n" + block
    else:
        base = base.rstrip("\n") + "\n" + block
    path.write_text(base, encoding="utf-8")


def _adopt(book, category: str, n: int = B.N_ADOPT, *, ts: float | None = None) -> None:
    for _ in range(n):
        book.record(f"Mind/wiki/{category}/a.md", category, "adopted", ts=ts)


def test_the_write_side_ramp_is_not_the_l0_ramp():
    """两个门槛各有主人：L1 写入面 5（11-16 拍），L0 汇报信任仍是 8。

    §6「不合并账本」——把 L1 的 5 抄到 `AUTO_THRESHOLD` 上会顺手改掉 L0 的行为，
    那一改没有裁定支撑。
    """
    assert B.N_ADOPT == 5
    assert L1.AUTO_THRESHOLD == 8
    assert B.N_ADOPT != L1.AUTO_THRESHOLD


def test_never_auto_cannot_be_promoted_however_long_the_streak(env):
    """硬下限：日记攒 100 次、政策也点头了，仍然必问（规格 §3.3「不可晋升」）。"""
    book, _db = env
    _adopt(book, "diary", 100)
    _grant("diary")

    verdict = B.evaluate(book, "diary", logical="Mind/wiki/diary/2026-11-16.md")

    assert verdict.silent is False
    assert "永不自动" in verdict.reason
    assert verdict.trace["rules"][0]["rule"] == "never-auto"


def test_a_short_streak_keeps_asking(env):
    """攒了 3 次还不够（N=5）；政策也点头了也不行——顺序是 ramp 先说话。"""
    book, _db = env
    _adopt(book, "wiki", 3)
    _grant("wiki")

    verdict = B.evaluate(book, "wiki", logical="Mind/wiki/daily/a.md")

    assert verdict.silent is False
    assert "3/5" in verdict.reason


def test_a_full_streak_without_a_policy_still_asks(env):
    """ramp 攒够了但没有政策授权 ⇒ 仍然问。攒次数不等于拿到许可。"""
    book, _db = env
    _adopt(book, "wiki", B.N_ADOPT)

    verdict = B.evaluate(book, "wiki", logical="Mind/wiki/daily/a.md")

    assert verdict.silent is False
    assert "政策" in verdict.reason


def test_streak_plus_policy_earns_silence_and_leaves_a_trace(env):
    """五条全过 ⇒ 静默，并且**同时**留下一条可复算的 trace。"""
    book, _db = env
    _adopt(book, "wiki", B.N_ADOPT)
    _grant("wiki")

    verdict = B.evaluate(book, "wiki", logical="Mind/wiki/daily/a.md")

    assert verdict.silent is True
    traces = book.recent_traces(10)
    assert len(traces) == 1
    row = traces[0]
    assert row["silent"] is True and row["category"] == "wiki"
    rules = {r["rule"]: r for r in row["trace"]["rules"]}
    assert rules["never-auto"]["ok"] and rules["ramp"]["ok"] and rules["policy"]["ok"]
    assert row["trace"]["evidence"], "trace 要能指回是哪几次表态让它够格"
    assert row["trace"]["preference_proxy"] is True, "偏好那条是代理判据，要在 trace 里自报"


def test_one_rejection_drops_the_category_back_immediately(env):
    """快降级：攒够之后被驳一次，立刻回到必问（裁定 156／199）。"""
    book, _db = env
    _adopt(book, "wiki", B.N_ADOPT)
    _grant("wiki")
    assert B.evaluate(book, "wiki", logical="Mind/wiki/daily/a.md").silent is True

    book.record("Mind/wiki/daily/a.md", "wiki", "rejected")

    after = B.evaluate(book, "wiki", logical="Mind/wiki/daily/b.md")
    assert after.silent is False
    assert "0/5" in after.reason


def test_three_rejections_in_the_window_trip_the_breaker(env):
    """熔断：7 天内 3 次驳回 ⇒ 连**已授权**的那一类也交回人。"""
    book, _db = env
    _adopt(book, "wiki", B.N_ADOPT)
    _grant("wiki")
    for _ in range(B.K_REJECT):
        book.record("Mind/notes/x.md", "notes", "rejected")

    verdict = B.evaluate(book, "wiki", logical="Mind/wiki/daily/a.md")

    assert verdict.silent is False
    assert "全部交回人" in verdict.reason
    assert verdict.trace["breaker"]["count"] == 3


def test_rejections_older_than_the_window_do_not_trip_it(env):
    """窗口是 7 天：更早的驳回不参与熔断，否则一次旧错会永远封住自动档。"""
    book, _db = env
    old = time.time() - (B.WINDOW_DAYS + 2) * DAY
    for _ in range(B.K_REJECT):
        book.record("Mind/notes/x.md", "notes", "rejected", ts=old)
    _adopt(book, "wiki", B.N_ADOPT)
    _grant("wiki")

    assert B.evaluate(book, "wiki", logical="Mind/wiki/daily/a.md").silent is True


def test_the_reviewer_can_only_tighten(env):
    """否决器只许往「必问」改；它不许把必问升级成静默，也不许在必问时被叫起来。"""
    book, _db = env
    calls: list[str] = []

    def always_allow(payload):
        calls.append(payload["trace"]["decision"] if "decision" in payload["trace"] else "?")
        return {"veto": False, "reason": "我看着没问题"}

    # 规则判必问：reviewer 压根不该被调用（哪怕它一心想放行）。
    short = B.evaluate(book, "wiki", logical="Mind/wiki/daily/a.md", reviewer=always_allow)
    assert short.silent is False
    assert calls == [], "规则判必问时不许问否决器——它没有放行权"

    # 规则判静默：否决器可以打回。
    _adopt(book, "wiki", B.N_ADOPT)
    _grant("wiki")
    vetoed = B.evaluate(book, "wiki", logical="Mind/wiki/daily/b.md",
                        reviewer=lambda _p: {"veto": True, "reason": "这条看着像在猜"})
    assert vetoed.silent is False
    assert "猜" in vetoed.reason


def test_a_broken_reviewer_fails_closed(env):
    """否决器抛异常 ⇒ 必问。静默不能建在一个没有回话的东西上。"""
    book, _db = env
    _adopt(book, "wiki", B.N_ADOPT)
    _grant("wiki")

    def boom(_payload):
        raise TimeoutError("model did not answer in time")

    verdict = B.evaluate(book, "wiki", logical="Mind/wiki/daily/a.md", reviewer=boom)
    assert verdict.silent is False
    assert "fail-closed" in verdict.reason


def test_a_silence_that_cannot_be_recorded_is_not_a_silence(env, monkeypatch):
    """留不下痕的静默不算静默：trace 是判据的一部分，不是日志。"""
    book, _db = env
    _adopt(book, "wiki", B.N_ADOPT)
    _grant("wiki")

    def refuse(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(ConsentLedger, "record_trace", refuse)

    verdict = B.evaluate(book, "wiki", logical="Mind/wiki/daily/a.md")
    assert verdict.silent is False
    assert "写不进流水" in verdict.reason


def test_policy_candidates_grow_from_both_directions(env):
    """两个方向都只产候选：反复驳回 ⇒ 先问；一路点头 ⇒ 可以不问。"""
    book, _db = env
    for _ in range(2):
        book.record("Mind/notes/x.md", "notes", "rejected")
    _adopt(book, "wiki", B.N_ADOPT)

    got = {c.key: c for c in B.policy_candidates(book)}

    assert got["pause:notes"].kind == "pause"
    assert "一律先问" in got["pause:notes"].text
    assert got["promote:wiki"].kind == "promote"
    assert len(got["promote:wiki"].evidence) == B.N_ADOPT


def test_drafting_a_policy_does_not_touch_the_canonical_file(env):
    """候选永不自动生效：起草之后正典一个字都不许变。"""
    book, _db = env
    _adopt(book, "wiki", B.N_ADOPT)

    result = B.stage_policies(book, writer=MindWriter(require_approval=True, commit=False))

    assert result is not None and result.pending_id
    assert not _canon().exists(), "点头之前正典不该被创建"


def test_approving_a_policy_writes_it_under_the_boundary_section(env):
    """点头之后才落字，且落在 `## 边界政策` 那一节里，带证据。"""
    book, _db = env
    _adopt(book, "wiki", B.N_ADOPT)
    result = B.stage_policies(book, writer=MindWriter(require_approval=True, commit=False))

    assert B.approve(result.pending_id).startswith("Approved")

    text = _canon().read_text(encoding="utf-8")
    assert B.POLICY_SECTION in text
    assert "<!-- pol:promote:wiki category:wiki state:proposed" in text
    assert B.policies_for(text, "wiki"), "写下来的条目要能被自己解析回来"


def test_new_preferences_land_above_the_boundary_section(env):
    """后写的偏好条目不能跑到政策节下面——那会读成政策的子项。"""
    book, _db = env
    _grant("wiki")
    book.record("Mind/wiki/daily/a.md", "wiki", "adopted", comment="别写全称。")

    out = P.render(_canon().read_text(encoding="utf-8"), P.candidates(book))

    assert out.index("- 别写全称。") < out.index(B.POLICY_SECTION)


def test_deleting_the_event_db_resets_learning_to_asking(env):
    """删库＝重置学习：判据回到「一律问」，正典里的字一个不动。"""
    book, db = env
    _adopt(book, "wiki", B.N_ADOPT)
    _grant("wiki")
    assert B.evaluate(book, "wiki", logical="Mind/wiki/daily/a.md").silent is True
    before = _canon().read_bytes()

    # 沙箱把 `unlink` 接管成「移到回收站」，对 sqlite 常报 aborted；用 rename 挪开等价于重置。
    os.replace(db, db.with_name("consent.moved.db"))
    rebuilt = ConsentLedger(db)

    verdict = B.evaluate(rebuilt, "wiki", logical="Mind/wiki/daily/a.md")
    assert verdict.silent is False
    assert _canon().read_bytes() == before
