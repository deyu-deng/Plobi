"""静默的接线（`WP-MIND-SILENCE-WIRE`）。

P2 把判据写齐了，但 `boundary.stage_policies()` 在生产里**零调用点**——政策正典
那一节恒空，`evaluate()` 每次都退到「必问」，静默一次都不会发生。这里钉的是接上之后：

1. 归纳真的会被一次真实写入触发，且真的落一张纸（正典此刻一个字节不变）；
2. 触发口里那次写入**不会**再触发自己（不递归、不多产第二张纸）；
3. 归纳失败不许拖死写入；
4. 问过一次的类别在窗口内不再问（否则驳回＝无限骚扰，就是那 22 张的来路）；
5. 静默之后 `WriteResult.silenced` 能说清「凭哪条政策、哪一类」；
6. 政策纸自己的批准史不算业务类别的表态（不自我强化）；
7. `require_approval=False` 不叫静默——那是我听你的，不是我替你决定。
"""

from __future__ import annotations

import pytest

from plobi.mind import boundary as B
from plobi.mind import preferences as P
from plobi.mind.consent_events import ConsentLedger
from plobi.mind.paths import real_target, resolve_roots
from plobi.mind.writer import MindWriter


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
    return ConsentLedger()


def _canon():
    return real_target(P.PREFERENCES_LOGICAL, resolve_roots())


def _adopt(book, category: str, n: int = B.N_ADOPT) -> None:
    for _ in range(n):
        book.record(f"Mind/wiki/{category}/a.md", category, "adopted")


def _grant(category: str) -> None:
    """模拟「人点头过一条政策」之后正典该有的样子。"""
    path = _canon()
    path.parent.mkdir(parents=True, exist_ok=True)
    base = path.read_text(encoding="utf-8") if path.is_file() else P._HEADER
    block = (
        f"\n- 类别 `{category}` 的写入：可以不问。\n"
        f"  <!-- pol:promote:{category} category:{category} state:active evidence:cev_seed -->\n"
    )
    if B.POLICY_SECTION not in base:
        base = base.rstrip("\n") + "\n\n" + B.POLICY_SECTION + "\n" + block
    else:
        base = base.rstrip("\n") + "\n" + block
    path.write_text(base, encoding="utf-8")


def _pending() -> int:
    from tools import write_approval as wa

    return wa.pending_count("memory")


def test_a_real_write_is_what_triggers_the_policy_paper(env):
    """一次真实写入顺手归纳：多出一张政策纸，且正典此刻不存在。

    同时钉「不递归」：内部那次 `write_one` 落纸时不许再触发归纳，否则这里会是 2 张。
    """
    book = env
    _adopt(book, "wiki", B.N_ADOPT)
    assert _pending() == 0

    MindWriter(require_approval=False, commit=False).write_one(
        "Mind/wiki/memory/note.md", "x\n", mode="append"
    )

    assert _pending() == 1, "归纳该被这次写入触发（且只触发一张）"
    assert not _canon().exists(), "点头之前正典一个字节都不该有"


def test_a_broken_staging_never_breaks_the_write(env, monkeypatch):
    """归纳是顺手做的，不是写入的必要条件：它炸了，写入照样落盘。"""
    book = env
    _adopt(book, "wiki", B.N_ADOPT)

    def boom(*args, **kwargs):
        raise RuntimeError("staging exploded")

    monkeypatch.setattr(B, "stage_policies", boom)

    result = MindWriter(require_approval=False, commit=False).write_one(
        "Mind/wiki/memory/note.md", "x\n", mode="append"
    )

    assert result.ok is True
    assert result.written == ["Mind/wiki/memory/note.md"]


def test_the_same_category_is_not_asked_twice_in_the_window(env):
    """问过一次就记一笔：窗口内不再拿同一句话反复问人。"""
    book = env
    _adopt(book, "wiki", B.N_ADOPT)
    writer = MindWriter(require_approval=False, commit=False)

    writer.write_one("Mind/wiki/memory/a.md", "x\n", mode="append")
    assert _pending() == 1

    writer.write_one("Mind/wiki/memory/b.md", "y\n", mode="append")
    assert _pending() == 1, "第二次写入不该再产第二张纸"


def test_a_dismissed_policy_stays_quiet_for_the_window(env):
    """驳回过的那一把，窗口内不许自动重提（纸被删了，但事实要留下）。"""
    book = env
    _adopt(book, "wiki", B.N_ADOPT)
    book.note_policy("promote:wiki", "dismissed")

    assert B.pending_policies(book) == []


def test_silence_leaves_a_readable_note(env):
    """判据全过、自动档开着 ⇒ 写完之后能说清「凭什么没问你」。"""
    book = env
    _adopt(book, "wiki", B.N_ADOPT)
    _grant("wiki")

    result = MindWriter(require_approval=None, auto_approve=True, commit=False).write_one(
        "Mind/wiki/memory/note.md", "x\n", mode="append"
    )

    assert result.ok is True
    assert len(result.silenced) == 1
    note = result.silenced[0]
    assert note["path"] == "Mind/wiki/memory/note.md"
    assert note["category"] == "wiki"
    assert note["policy"] == "promote:wiki", "要能对回是哪一条政策放行的"
    assert "Mind/wiki/memory/note.md" in note["text"] and "wiki" in note["text"]


def test_being_told_not_to_ask_is_not_silence(env):
    """`require_approval=False` 是你明说别问——那不是系统替你决定的，不记静默。"""
    book = env
    _adopt(book, "wiki", B.N_ADOPT)
    _grant("wiki")

    result = MindWriter(require_approval=False, commit=False).write_one(
        "Mind/wiki/memory/note.md", "x\n", mode="append"
    )

    assert result.ok is True
    assert result.silenced == []


def test_a_policy_paper_does_not_count_as_a_business_vote(env):
    """政策纸自己的采纳不许算进 wiki 类：否则批准一条政策就是自己给自己发资格。"""
    book = env
    book.record(P.PREFERENCES_LOGICAL, "wiki", "adopted")
    book.record(P.PREFERENCES_LOGICAL, "wiki", "adopted")
    book.record("Mind/wiki/daily/a.md", "wiki", "adopted")

    assert B.stats(book)["wiki"].adopted == 1
    assert B.stats(book)["wiki"].streak == 1
    assert [c for c in B.policy_candidates(book) if c.kind == "promote"] == []


def test_a_dismissed_policy_still_counts_towards_the_breaker(env):
    """驳回政策纸**要**算熔断——那是「别再自动」的信号，不能被过滤掉。"""
    book = env
    for _ in range(B.K_REJECT):
        book.record(P.PREFERENCES_LOGICAL, "wiki", "rejected")

    assert B.breaker(book)["tripped"] is True
