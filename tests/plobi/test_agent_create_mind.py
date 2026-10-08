"""R-013 扩写（2026-10-06）—— 建 project 分身要在 Mind 立项，重名在创建那一刻就禁。

用户原话就是判据：「在代码层面上实现每创建一个 project 类型的 L2 agent，mind 里面要
自动创建对应的文件夹和相关文件。如果有出现重名的就直接禁止创建。」触发它的实况是他
在 ``projects.yaml`` 里手建了 ``nymo``（没有 ``mind_subtree``），和 Mind 播种的 ``Nymo``
成了同一个项目的两条，左栏看到两个 nymo。

钉的是四条不变量，不钉名单：

* 建成功后 ``mind_subtree`` 指向的目录与三件套**真的在盘上**（②）；
* 撞已有键 / 只差大小写的键 / 同一个项目名 / 保留 profile 名 → 当场拒，名册不落、
  Mind 不建目录（①）；
* Mind 立项建不成 → 这条创建就是失败的，registry 里不许留「有名册没目录」的孤儿（③）；
* 存量那对只读不并，但行上带得出谁没挂 Mind。

MIND_ROOT 一律指到 tmp 里的假库——真脑仓（``MIND_ROOT`` 指到的那个）是自带着 ``.git`` 的第四个仓，
测试往里写一个目录就是污染用户的脑。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plobi.agents.registry import AgentEntry, AgentRegistry
import plobi.console.router as console_router
from plobi.delegation.tracker import SubagentTracker, set_tracker
from plobi.mind.project_tree import ENTRY_FILE_NAMES

VAULT_PROJECTS = "Vault/projects"


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(console_router.router, prefix="/api")
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def mind_home(tmp_path, monkeypatch):
    """隔离的名册 + 一个存在的假 Mind 库 + 不碰真 profile 的 spawn。"""
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
    root = tmp_path / "mind"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MIND_ROOT", str(root))

    registry = AgentRegistry(path=tmp_path / "projects.yaml")
    console_router.set_registry(registry)
    set_tracker(SubagentTracker())

    def fake_spawn(name, *, clone_from=None, write_config=True):
        return {"name": name, "clone_from": clone_from}

    monkeypatch.setattr(registry, "spawn", fake_spawn)
    yield registry
    console_router.set_registry(None)
    set_tracker(None)


def live_registry() -> AgentRegistry:
    """``create_agent`` 收尾会 reload_registry()，所以断言读活的那份 singleton。"""
    return console_router.get_registry()


def vault(root: Path) -> Path:
    return root / VAULT_PROJECTS


def tree_names(root: Path) -> list[str]:
    base = vault(root)
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir())


def register(registry: AgentRegistry, name: str, **raw) -> None:
    """直接摆一条进名册（绕开创建门，模拟已有 / 存量数据）。"""
    registry.agents[name] = AgentEntry.from_dict(
        name, {"role": "l2_project", "category": "projects", **raw}
    )


def make_tree(root: Path, name: str, *, plan: str = "") -> None:
    directory = vault(root) / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "plan.md").write_text(
        plan or f"---\nproject: {name}\n---\n\n# {name}\n", encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# ② 建成功后，mind_subtree 指的那棵树与三件套真的在盘上
# --------------------------------------------------------------------------- #


def test_project_create_makes_the_mind_tree_and_mounts_it(client, mind_home, tmp_path):
    response = client.post(
        "/api/agents",
        json={
            "id": "stithy",
            "name": "Stithy",
            "category": "projects",
            "description": "从零手写编程语言系统",
        },
    )
    assert response.status_code == 200, response.json()
    row = response.json()["data"]

    # 行上带出挂了哪棵树；目录名用用户打出来的那个（保留大小写），不是 slug。
    assert row["mindSubtree"] == f"{VAULT_PROJECTS}/Stithy"

    directory = vault(tmp_path / "mind") / "Stithy"
    assert directory.is_dir()
    for name in ENTRY_FILE_NAMES:
        assert (directory / name).is_file(), f"{name} 没建出来"

    plan = (directory / "plan.md").read_text(encoding="utf-8")
    # frontmatter 的键 = Mind 的 ``Loom/scripts/verifier.py`` 重生成 INDEX 时读的那几个。
    for key in ("project", "title", "type", "group", "cloud", "status",
                "created", "updated", "tech", "summary"):
        assert f"\n{key}:" in plan, f"{key} 这一格没了"
    assert "从零手写编程语言系统" in plan
    # 设备事实不进 Mind（2026-09-28 那轮元数据修正把盘符从 cloud 格里清出去了）。
    assert "D:\\" not in plan and str(tmp_path) not in plan

    # 注册表这一格也落了盘，不只活在内存里。
    saved = AgentRegistry.load(mind_home.path).get("stithy")
    assert saved is not None
    assert saved.mind_subtree == f"{VAULT_PROJECTS}/Stithy"


def test_research_category_projects_under_the_research_group(client, mind_home, tmp_path):
    response = client.post(
        "/api/agents", json={"id": "wealth-lab", "name": "Wealth-Lab", "category": "research"}
    )
    assert response.status_code == 200, response.json()
    plan = (vault(tmp_path / "mind") / "Wealth-Lab" / "plan.md").read_text(encoding="utf-8")
    # 活项目里 research 那一类的配对是 type=research + group=research（Wealth-Lab/SRTP）。
    assert "type: research" in plan
    assert "group: research" in plan


def test_butler_create_does_not_touch_mind(client, mind_home, tmp_path):
    response = client.post(
        "/api/agents",
        json={"id": "housekeeping", "name": "Housekeeping", "category": "butler"},
    )
    assert response.status_code == 200, response.json()
    # butler 不是 Mind 里的项目，不许给它凭空立一个。
    assert tree_names(tmp_path / "mind") == []
    assert response.json()["data"]["mindSubtree"] == ""


# --------------------------------------------------------------------------- #
# ① 重名在创建那一刻就拒：名册不落、Mind 不建目录
# --------------------------------------------------------------------------- #


def test_case_variant_key_is_refused_without_a_row_and_without_a_tree(
    client, mind_home, tmp_path
):
    root = tmp_path / "mind"
    register(mind_home, "Nymo", display_name="Nymo", mind_subtree=f"{VAULT_PROJECTS}/Nymo")
    make_tree(root, "Nymo")

    response = client.post(
        "/api/agents", json={"id": "nymo", "name": "nymo", "category": "projects"}
    )

    assert response.status_code == 409, response.json()
    message = response.json()["error"]
    # 能行动：点名撞了哪一条，并给出出路。
    assert "Nymo" in message and "nymo" in message
    assert live_registry().get("nymo") is None
    # 被拒的这次创建连目录都不许建。
    assert tree_names(root) == ["Nymo"]


def test_same_project_name_on_another_key_is_refused(client, mind_home, tmp_path):
    root = tmp_path / "mind"
    register(mind_home, "Nymo", display_name="Nymo", mind_subtree=f"{VAULT_PROJECTS}/Nymo")
    make_tree(root, "Nymo")

    response = client.post(
        "/api/agents", json={"id": "robot-buddy", "name": "Nymo", "category": "projects"}
    )

    assert response.status_code == 409, response.json()
    assert "Nymo" in response.json()["error"]
    assert live_registry().get("robot-buddy") is None
    # 同名的第二条不许在 Mind 里再开一棵树。
    assert tree_names(root) == ["Nymo"]


def test_existing_key_held_by_another_project_name_is_refused(client, mind_home, tmp_path):
    """命中已有键、但那条槽位属于另一个项目 → 拒，不许顶替掉用户那条。"""
    register(mind_home, "nymo", display_name="Nymo")

    response = client.post(
        "/api/agents", json={"id": "nymo", "name": "另一个项目", "category": "projects"}
    )

    assert response.status_code == 409, response.json()
    assert "nymo" in response.json()["error"]
    assert live_registry().get("nymo").display_name == "Nymo"
    assert tree_names(tmp_path / "mind") == []


def test_reserved_profile_name_is_refused_before_anything_lands(client, mind_home, tmp_path):
    response = client.post(
        "/api/agents", json={"id": "plobi", "name": "Plobi", "category": "projects"}
    )

    assert response.status_code == 400, response.json()
    assert "plobi" in response.json()["error"].lower()
    assert live_registry().get("plobi") is None
    assert tree_names(tmp_path / "mind") == []


def test_a_genuinely_different_project_is_still_created(client, mind_home, tmp_path):
    root = tmp_path / "mind"
    register(mind_home, "Nymo", display_name="Nymo", mind_subtree=f"{VAULT_PROJECTS}/Nymo")
    make_tree(root, "Nymo")

    response = client.post(
        "/api/agents", json={"id": "framelet", "name": "Framelet", "category": "projects"}
    )

    assert response.status_code == 200, response.json()
    assert response.json()["data"]["mindSubtree"] == f"{VAULT_PROJECTS}/Framelet"
    assert tree_names(root) == ["Framelet", "Nymo"]


def test_existing_mind_tree_is_claimed_not_overwritten(client, mind_home, tmp_path):
    """Mind 里已有这棵树（用户自己立的项）：只认领，一个字都不覆盖。"""
    root = tmp_path / "mind"
    make_tree(root, "Orbit", plan="---\nproject: Orbit\n---\n\n# Orbit\n\n用户自己写的\n")
    before = (vault(root) / "Orbit" / "plan.md").read_text(encoding="utf-8")

    response = client.post(
        "/api/agents", json={"id": "orbit", "name": "Orbit", "category": "projects"}
    )

    assert response.status_code == 200, response.json()
    assert response.json()["data"]["mindSubtree"] == f"{VAULT_PROJECTS}/Orbit"
    after = (vault(root) / "Orbit" / "plan.md").read_text(encoding="utf-8")
    assert after == before


def test_a_tree_claimed_by_another_row_is_not_handed_over(client, mind_home, tmp_path):
    """磁盘上那棵树已挂在别条分身名下 → 第二把钥匙不许领走它。"""
    root = tmp_path / "mind"
    register(mind_home, "Nymo", display_name="别的名字",
             mind_subtree=f"{VAULT_PROJECTS}/Nymo")
    make_tree(root, "Nymo")

    response = client.post(
        "/api/agents", json={"id": "nim", "name": "Nymo", "category": "projects"}
    )

    assert response.status_code == 409, response.json()
    assert "Nymo" in response.json()["error"]
    assert live_registry().get("nim") is None


# --------------------------------------------------------------------------- #
# 编辑（同 id 再 POST）不许抹掉挂载，也不许二次立项
# --------------------------------------------------------------------------- #


def test_edit_without_the_subtree_field_keeps_the_mount(client, mind_home, tmp_path):
    """桌面不填 ``mindSubtree``；一次编辑不许把已有那条的挂载抹成空串——抹掉它就又
    变成用户 10-06 手建的那种记录，左栏再也看不出它挂过 Mind。"""
    root = tmp_path / "mind"
    register(mind_home, "prism", display_name="Prism",
             mind_subtree=f"{VAULT_PROJECTS}/Prism")
    mind_home.save()
    console_router.reload_registry()

    response = client.post(
        "/api/agents",
        json={"id": "prism", "name": "Prism", "category": "projects",
              "description": "改了一句说明"},
    )

    assert response.status_code == 200, response.json()
    assert live_registry().get("prism").mind_subtree == f"{VAULT_PROJECTS}/Prism"
    # 编辑不是新建：不许为同一个项目在 Mind 里再立一棵树。
    assert tree_names(root) == []


# --------------------------------------------------------------------------- #
# ③ Mind 建不成 = 这次创建失败：registry 里不留孤儿行
# --------------------------------------------------------------------------- #


def test_unreachable_mind_fails_the_create_and_leaves_no_orphan_row(
    client, mind_home, tmp_path, monkeypatch
):
    # resolve_root 只认存在的目录：指向一个不存在的库就是「Mind 够不着」。
    monkeypatch.setenv("MIND_ROOT", str(tmp_path / "no-such-vault"))

    response = client.post(
        "/api/agents", json={"id": "orbit", "name": "Orbit", "category": "projects"}
    )

    assert response.status_code == 400, response.json()
    assert "Mind" in response.json()["error"]
    assert live_registry().get("orbit") is None
    assert AgentRegistry.load(mind_home.path).get("orbit") is None


def test_registry_write_failure_leaves_no_row_and_no_tree(client, mind_home, tmp_path, monkeypatch):
    def fail_save(path=None):
        raise OSError("disk full")

    monkeypatch.setattr(mind_home, "save", fail_save)

    response = client.post(
        "/api/agents", json={"id": "orbit", "name": "Orbit", "category": "projects"}
    )

    assert response.status_code == 400, response.json()
    # 名册没写成：内存里那条也撤掉，刚建的树收回，Mind 里不留没主的目录。
    assert live_registry().get("orbit") is None
    assert tree_names(tmp_path / "mind") == []
    assert not (vault(tmp_path / "mind") / "Orbit").exists()


# --------------------------------------------------------------------------- #
# 存量那对只读不并，但要辨得出
# --------------------------------------------------------------------------- #


def test_roster_shows_which_row_has_no_mind_tree(client, mind_home, tmp_path):
    """用户手建的那条没有 mind_subtree：两条都留着（不替他合并 / 删除），但行上辨得出。"""
    root = tmp_path / "mind"
    register(mind_home, "Nymo", display_name="Nymo", mind_subtree=f"{VAULT_PROJECTS}/Nymo")
    register(mind_home, "nymo", display_name="Nymo")
    make_tree(root, "Nymo")
    mind_home.save()
    console_router.reload_registry()

    rows = {row["id"]: row for row in client.get("/api/agents").json()["data"]}

    assert {"Nymo", "nymo"} <= set(rows)
    assert rows["Nymo"]["mindSubtree"] == f"{VAULT_PROJECTS}/Nymo"
    assert rows["nymo"]["mindSubtree"] == ""
