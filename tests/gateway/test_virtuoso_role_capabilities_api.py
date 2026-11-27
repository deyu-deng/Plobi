"""名手（专家）能力集的 HTTP 口（`WP-EXPERT-CAPABILITIES` 11-16）。

钉五件事：

1. `GET /api/virtuosos/roles` 把**默认能力**、**现在有的能力**、**缺哪样**、**它自己的
   黑名单**一起交出去（屏上那一格靠这几格画）；
2. `PUT` 带 `disabled_capabilities` ⇒ 写它自己的黑名单，**且不碰模型**；
3. `PUT` 带 `model` ⇒ 写模型，**且不碰黑名单**；
4. 不认得的能力项 ⇒ **报错**（不许默默吞掉）；
5. 两格都不带 ⇒ 报错（不许当成「什么都清空」）。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plobi.console.router import router as console_router


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """隔离的名手角色级设置（不碰这台机器的真档）。"""
    monkeypatch.setenv("PLOBI_VIRTUOSOS_DIR", str(tmp_path / "virtuosos"))
    app = FastAPI()
    app.include_router(console_router, prefix="/api")
    return TestClient(app)


def _roles(client) -> dict:
    body = client.get("/api/virtuosos/roles").json()
    assert body["ok"] is True
    return {row["key"]: row for row in body["data"]["roles"]}


def test_the_list_carries_capabilities_and_the_blacklist(client):
    rows = _roles(client)

    assert set(rows) == {"file", "browser", "software", "computer"}
    row = rows["software"]
    # 默认能力（人话标签）与它现在拿到的东西
    assert "跑命令" in row["capabilities"]
    assert "跑命令" in row["effective_capabilities"]
    assert row["missing_capabilities"] == []
    assert row["disabled_capabilities"] == []
    # 默认工具面与 11-15 之前逐字一致
    assert row["toolsets"] == ["terminal", "file"]


def test_writing_the_blacklist_does_not_touch_the_model(client):
    client.put("/api/virtuosos/roles/software", json={"model": "prov/model-x"})
    body = client.put(
        "/api/virtuosos/roles/software", json={"disabled_capabilities": ["run_commands"]}
    ).json()

    assert body["ok"] is True
    assert body["data"]["model"] == "prov/model-x"
    assert body["data"]["disabled_capabilities"] == ["run_commands"]

    row = _roles(client)["software"]
    assert row["model"] == "prov/model-x"
    assert "跑命令" not in row["effective_capabilities"]
    assert "跑命令" in row["missing_capabilities"]
    assert "terminal" not in row["effective_toolsets"]
    # 默认那一层不动（默认工具面与 11-15 之前逐字一致）
    assert row["toolsets"] == ["terminal", "file"]


def test_writing_the_model_does_not_touch_the_blacklist(client):
    client.put("/api/virtuosos/roles/software", json={"disabled_capabilities": ["run_commands"]})
    body = client.put("/api/virtuosos/roles/software", json={"model": "prov/model-y"}).json()

    assert body["ok"] is True
    assert body["data"]["model"] == "prov/model-y"
    assert body["data"]["disabled_capabilities"] == ["run_commands"]


def test_clearing_the_blacklist_goes_back_to_the_default(client):
    client.put("/api/virtuosos/roles/software", json={"disabled_capabilities": ["run_commands"]})
    body = client.put("/api/virtuosos/roles/software", json={"disabled_capabilities": []}).json()

    assert body["ok"] is True
    assert body["data"]["disabled_capabilities"] == []
    assert "跑命令" in _roles(client)["software"]["effective_capabilities"]


def test_an_unknown_capability_is_refused(client):
    body = client.put(
        "/api/virtuosos/roles/software", json={"disabled_capabilities": ["fly_to_the_moon"]}
    ).json()

    assert body["ok"] is False
    assert "fly_to_the_moon" in body["error"]


def test_an_empty_body_changes_nothing(client):
    body = client.put("/api/virtuosos/roles/software", json={}).json()

    assert body["ok"] is False
