"""新建项目分身：脑仓**没有**那把门禁时，也不该把这条路堵死（WP-MIND-MINIMAL-BRAIN-CREATE）。

2026-11-17 用户现场：桌面「New agent」弹窗红字「D:\\Data\\AppData\\Plobi\\mind 带着
AGENTS.md，却没有脑仓自己的 Mind/scripts/verifier.py：Dossiers/projects/INDEX.md 只由那个
脚本的 --fix-projects 生成，不另写一套。先把 Mind 装齐（或把 MIND_ROOT 指回真的脑仓）再建
项目分身。」⇒ 装机版**一个 projects 品类的 Agent 都建不出来**。

根因不在「脑不完整」，在**两侧口径不一致**：

* 写入侧同一把 verifier 缺了是放行的（``plobi/mind/writer.py`` → ``"verifier not present"``）；
* 创建侧只按「有没有 verifier」判 ⇒ 缺了就判死。

而**装机版自动建的那份最小脑**（``plobi/mind/paths.py::ensure_root``：一份 AGENTS.md ＋ 目录
骨架，没有 .git、没有 verifier、**§1 一个项目都不声明**）恰好落进「有 AGENTS.md、没 verifier」
那一档 ⇒ 产品自己建的脑，产品自己不认。

这里钉三条：

* 最小脑（产品自己那份）⇒ 建得出来，且**只建档案与记忆文件**：脑里一个字节都不动，
  也不另造一份 INDEX 生成器；
* **声明着项目清单**却缺 verifier ⇒ 仍判失败（半装的真脑仓：§1 会与盘上漂，而 INDEX.md
  又只能由那个脚本生成）；
* 裸目录（连 AGENTS.md 都没有）那一档的老行为一个字不动。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plobi.agents.registry import AgentRegistry
import plobi.console.router as console_router
from plobi.delegation.tracker import SubagentTracker, set_tracker
from plobi.mind import paths
from plobi.mind.paths import MINIMAL_AGENTS_MD
from plobi.mind.project_declarations import (
    AGENTS_RELATIVE,
    INDEX_RELATIVE,
    PROJECTS_RELATIVE,
    VERIFIER_RELATIVE,
)


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(console_router.router, prefix="/api")
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def isolated_registry(tmp_path, monkeypatch):
    """隔离名册 + 不碰真 profile 的 spawn（脑根与人读面由各用例自己指）。"""
    monkeypatch.delenv("PLOBI_PROJECTS_CONFIG", raising=False)
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


def declaring_agents_md(declared: list[str]) -> str:
    """形状抄自脑仓 ``AGENTS.md`` §1 真实那行（树形标记 + 计数 + 括注 + 全角冒号 + 名单）。"""
    return (
        "# Mind\n"
        "\n"
        "## 1. 结构\n"
        "\n"
        "```\n"
        "<MIND_ROOT>\n"
        "├── Mind/                   ← AI 消化区\n"
        "│   ├── skills/\n"
        "│   └── scripts/\n"
        "└── Surface/                  ← 人+AI 协作区\n"
        f"    └── projects/           ← {len(declared)} 个项目（推进中的项目）："
        + " / ".join(declared)
        + "\n"
        "        └── INDEX.md        ← 项目导航入口\n"
        "```\n"
    )


@pytest.fixture()
def minimal_brain(tmp_path, monkeypatch) -> Path:
    """产品自己建的那份**最小脑**（``paths.ensure_root`` 的产物）：AGENTS.md ＋ 目录骨架。

    没有 .git、没有 verifier、**§1 一个项目都不声明**——这正是装机版首次启动后
    ``D:\\Data\\AppData\\Plobi\\mind`` 的样子（2026-11-17 用户现场）。

    人读面钉到自己的 tmp 目录：这一档不许往用户的真数据家写一个字。
    """
    human = tmp_path / "human"
    human.mkdir()
    monkeypatch.setenv("HUMAN_ROOT", str(human))

    mind = tmp_path / "mind"
    mind.mkdir()
    (mind / AGENTS_RELATIVE).write_text(MINIMAL_AGENTS_MD, encoding="utf-8")
    for rel in paths.brain_skeleton():
        (mind / rel).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MIND_ROOT", str(mind))
    return mind


def create(client, payload: dict):
    return client.post("/api/agents", json=payload)


def live_registry() -> AgentRegistry:
    return console_router.get_registry()


# --------------------------------------------------------------------------- #
# ① 最小脑：建得出来，且只建档案与记忆文件
# --------------------------------------------------------------------------- #


def test_the_minimal_brain_can_still_create_a_project_agent(
    client, isolated_registry, minimal_brain
):
    """最小脑 ⇒ 建得出来；脑里一个字节不动，也不另造 INDEX。"""
    before = (minimal_brain / AGENTS_RELATIVE).read_bytes()

    response = create(
        client, {"id": "aura", "name": "Aura", "category": "projects"}
    )

    assert response.status_code == 200, response.json()

    # 档案落在人读面（数据家）那一份，不在脑里。
    tree = paths.real_target(PROJECTS_RELATIVE, minimal_brain) / "Aura"
    assert (tree / "dossier.json").is_file()

    # 名册那行写得成，且记着这棵树。
    entry = live_registry().get("aura")
    assert entry is not None
    assert entry.mind_subtree.startswith(f"{PROJECTS_RELATIVE}/")

    # 脑里一个字节都没动，也没有多出 INDEX.md（那份只由 verifier 生成，不另写一套）。
    assert (minimal_brain / AGENTS_RELATIVE).read_bytes() == before
    assert not paths.real_target(INDEX_RELATIVE, minimal_brain).exists()


def test_the_minimal_brain_gets_no_verifier_invented_for_it(
    client, isolated_registry, minimal_brain
):
    """不许替它造一把门禁脚本——那是脑仓自己的东西，不是我们的。"""
    create(client, {"id": "aura", "name": "Aura", "category": "projects"})

    assert not paths.real_target(VERIFIER_RELATIVE, minimal_brain).exists()


# --------------------------------------------------------------------------- #
# ② 边界：声明着项目清单却缺 verifier ⇒ 仍判失败
# --------------------------------------------------------------------------- #


def test_a_brain_that_declares_projects_still_refuses_without_its_generator(
    client, isolated_registry, tmp_path, monkeypatch
):
    """半装的真脑仓：§1 声明着项目、verifier 却没了 ⇒ 判失败（声明会对不上盘）。"""
    human = tmp_path / "human"
    human.mkdir()
    monkeypatch.setenv("HUMAN_ROOT", str(human))

    mind = tmp_path / "half-brain"
    mind.mkdir()
    (mind / AGENTS_RELATIVE).write_text(
        declaring_agents_md(["Aura"]), encoding="utf-8"
    )
    monkeypatch.setenv("MIND_ROOT", str(mind))

    response = create(
        client, {"id": "orbit", "name": "Orbit", "category": "projects"}
    )

    assert response.status_code == 400, response.json()
    assert VERIFIER_RELATIVE in response.json()["error"]
    assert live_registry().get("orbit") is None


# --------------------------------------------------------------------------- #
# ③ 裸目录（连 AGENTS.md 都没有）：老行为一个字不动
# --------------------------------------------------------------------------- #


def test_a_bare_root_still_creates_just_the_tree(
    client, isolated_registry, tmp_path, monkeypatch
):
    human = tmp_path / "human"
    human.mkdir()
    monkeypatch.setenv("HUMAN_ROOT", str(human))

    mind = tmp_path / "bare"
    mind.mkdir()
    monkeypatch.setenv("MIND_ROOT", str(mind))

    response = create(
        client, {"id": "orbit", "name": "Orbit", "category": "projects"}
    )

    assert response.status_code == 200, response.json()
    tree = paths.real_target(PROJECTS_RELATIVE, mind) / "Orbit"
    assert (tree / "dossier.json").is_file()
    # 没有声明可同步 ≠ 自己造一份。
    assert not (mind / AGENTS_RELATIVE).exists()
    assert not paths.real_target(INDEX_RELATIVE, mind).exists()
