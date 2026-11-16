"""P1 信号层的判据（`WP-MIND-CONSENT-EVENTS`）。

规格 §3.1：每次接纳／拒绝／评论落一条**不可变事件**；对称律＝接纳与驳回进同一本账；
`category` 由路径派生、`content_sha` 是点头那一刻那一版；评论原话随事件走。

与 `test_mind_pending_content.py` 分档：那一档钉 P0（正文可见＋评论入账＋记账格），
这一档只钉流水本身，互不当前提。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plobi.mind.consent_events import ConsentLedger
from plobi.mind.pending_api import register_memory_pending_api
from plobi.mind.writer import MindWriter


@pytest.fixture()
def env(tmp_path, monkeypatch):
    mind = tmp_path / "Mind"
    surface = tmp_path / "Surface"
    (mind / "wiki" / "daily").mkdir(parents=True)
    (surface / "dossiers" / "projects").mkdir(parents=True)
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("HUMAN_ROOT", str(surface))
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PLOBI_CONSENT_DB", str(tmp_path / "state" / "consent.db"))
    app = FastAPI()
    register_memory_pending_api(app)
    return TestClient(app), mind, Path(os.environ["PLOBI_CONSENT_DB"])


def _stage(content: str = "# 日程\n正文第一行。\n", path: str = "Mind/wiki/daily/2026-11-16.md") -> str:
    return MindWriter(require_approval=True, commit=False).write_one(path, content).pending_id


def test_events_live_in_their_own_db_honouring_the_env_var(env):
    """拍定「独立 `consent.db`」：不许落进票库，也不许落进会话库。"""
    http, _mind, db = env
    assert not db.exists()

    pid = _stage()
    assert http.post(f"/api/memory/pending/{pid}/approve").json()["ok"] is True

    assert db.is_file(), "事件没落进 PLOBI_CONSENT_DB 指的是那一份"
    assert ConsentLedger().path == db


def test_adoption_and_rejection_share_one_table(env):
    """对称律：采纳与驳回进同一本账——P0 修掉的是「记错格子」，这条钉住流水层。"""
    http, _mind, db = env
    pid_ok = _stage()
    assert http.post(f"/api/memory/pending/{pid_ok}/approve").json()["ok"] is True

    pid_no = _stage(content="# 日程\n第二版，要被驳回的那张。\n")
    assert http.post(f"/api/memory/pending/{pid_no}/reject").json()["ok"] is True

    rows = ConsentLedger(db).recent(50)
    actions = sorted(r.action for r in rows)
    assert actions == ["adopted", "rejected"], rows
    assert {r.category for r in rows} == {"wiki"}, rows


def test_event_carries_the_version_that_was_approved(env):
    """`content_sha` 是点头那一刻那一版；`category` 由路径派生，不由模型猜。"""
    http, _mind, db = env
    content = "# 日程\n这一刻的正文。\n"
    pid = _stage(content)
    assert http.post(f"/api/memory/pending/{pid}/approve").json()["ok"] is True

    (event,) = ConsentLedger(db).recent(10)
    assert event.target == "Mind/wiki/daily/2026-11-16.md"
    assert event.category == "wiki"
    assert event.content_sha == hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert event.context_ref == f"pending:{pid}", "事件要能指回它评判的那张纸"


def test_the_comment_travels_with_the_event_even_though_the_paper_is_gone(env):
    """评论是学习原料：纸在点头那一刻被删了，原话必须已经进了事件。"""
    http, _mind, db = env
    pid = _stage()
    http.post(f"/api/memory/pending/{pid}/comment", json={"comment": "这类摘要只列要我拍板的。"})
    assert http.post(f"/api/memory/pending/{pid}/approve").json()["ok"] is True

    (event,) = ConsentLedger(db).recent(10)
    assert event.action == "adopted"
    assert event.comment == "这类摘要只列要我拍板的。"


def test_a_broken_ledger_never_breaks_the_approval(env, monkeypatch):
    """记不上账不该让点头本身失败（沿用 `_remember_l0_decision` 的口径）。"""
    http, mind, _db = env
    pid = _stage()

    def boom(self, plan, action, **kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(ConsentLedger, "record_plan", boom)
    out = http.post(f"/api/memory/pending/{pid}/approve").json()

    assert out["ok"] is True, out
    assert (mind / "wiki" / "daily" / "2026-11-16.md").is_file(), "账本坏了，写入本身还得落"


def test_read_api_reports_the_book_without_touching_it(env):
    """读口只读：GET 一次不许放行任何纸，也不许写第二条事件。"""
    http, _mind, db = env
    pid = _stage()
    assert http.post(f"/api/memory/pending/{pid}/approve").json()["ok"] is True
    pid_open = _stage(content="# 日程\n这张还等着。\n")

    got = http.get("/api/memory/consent").json()

    assert got["count"] == 1 and len(got["events"]) == 1, got
    assert got["categories"]["wiki"]["adopted"] == 1, got["categories"]
    assert got["events"][0]["action"] == "adopted"
    assert got["db"] == str(db)
    # 纸还在待批层：读流水不等于放行
    assert http.get("/api/memory/pending").json()["count"] == 1
    assert ConsentLedger(db).recent(50)[0].context_ref == f"pending:{pid}"
    assert pid_open  # 仍未被处理


def test_recent_is_newest_first_and_capped(env):
    """读口按时间倒序、条数受上限约束（`limit` 上限 200，防一次拉全库）。"""
    http, _mind, db = env
    book = ConsentLedger(db)
    for i in range(5):
        book.record(f"Mind/wiki/page-{i}.md", "wiki", "adopted", ts=1_700_000_000 + i)

    events = book.recent(3)
    assert [e.target for e in events] == [
        "Mind/wiki/page-4.md", "Mind/wiki/page-3.md", "Mind/wiki/page-2.md",
    ]
    assert len(http.get("/api/memory/consent?limit=2").json()["events"]) == 2


def test_unknown_action_is_refused_not_silently_stored(env):
    """动作集合写死三种；拼错的动作要当场报错，不许落成一条读不懂的流水。"""
    _http, _mind, db = env
    with pytest.raises(ValueError):
        ConsentLedger(db).record("Mind/wiki/x.md", "wiki", "approved")
