"""裁定 42 §42.2 第 6 项：L2 项目记录能真正写入并留住「模型覆盖」。

覆盖只解决后端收字这一段（消费点 ``session.create`` 由桌面那一棒接）。锁的是六条不变量：

* 写了就能读回来，且落盘（``projects.yaml`` 的 ``provider:`` / ``model:`` 两行）。
* 只给 ``model`` 时 ``provider`` 沿用记录里那个，不静默丢。
* 只给 ``provider`` 不给 ``model`` → 400，且原记录逐字不动（光有 provider 定位不到模型）。
* id / model 里带空白 → 400，原记录不动。
* **编辑其它字段时不带这两个键，已有覆盖必须活着**——``upsert`` 是整条替换，
  这一条是本功能最容易被后来的改动踩坏的点。
* ``model`` 给空串 = 清掉覆盖，回到该角色的默认路由。

纪律：不碰 ``plobi/routing``、不新增路由键；测试全在 tmp 里，不写真实 ``~/.plobi``。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plobi.agents.registry import AgentRegistry
from plobi.delegation.tracker import SubagentTracker, set_tracker
import plobi.console.router as console_router


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(console_router.router, prefix="/api")
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """全新注册表 + 全新 tracker；默认项目根拐进 tmp，绝不碰真实家目录。"""
    monkeypatch.setattr(
        "plobi.agents.registry.default_path", lambda: tmp_path / "projects.yaml"
    )
    monkeypatch.setattr(
        "plobi.agents.registry.DEFAULT_PROJECT_PATHS",
        {
            "projects": str(tmp_path / "projects"),
            "events": str(tmp_path / "cloud" / "events"),
            "research": str(tmp_path / "cloud" / "research"),
            "butler": "",
        },
    )
    registry = AgentRegistry(path=tmp_path / "projects.yaml")
    console_router.set_registry(registry)
    set_tracker(SubagentTracker())

    def fake_spawn(name, *, clone_from=None, write_config=True):
        return {"name": name, "clone_from": clone_from}

    monkeypatch.setattr(registry, "spawn", fake_spawn)
    yield registry
    console_router.set_registry(None)
    set_tracker(None)


def _create(client, agent_id, **extra):
    body = {"id": agent_id, "category": "projects", "role": "l2_project"}
    body.update(extra)
    return client.post("/api/agents", json=body)


# --------------------------------------------------------------------------- #
# 写入 → 读回 → 落盘
# --------------------------------------------------------------------------- #


def test_model_override_round_trips_through_api_and_disk(client, _isolated_state):
    resp = _create(client, "aura", provider="moonshot", model="kimi-k2")
    assert resp.status_code == 200, resp.json()
    row = resp.json()["data"]
    assert row["model"] == "moonshot/kimi-k2", row

    # 真落盘：重新加载注册表，字段还在（不是只在内存里活着）
    reloaded = AgentRegistry.load(_isolated_state.path)
    entry = reloaded.get("aura")
    assert (entry.provider, entry.model) == ("moonshot", "kimi-k2")


# --------------------------------------------------------------------------- #
# provider 沿用 / 校验 fail-closed
# --------------------------------------------------------------------------- #


def test_model_only_keeps_the_existing_provider(client, _isolated_state):
    assert _create(client, "prism", provider="aigw", model="workbuddy/deepseek-chat").status_code == 200

    resp = _create(client, "prism", model="workbuddy/deepseek-reasoner")
    assert resp.status_code == 200, resp.json()
    entry = AgentRegistry.load(_isolated_state.path).get("prism")
    assert (entry.provider, entry.model) == ("aigw", "workbuddy/deepseek-reasoner")


def test_provider_without_model_is_rejected_and_row_untouched(client, _isolated_state):
    assert _create(client, "kit", provider="aigw", model="workbuddy/deepseek-chat").status_code == 200

    resp = _create(client, "kit", provider="moonshot")
    assert resp.status_code == 400, resp.json()
    entry = AgentRegistry.load(_isolated_state.path).get("kit")
    assert (entry.provider, entry.model) == ("aigw", "workbuddy/deepseek-chat")


def test_whitespace_in_model_is_rejected(client, _isolated_state):
    resp = _create(client, "nymo", model="kimi k2")
    assert resp.status_code == 400, resp.json()
    assert AgentRegistry.load(_isolated_state.path).get("nymo") is None


# --------------------------------------------------------------------------- #
# 最容易被后来改动踩坏的一条：编辑别的字段不能把覆盖吞掉
# --------------------------------------------------------------------------- #


def test_edit_without_model_keys_preserves_the_override(client, _isolated_state):
    assert _create(client, "stithy", provider="moonshot", model="kimi-k2").status_code == 200

    resp = _create(client, "stithy", description="改了标语，没提模型")
    assert resp.status_code == 200, resp.json()
    entry = AgentRegistry.load(_isolated_state.path).get("stithy")
    assert entry.description == "改了标语，没提模型"
    assert (entry.provider, entry.model) == ("moonshot", "kimi-k2")


def test_empty_model_clears_the_override_back_to_role_default(client, _isolated_state):
    assert _create(client, "resonote", provider="moonshot", model="kimi-k2").status_code == 200

    resp = _create(client, "resonote", model="")
    assert resp.status_code == 200, resp.json()
    entry = AgentRegistry.load(_isolated_state.path).get("resonote")
    assert entry.has_model_override is False, (entry.provider, entry.model)
