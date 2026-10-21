"""PATCH /api/agents/:id —— 「三个点 → 编辑这个 Agent」（用户 10-21）。

用户原话：「还有 Agent 抽屉的更多有没有复用底座之前会话的下拉框呢？可以编辑
更多信息，比如修改添加工作目录。重命名等等。」

这一格**只改人和这个 Agent 的关系**，不动它的身份：
  * 显示名（左栏那行字）——`id` / profile 家不动，改了会把会话历史孤儿化；
  * 图标与颜色——创建时选过、以前左栏从不渲染，现在两边一致；
  * 工作目录清单——主文件夹在前（驱动右栏挂载与 `terminal.cwd`）。

不复用 `POST /api/agents` 的原因写在端点上方那段注释里：那个是 upsert，按
body 全量重建，没带上的 `role`/`model`/`provider` 会被清成默认。下面第 5 条
测试就是钉这一点——只改图标，别的一格都不许动。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import plobi.console.router as console_router
from plobi.agents.registry import AgentEntry, AgentRegistry
from plobi.delegation.tracker import SubagentTracker, set_tracker


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(console_router.router, prefix="/api")
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Fresh registry per test; nothing leaks into the real home."""
    monkeypatch.delenv("PLOBI_PROJECTS_CONFIG", raising=False)
    monkeypatch.setattr("plobi.agents.registry.default_path", lambda: tmp_path / "projects.yaml")
    (tmp_path / "mind").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MIND_ROOT", str(tmp_path / "mind"))
    import plobi_cli.profiles as _profiles

    monkeypatch.setattr(_profiles, "profile_exists", lambda name: True, raising=False)
    registry = AgentRegistry(path=tmp_path / "projects.yaml")
    console_router.set_registry(registry)
    set_tracker(SubagentTracker())
    yield registry
    console_router.set_registry(None)
    set_tracker(None)


def _stored(registry: AgentRegistry, name: str = "aura") -> AgentEntry:
    """从**磁盘**读回来。

    `get_registry()` 会按文件签名自动重读（那是给长跑的服务端用的），所以本
    用例里 `_seed()` 一 `save()`，真正被 PATCH 的就是重读出来的那个对象——
    拿 fixture 手里那个旧对象断言会看见"改了但没动"的假象。
    """
    return AgentRegistry.load(registry.path).get(name)


def _seed(registry: AgentRegistry, **overrides) -> AgentEntry:
    # 目录必须**真的存在**：名册加载时会自愈「盘上找不到的 project_path」
    # （`heal_stale_project_paths`，那是给换了机器的用户用的）——写个假路径
    # 进去，读回来就是被 rebase 过的另一条，断言会莫名其妙地红。
    folder = registry.path.parent / "work" / "aura"
    folder.mkdir(parents=True, exist_ok=True)
    raw = {
        "role": "l2_project",
        "profile": "l2-aura",
        "display_name": "Aura",
        "category": "projects",
        "project_path": str(folder),
        "project_paths": [str(folder)],
        "icon": "robot",
        "color": "hsl(210 68% 58%)",
        "model": "deepseek/deepseek-chat",
        **overrides,
    }
    entry = AgentEntry.from_dict("aura", raw)
    registry.upsert(entry)
    registry.save()
    return entry


def test_rename_changes_the_rail_label_and_nothing_else(client, _isolated_state):
    """重命名 = 显示名。`id` 与 profile 家不动（动了历史会话就找不到主人）。"""
    _seed(_isolated_state)

    response = client.patch("/api/agents/aura", json={"displayName": "欧拉"})

    assert response.status_code == 200
    row = response.json()["data"]
    assert row["name"] == "欧拉"
    assert row["id"] == "aura"
    assert row["profile"] == "l2-aura"
    assert _stored(_isolated_state).display_name == "欧拉"
    # 落盘了（不是只在内存里漂着）
    reloaded = AgentRegistry.load(_isolated_state.path)
    assert reloaded.get("aura").display_name == "欧拉"


def test_workspace_folders_are_replaced_with_the_first_one_primary(client, _isolated_state):
    """工作目录整单替换；清单首个 = 主文件夹（右栏挂载看它）。"""
    _seed(_isolated_state)
    base = _isolated_state.path.parent
    first = str(base / "work" / "aura-v2")
    second = str(base / "work" / "shared")

    response = client.patch("/api/agents/aura", json={"projectPaths": [first, second]})

    assert response.status_code == 200
    row = response.json()["data"]
    assert row["projectPath"] == first
    assert row["projectPaths"] == [first, second]
    # 新绑的目录真的被建出来了（右栏点开就有东西看）
    assert (base / "work" / "shared").is_dir()


