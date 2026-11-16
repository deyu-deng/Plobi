"""偏好条目那一层（`WP-MIND-CONSENT-EVENTS` 的 P1 后半，规格 §3.2）。

钉四件事，全在"人点头之前不许变"这条线上：

1. 只有**有原话**的表态才长候选（点一下不等于说了怎么改）；
2. 起草只产出一张待批纸，正典 `.md` **此刻一个字不变**；
3. 点头之后才落字，且同一条事件不会写第二次（幂等靠 `<!-- ev:… -->`）；
4. 删掉 `consent.db` 重建，正典里已有的字**逐字不变**（规格 §5.2 那条不变量）。

零模型调用：默认抽取器是 `verbatim`，原话归一成一行，不替用户改写。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

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
    return ConsentLedger(), tmp_path / "state" / "consent.db"


def _target() -> Path:
    return real_target(P.PREFERENCES_LOGICAL, resolve_roots())


def test_only_comments_grow_candidates(env):
    """点了一下不说明「该怎么写」；有原话才有候选。"""
    book, _db = env
    book.record("Mind/wiki/daily/a.md", "wiki", "adopted", comment="")
    book.record("Mind/wiki/daily/b.md", "wiki", "rejected", comment="这类摘要只列要我拍板的。")

    got = P.candidates(book)

    assert [c.text for c in got] == ["这类摘要只列要我拍板的。"]
    assert got[0].category == "wiki"
    assert got[0].action == "rejected"


def test_drafting_does_not_touch_the_canonical_file(env):
    """候选只是候选：起草之后 `.md` 一个字都不许变（人还没点头）。"""
    book, _db = env
    book.record("Mind/wiki/daily/a.md", "wiki", "adopted", comment="别写全称。")

    result = P.stage(book, writer=MindWriter(require_approval=True, commit=False))

    assert result is not None and result.pending_id
    assert not _target().exists(), "点头之前正典不该被创建"


def test_approving_writes_the_rule_with_its_evidence(env):
    """点头之后才落字；条目下面带着证据 id，将来能回指到那一次表态。"""
    book, _db = env
    event = book.record("Mind/wiki/daily/a.md", "wiki", "adopted", comment="别写全称，写通称。")
    result = P.stage(book, writer=MindWriter(require_approval=True, commit=False))

    assert P.approve(result.pending_id).startswith("Approved"), "点头要走同一处批准实现"

    text = _target().read_text(encoding="utf-8")
    assert "# 记忆写入偏好" in text
    assert "- 别写全称，写通称。" in text
    assert f"<!-- ev:{event.id}" in text


def test_the_same_event_is_never_written_twice(env):
    """幂等：同一条事件起草两次，第二次没有候选可写。"""
    book, _db = env
    book.record("Mind/wiki/daily/a.md", "wiki", "adopted", comment="先写成三段。")
    first = P.stage(book, writer=MindWriter(require_approval=True, commit=False))
    P.approve(first.pending_id)

    assert P.pending(book) == [], "正典里已经有这条事件，不该再出候选"
    assert P.stage(book, writer=MindWriter(require_approval=True, commit=False)) is None


def test_deleting_the_event_db_leaves_the_canonical_text_byte_identical(env):
    """流水可派生、正典不可派生：删掉 consent.db 重建，正典逐字不变（规格 §5.2）。"""
    book, db = env
    book.record("Mind/wiki/daily/a.md", "wiki", "adopted", comment="摘要只列要拍板的。")
    result = P.stage(book, writer=MindWriter(require_approval=True, commit=False))
    P.approve(result.pending_id)
    before = _target().read_bytes()

    # 沙箱把 `unlink` 接管成「移到回收站」，对 sqlite 常报 aborted；
    # 用 rename 把库挪开，等价于「这一份没了、下次从空库开始」，语义更贴近「重置学习」。
    os.replace(db, db.with_name("consent.moved.db"))
    rebuilt = ConsentLedger(db)
    assert rebuilt.recent(50) == [], "库空了＝重置学习，这是设计不是事故"
    assert P.pending(rebuilt) == []
    assert _target().read_bytes() == before


def test_the_extractor_is_pluggable_and_defaults_to_verbatim(env):
    """默认零模型：原话照抄。接便宜模型时只换抽取器，不改「人点头才生效」。"""
    book, _db = env
    book.record("Mind/wiki/notes/x.md", "notes", "rejected", comment="  这里别写全称 \n第二行  ")

    assert P.candidates(book)[0].text == "这里别写全称 第二行"
    rewritten = P.candidates(book, extract=lambda c: f"规则：{c.strip()}")
    assert rewritten[0].text.startswith("规则：")


def test_category_filter_keeps_preferences_scoped(env):
    """按类筛：一门课攒的偏好不去替另一类内容定规矩。"""
    book, _db = env
    book.record("Mind/wiki/daily/a.md", "wiki", "adopted", comment="日记那条偏好。")
    book.record("Mind/notes/cs101/a.md", "notes", "adopted", comment="课程那条偏好。")

    got = P.candidates(book, category="notes")

    assert [c.text for c in got] == ["课程那条偏好。"]
    assert os.environ["PLOBI_CONSENT_DB"].endswith("consent.db")
