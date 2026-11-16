"""记忆写入 P0 的四条新判据（WP-MIND-PENDING-CONTENT）。

原档 `test_mind_pending_api.py` 归 `WP-MIND-PENDING-VISIBLE`／`WP-NOTE-RENAME-DELETE`，
本档只钉本刀新增的四件事：正文可见、评论入账、回包挂反馈、L1 采纳记账。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plobi.mind import proposal as L1
from plobi.mind.pending_api import register_memory_pending_api
from plobi.mind.writer import MindWriter


@pytest.fixture()
def client(tmp_path, monkeypatch):
    mind = tmp_path / "Mind"
    surface = tmp_path / "Surface"
    (mind / "wiki" / "daily").mkdir(parents=True)
    (surface / "dossiers" / "projects").mkdir(parents=True)
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("HUMAN_ROOT", str(surface))
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path / "home"))
    app = FastAPI()
    register_memory_pending_api(app)
    return TestClient(app), mind


def _stage(content="# 日程摘要\n正文第一行，要点头的人得看得见。\n"):
    result = MindWriter(require_approval=True, commit=False).write_one(
        "Mind/wiki/daily/2026-11-16.md", content
    )
    return result.pending_id


def test_pending_item_exposes_the_actual_content_not_only_a_path(client):
    http, _mind = client
    _stage()
    item = http.get("/api/memory/pending").json()["items"][0]

    assert item["changes"], "只有文件名＝人不知道要批准什么"
    assert item["changes"][0]["path"] == "wiki/daily/2026-11-16.md"
    assert item["changes"][0]["mode"] == "overwrite"
    assert "正文第一行" in item["changes"][0]["content"]
    assert item["summary"], "摘要仍要给折叠态用"


def test_comment_is_recorded_verbatim_and_does_not_approve_anything(client):
    http, mind = client
    pid = _stage()
    out = http.post(
        f"/api/memory/pending/{pid}/comment",
        json={"comment": "这段写成三段，别揉一行。"},
    ).json()

    assert out["ok"] is True, out
    record = json.loads(
        (Path(os.environ["PLOBI_HOME"]) / "pending" / "memory" / f"{pid}.json").read_text(encoding="utf-8")
    )
    assert record["payload"]["feedback"][0]["comment"] == "这段写成三段，别揉一行。"
    assert not (mind / "wiki" / "daily" / "2026-11-16.md").exists(), "评论不该顺手放行"
    assert http.get("/api/memory/pending").json()["count"] == 1, "评论不该让纸消失"


def test_unpopular_comments_are_carried_in_the_read_payload(client):
    http, _mind = client
    pid = _stage()
    http.post(f"/api/memory/pending/{pid}/comment", json={"comment": "先别写月份。"})

    got = http.get("/api/memory/pending").json()
    assert got["pending_feedback"], "评论不进回包＝指挥那一轮读不到"
    assert got["pending_feedback"][0]["pending_id"] == pid
    assert got["pending_feedback"][0]["comment"] == "先别写月份。"
    # 归位：kind 在记录顶层、路径取 changes 第一格——这两格曾经都读空。
    assert got["pending_feedback"][0]["kind"] == "mind_plan"
    assert got["pending_feedback"][0]["path"] == "wiki/daily/2026-11-16.md"


def test_a_comment_survives_the_paper_it_was_written_on(client):
    """「评论＋顺手点头」是最自然的动作；纸被删掉时评论不能跟着蒸发。"""
    http, _mind = client
    pid = _stage()
    http.post(f"/api/memory/pending/{pid}/comment", json={"comment": "下次这类先写成三段。"})

    approved = http.post(f"/api/memory/pending/{pid}/approve").json()
    assert approved["ok"] is True, approved
    assert http.get("/api/memory/pending").json()["count"] == 0, "纸本来就该被批掉"

    got = http.get("/api/memory/pending").json()["pending_feedback"]
    assert [f["comment"] for f in got] == ["下次这类先写成三段。"], got


def test_approving_a_mind_plan_records_adoption_on_the_l1_ledger(client, tmp_path, monkeypatch):
    """采纳记 L0 账时，L1 的 streak 永远攒不起来——这条钉死记账格。"""
    http, _mind = client
    ledger = Path(os.environ["PLOBI_HOME"]) / "l1-trust.json"
    pid = _stage()

    out = http.post(f"/api/memory/pending/{pid}/approve").json()
    assert out["ok"] is True, out

    from plobi.mind.proposal import TrustLedger

    rows = TrustLedger(ledger).as_rows()
    assert rows, "采纳后 L1 账里一格都没有＝记错到 L0 去了"
    assert any(r["source"] == "l1" and r["adopted"] >= 1 for r in rows), rows
