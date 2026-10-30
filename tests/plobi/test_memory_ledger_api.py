"""个人记忆的读口与「忘掉」（用户 2026-10-30 拍：真删，并记一笔）。

钉两个失败形状：读到的东西跟盘上不一致（`§` 分隔被忽略、条目混成一块），
以及「忘掉」删错人/删错条（序号对上了但落到了另一份，或者删了却查不到删过什么）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plobi.mind.memory_api import register_memory_ledger_api


@pytest.fixture()
def client(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "memory" / "agents" / "l2-course-x").mkdir(parents=True)
    monkeypatch.setenv("PLOBI_HOME", str(home))
    monkeypatch.setenv("PLOBI_AGENT_ID", "l2-course-x")
    app = FastAPI()
    register_memory_ledger_api(app)
    return TestClient(app), home


def test_it_lists_the_three_ledgers_entry_by_entry(client):
    http, home = client
    (home / "memory" / "GLOBAL.md").write_text("共用纪律甲\n§\n共用纪律乙\n", encoding="utf-8")
    (home / "memory" / "USER.md").write_text("小龟，浙大\n", encoding="utf-8")
    (home / "memory" / "agents" / "l2-course-x" / "MEMORY.md").write_text("这门课周三有实验\n", encoding="utf-8")

    got = http.get("/api/memory/ledgers").json()

    assert got["agent"] == "l2-course-x"
    by_key = {s["key"]: s for s in got["scopes"]}
    assert [e["text"] for e in by_key["global"]["entries"]] == ["共用纪律甲", "共用纪律乙"]
    assert by_key["user"]["entries"][0]["text"].startswith("小龟")
    assert by_key["memory"]["entries"][0]["text"].startswith("这门课")
    assert by_key["memory"]["limit"] > 0, "上限要一起给出来，屏幕上才画得出用了多少"


def test_forget_deletes_exactly_one_entry_and_leaves_a_receipt(client):
    http, home = client
    memory = home / "memory" / "agents" / "l2-course-x" / "MEMORY.md"
    memory.write_text("第一条\n§\n第二条要忘\n§\n第三条\n", encoding="utf-8")

    out = http.post("/api/memory/forget", json={"scope": "memory", "index": 1}).json()

    assert out["ok"] is True, out
    assert out["removed"] == "第二条要忘"
    assert memory.read_text(encoding="utf-8").strip().split("\n§\n") == ["第一条", "第三条"]
    receipt = Path(home / "memory" / "deletions.jsonl").read_text(encoding="utf-8").strip().split("\n")
    assert json.loads(receipt[-1])["text"] == "第二条要忘"

    # 越界要拒，而且不能误删任何一条
    bad = http.post("/api/memory/forget", json={"scope": "memory", "index": 9}).json()
    assert bad["ok"] is False and "越界" in bad["error"]
    assert len(memory.read_text(encoding="utf-8").strip().split("\n§\n")) == 2
