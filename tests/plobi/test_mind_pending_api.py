"""待确认的读口/批口（WP-MIND-PENDING-VISIBLE）。

钉的是"进人眼"这件事的两个失败形状：过期的纸被当成能批的（点了也不落，白点一次），
以及批完列表不清零（屏幕上永远挂着一个数）。行为本身归 ``write_approval_commands``，
这里只验 HTTP 那一层没有把它抄歪。
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
    vault = tmp_path / "Vault"
    (mind / "wiki" / "daily").mkdir(parents=True)
    (vault / "dossiers" / "projects").mkdir(parents=True)
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("VAULT_ROOT", str(vault))
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path / "home"))
    app = FastAPI()
    register_memory_pending_api(app)
    return TestClient(app), mind


def _stage(rel="Loom/wiki/daily/2026-10-27.md", content="# 日程摘要\n"):
    result = MindWriter(require_approval=True, commit=False).write_one(rel, content)
    return result.pending_id, rel


def test_an_expired_paper_is_listed_as_expired_and_a_click_lands_nothing(client):
    http, mind = client
    pending_id, rel = _stage()

    got = http.get("/api/memory/pending").json()
    assert got["count"] == 1, got
    item = got["items"][0]
    assert item["id"] == pending_id
    assert item["expired"] is False, "刚出的计划不该被判过期"
    # 屏幕上不写逻辑前缀 Loom/（用户 2026-10-29「不是已经没有 Loom 这个概念了吗」）
    assert item["paths"] == [rel[len("Loom/"):] if rel.startswith("Loom/") else rel]

    # 时间往前推过 TTL（6h）：这张纸作废，屏幕上要看得见"过期"，点它也不落一个字
    store = Path(os.environ["PLOBI_HOME"]) / "pending" / "memory" / f"{pending_id}.json"
    record = json.loads(store.read_text(encoding="utf-8"))
    plan = L1.Plan.from_json(record["payload"]["plan"])
    plan.created_at -= L1.PLAN_TTL_SECONDS + 60
    record["payload"]["plan"] = plan.to_json()
    store.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

    got = http.get("/api/memory/pending").json()
    assert got["items"][0]["expired"] is True, got
    assert got["expired_count"] == 1

    after = http.post(f"/api/memory/pending/{pending_id}/approve").json()
    assert after["ok"] is False, after
    assert not (mind / "wiki" / "daily" / "2026-10-27.md").exists(), "过期那张被点落了＝审批可以绕过 TTL"


def test_approving_a_live_plan_lands_the_file_and_clears_it_from_the_list(client):
    http, mind = client
    pending_id, _rel = _stage()

    out = http.post(f"/api/memory/pending/{pending_id}/approve").json()

    assert out["ok"] is True, out
    assert (mind / "wiki" / "daily" / "2026-10-27.md").read_text(encoding="utf-8") == "# 日程摘要\n"
    assert out["count"] == 0, "批完了屏幕上还挂着数＝这个入口本身就变成噪音"


def test_prune_clears_only_expired_papers_and_keeps_a_copy(client):
    """清过期的，不动还活着的；清掉的要先抄进归档（不是直接丢）。"""
    import json

    http, _mind = client
    live_id, _rel = _stage("Loom/wiki/daily/2026-10-29.md")
    dead_id, _rel2 = _stage("Loom/wiki/daily/2026-10-28.md")

    store = Path(os.environ["PLOBI_HOME"]) / "pending" / "memory" / f"{dead_id}.json"
    record = json.loads(store.read_text(encoding="utf-8"))
    plan = L1.Plan.from_json(record["payload"]["plan"])
    plan.created_at -= L1.PLAN_TTL_SECONDS + 60
    record["payload"]["plan"] = plan.to_json()
    store.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

    out = http.post("/api/memory/pending/prune").json()

    assert out["ok"] is True
    assert out["pruned"] == [dead_id]
    assert out["count"] == 1 and out["items"][0]["id"] == live_id
    archived = Path(os.environ["PLOBI_HOME"]) / "archive" / "pending-expired" / f"{dead_id}.json"
    assert archived.is_file(), "清掉的纸要先有一份归档，别直接丢"