def test_empty_folder_list_is_refused_with_a_sentence(client, _isolated_state):
    """清空走不通：`upsert` 见「没有绑定」会按品类把默认目录种回来。

    与其让人以为删掉了，不如说清楚要换就填新的那个。
    """
    _seed(_isolated_state)

    response = client.patch("/api/agents/aura", json={"projectPaths": []})

    assert response.status_code == 400
    assert "至少要留一个工作目录" in response.json()["error"]
    # 没有副作用：还是原来那一个
    assert _stored(_isolated_state).project_path == str(_isolated_state.path.parent / "work" / "aura")


def test_empty_display_name_is_refused(client, _isolated_state):
    """左栏那一行必须有字。"""
    _seed(_isolated_state)

    response = client.patch("/api/agents/aura", json={"displayName": "   "})

    assert response.status_code == 400
    assert "显示名不能为空" in response.json()["error"]


def test_unknown_keys_are_refused_instead_of_silently_dropped(client, _isolated_state):
    """`role` / `model` 这些不在这张表单上——不许从一个窄口顺手改掉。

    有人拿它当 PUT 用时，宁可 400 说清楚能改什么，也不要"改了但没生效"。
    """
    _seed(_isolated_state)

    response = client.patch("/api/agents/aura", json={"role": "l2_agenda"})

    assert response.status_code == 400
    assert "这一格不改" in response.json()["error"]
    assert _stored(_isolated_state).role == "l2_project"


def test_icon_only_edit_leaves_model_and_role_intact(client, _isolated_state):
    """只改图标：模型覆盖、role、工作目录一格都不许动（upsert 的坑）。"""
    _seed(_isolated_state)

    response = client.patch("/api/agents/aura", json={"icon": "files", "color": ""})

    assert response.status_code == 200
    stored = _stored(_isolated_state)
    assert stored.icon == "files"
    assert stored.color == ""
    assert stored.model == "deepseek/deepseek-chat"
    assert stored.role == "l2_project"
    assert stored.project_path == str(_isolated_state.path.parent / "work" / "aura")


def test_editing_an_unregistered_agent_is_404(client):
    response = client.patch("/api/agents/nobody", json={"displayName": "Nobody"})

    assert response.status_code == 404


# ─── Agent 行上的「在工作」光条（用户 10-21）───────────────────────────────
#
# 「之前底座如果某一个会话在工作。他的抽屉还会有一个光条围绕旋转。很好看。」
# 左栏要转那条光条，就得知道**别的家**里谁在干活——桌面那个
# `$workingSessionIds` 只装当前 gateway 的会话，L1 视图下看不见 L2。所以判据
# 放在后端、按总账算，随行透出 `working`。


def _live_session(tmp_path, monkeypatch, *, agent_id: str, close_it: bool = False):
    """在 tmp 的总账里造一个该 Agent 的会话（默认未结束）。"""
    from plobi_state import SessionDB

    db_path = tmp_path / "state.db"
    monkeypatch.setattr(console_router, "default_db_path", lambda: db_path)
    db = SessionDB(db_path=db_path)
    db.create_session("s-live", source="cli", agent_id=agent_id)
    db.append_message("s-live", role="user", content="正在干的那一轮")
    if close_it:
        with db._lock:
            db._conn.execute("UPDATE sessions SET ended_at = started_at + 60 WHERE id = 's-live'")
            db._conn.commit()
    db.close()
    return db_path


def test_row_marks_the_agent_that_is_working_right_now(client, _isolated_state, tmp_path, monkeypatch):
    _seed(_isolated_state)
    _live_session(tmp_path, monkeypatch, agent_id="aura")

    rows = client.get("/api/agents").json()["data"]
    row = next(item for item in rows if item["id"] == "aura")

    assert row["working"] is True


def test_a_finished_session_is_not_working(client, _isolated_state, tmp_path, monkeypatch):
    """结束了的不算：光条要能自己灭，不然它只是个装饰。"""
    _seed(_isolated_state)
    _live_session(tmp_path, monkeypatch, agent_id="aura", close_it=True)

    rows = client.get("/api/agents").json()["data"]
    row = next(item for item in rows if item["id"] == "aura")

    assert row["working"] is False
