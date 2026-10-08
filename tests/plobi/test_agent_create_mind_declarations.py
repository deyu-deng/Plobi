"""建完 Mind 项目树，脑仓自己那两处声明必须跟着走（AGENTS.md §1 + Vault/projects/INDEX.md）。

钉这件事的理由不在产品代码里，在脑仓自己的门禁里：``Loom/scripts/verifier.py`` 的
``check_projects()`` 把「§1 声明的清单 ≠ ``Vault/projects`` 磁盘」判成 **BLOCKER**（退出码 1），
而 ``mind/.githooks/pre-commit`` 已经装上。只建目录不补声明 = 用户在脑仓的下一次提交被我们造的洞
拦住。所以这里断的都是**关系**，不是名单：

* 建成功 → §1 那行的计数与列表**包含并等于**盘上每个项目目录、INDEX.md 里有它、门禁退出码 0；
* 重名被拒 → 三处声明与磁盘**一个字节都没动**；
* INDEX 生成失败 / 门禁不放行 → 目录、名册行、§1 那行全部还原，不留孤儿；
* 声明里写的是**磁盘上那个名字**（Mind §5：大小写按项目名本身、多词以 ``-`` 连接），不是小写 slug；
* 库不带脑仓门禁（只有 ``Vault/projects`` 的假库）→ 照旧只建三件套，不自己造一份 AGENTS.md/INDEX。

MIND_ROOT 一律指到 tmp 里的假库；真正的脑仓（``MIND_ROOT`` 指到的那个，自带 ``.git``）是用户的数据，
测试往里写一个目录就是污染他的库（这里只**读**那个仓里的 ``verifier.py`` 一份，复制进临时根去当被测的生成器）。
"""

from __future__ import annotations

import datetime as _dt
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plobi.agents.registry import AgentEntry, AgentRegistry
import plobi.console.router as console_router
from plobi.delegation.tracker import SubagentTracker, set_tracker
from plobi.mind.paths import resolve_root
from plobi.mind.project_declarations import (
    AGENTS_RELATIVE,
    INDEX_RELATIVE,
    PROJECTS_RELATIVE,
    VERIFIER_RELATIVE,
    declared_projects,
)

# 被测的生成器就是脑仓自己那一份（读它、复制进临时根；绝不改它）。
# 脑住在哪归 resolve_root() 说：MIND_ROOT 指哪儿算哪儿，没设时回退仓内 mind/。
_MIND_ROOT = resolve_root()
REAL_VERIFIER = (
    _MIND_ROOT / VERIFIER_RELATIVE
    if _MIND_ROOT is not None
    else Path("mind-root-not-configured") / VERIFIER_RELATIVE
)
PLACEHOLDER_INDEX = "# Projects · 索引（自动生成，勿手改）\n\n（这一版是人手放的占位）\n"


# --------------------------------------------------------------------------- #
# 一个带脑仓自己门禁的假 Mind 根
# --------------------------------------------------------------------------- #


def agents_document(declared: list[str]) -> str:
    """形状抄自脑仓 ``AGENTS.md`` §1 真实那行（树形标记 + 计数 + 括注 + 全角冒号 + 名单）。"""
    return (
        "# Mind\n"
        "\n"
        "## 1. 结构\n"
        "\n"
        "```\n"
        "<MIND_ROOT>\n"
        "├── Loom/                   ← AI 消化区\n"
        "│   ├── skills/\n"
        "│   └── scripts/\n"
        "└── Vault/                  ← 人+AI 协作区\n"
        f"    └── projects/           ← {len(declared)} 个项目（推进中的项目）："
        + " / ".join(declared)
        + "\n"
        "        └── INDEX.md        ← 项目导航入口\n"
        "```\n"
    )


def make_plan(directory: Path, name: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    today = _dt.date.today().isoformat()
    (directory / "plan.md").write_text(
        "---\n"
        f"project: {name}\n"
        f"title: {name}\n"
        "type: engineering\n"
        "group: product\n"
        f"cloud: {name}\n"
        "status: active\n"
        f"created: {today}\n"
        f"updated: {today}\n"
        "tech: []\n"
        f"summary: {name} 的一句话\n"
        "---\n"
        "\n"
        f"# {name}\n",
        encoding="utf-8",
    )


def build_brain(
    root: Path, *, disk: list[str], declared: list[str] | None = None
) -> Path:
    """一个「装了门禁」的 Mind 根：AGENTS.md §1 + Loom/scripts/verifier.py + Vault/projects。

    ``disk`` 是盘上真有的项目目录，``declared`` 是 §1 那行此刻写着的那些——两者故意可以不一致
    （存量漂移），用来验「对齐到盘」而不是「在旧名单上加一项」。
    """
    if _MIND_ROOT is None:
        pytest.skip(
            "这台没配脑仓（MIND_ROOT 未设且仓内无 mind/），没有可复用的生成器"
        )
    if not REAL_VERIFIER.is_file():
        pytest.fail(
            f"脑仓已配置在 {_MIND_ROOT}，但那里找不到 {VERIFIER_RELATIVE} —— "
            "脑仓的门禁脚本本身缺位，跳过就等于让这条声明纪律没人验"
        )

    script = root / VERIFIER_RELATIVE
    script.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REAL_VERIFIER, script)

    # 门禁的其余检查项要有东西可查，否则退出码 2（脚本自身异常）掩盖我们要验的那一条。
    skills = root / "Loom" / "skills"
    skills.mkdir(parents=True, exist_ok=True)
    (skills / "INDEX.md").write_text("total_skills: 0\n", encoding="utf-8")
    (root / "Loom" / "wiki").mkdir(parents=True, exist_ok=True)

    base = root / PROJECTS_RELATIVE
    base.mkdir(parents=True, exist_ok=True)
    for name in disk:
        make_plan(base / name, name)

    (root / AGENTS_RELATIVE).write_text(
        agents_document(sorted(disk) if declared is None else declared), encoding="utf-8"
    )
    (root / INDEX_RELATIVE).write_text(PLACEHOLDER_INDEX, encoding="utf-8")
    return root


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(console_router.router, prefix="/api")
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def isolated_registry(tmp_path, monkeypatch):
    """隔离名册 + 不碰真 profile 的 spawn（Mind 根由 ``make_brain`` 指）。"""
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


@pytest.fixture()
def make_brain(tmp_path, monkeypatch, isolated_registry):
    """造假脑仓并把 MIND_ROOT 指过去，返回那份隔离名册。"""

    def _make(*, disk: list[str], declared: list[str] | None = None) -> Path:
        root = build_brain(
            tmp_path / "mind", disk=disk, declared=declared
        )
        monkeypatch.setenv("MIND_ROOT", str(root))
        return root

    return _make


# --------------------------------------------------------------------------- #
# 读盘 / 读声明的小探针（断关系用，不冻数字与名单）
# --------------------------------------------------------------------------- #


def disk_names(root: Path) -> list[str]:
    base = root / PROJECTS_RELATIVE
    if not base.is_dir():
        return []
    return sorted(child.name for child in base.iterdir() if child.is_dir())


def agents_bytes(root: Path) -> bytes | None:
    path = root / AGENTS_RELATIVE
    return path.read_bytes() if path.is_file() else None


def index_bytes(root: Path) -> bytes | None:
    path = root / INDEX_RELATIVE
    return path.read_bytes() if path.is_file() else None


def snapshot(root: Path) -> dict:
    return {
        "agents": agents_bytes(root),
        "index": index_bytes(root),
        "disk": disk_names(root),
    }


def run_gate(root: Path, *args: str) -> subprocess.CompletedProcess:
    """直接跑那个根自己的 verifier——就是脑仓 pre-commit 跑的那一次。"""
    return subprocess.run(
        [sys.executable, str(root / VERIFIER_RELATIVE), *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )


def create(client, payload: dict):
    return client.post("/api/agents", json=payload)


def live_registry() -> AgentRegistry:
    return console_router.get_registry()


def break_verifier(root: Path, *, mode: str) -> None:
    """把复制进临时根的那个生成器换成失败现场（真脑仓的脚本一个字不碰）。"""
    if mode == "always":  # --fix-projects 与门禁两次都失败
        body = 'import sys\nsys.stderr.write("生成器挂了\\n")\nsys.exit(3)\n'
    elif mode == "gate":  # 生成成功、门禁不放行
        body = (
            "import sys\n"
            'if "--fix-projects" in sys.argv:\n'
            "    sys.exit(0)\n"
            'sys.stderr.write("BLOCKER: 声明与磁盘不一致\\n")\n'
            "sys.exit(1)\n"
        )
    else:  # 脚本自身跑不起来（退出码 2 那条路）
        body = 'import sys\nraise ValueError("verifier 运行异常")\n'
    (root / VERIFIER_RELATIVE).write_text(body, encoding="utf-8")


# --------------------------------------------------------------------------- #
# ① 建成功 → 两处声明都带上它，且脑仓门禁放行
# --------------------------------------------------------------------------- #


def test_a_new_project_lands_in_both_declarations_and_the_gate_passes(
    client, make_brain, isolated_registry
):
    root = make_brain(disk=["Aura", "Kit"])

    response = create(
        client,
        {"id": "orbit", "name": "Orbit", "category": "projects",
         "description": "新的一格"},
    )

    assert response.status_code == 200, response.json()
    names = disk_names(root)
    assert "Orbit" in names

    # §1 那一行：计数与列表两处都对得上盘（断关系，不冻「13」这种数字）。
    declared = declared_projects((root / AGENTS_RELATIVE).read_text(encoding="utf-8"))
    assert declared is not None, "§1 的声明行被写成了门禁读不到的形状"
    assert sorted(declared) == names
    line = next(
        row for row in (root / AGENTS_RELATIVE).read_text(encoding="utf-8").splitlines()
        if "projects/" in row
    )
    assert f"{len(names)} 个项目" in line

    # INDEX.md 由脑仓自己的生成器重跑过（人手的占位版已被覆盖），里面有它。
    index = (root / INDEX_RELATIVE).read_text(encoding="utf-8")
    assert index != PLACEHOLDER_INDEX
    assert "Orbit" in index

    # 门禁：脑仓 pre-commit 跑的那一次必须放行——「磁盘有目录、声明里没有」就是它拦的。
    gate = run_gate(root)
    assert gate.returncode == 0, gate.stdout + gate.stderr

    # 名册那一行也确实落了盘（不是只活在内存里）。
    assert AgentRegistry.load(isolated_registry.path).get("orbit") is not None
    # 三件套在、且改动只发生在应有的地方（临时根里没留下写锁一类的残骸）。
    assert (root / PROJECTS_RELATIVE / "Orbit" / "plan.md").is_file()
    assert not (root / ".plobi-mind.lock").exists()


def test_a_stale_declaration_is_aligned_with_disk_not_just_appended(
    client, make_brain, isolated_registry
):
    """存量漂移（盘上有 ``Prism`` 但 §1 没声明）：同步之后声明等于盘，不是「旧名单 + 新项」。"""
    root = make_brain(disk=["Aura", "Prism"], declared=["Aura"])

    response = create(client, {"id": "orbit", "name": "Orbit", "category": "projects"})

    assert response.status_code == 200, response.json()
    declared = declared_projects((root / AGENTS_RELATIVE).read_text(encoding="utf-8"))
    assert sorted(declared) == disk_names(root) == ["Aura", "Orbit", "Prism"]
    assert run_gate(root).returncode == 0


def test_a_fresh_brain_gets_its_empty_declaration_line_filled(
    client, make_brain, isolated_registry
):
    """还没立过项的 §1 那行（冒号后空着）：第一次创建把它填上，之后门禁才管得住它。"""
    root = make_brain(disk=[], declared=[])

    response = create(client, {"id": "orbit", "name": "Orbit", "category": "projects"})

    assert response.status_code == 200, response.json()
    assert declared_projects((root / AGENTS_RELATIVE).read_text(encoding="utf-8")) == ["Orbit"]
    assert run_gate(root).returncode == 0


def test_the_declaration_lists_the_disk_name_not_a_lowercase_slug(
    client, make_brain, isolated_registry
):
    """Mind §5：目录名尊重用户给定名称（大小写按本身、多词以 ``-`` 连），声明里就写那个名字。"""
    root = make_brain(disk=["SRTP"])

    response = create(
        client, {"id": "battery-box", "name": "Battery Box", "category": "projects"}
    )

    assert response.status_code == 200, response.json()
    text = (root / AGENTS_RELATIVE).read_text(encoding="utf-8")
    assert "Battery-Box" in disk_names(root)  # 多词以 - 连接
    assert declared_projects(text) == ["Battery-Box", "SRTP"]  # 大小写按项目名本身
    assert "battery-box" not in text and "Battery Box" not in text


# --------------------------------------------------------------------------- #
# ② 重名被拒：三处声明与磁盘一个字都没变
# --------------------------------------------------------------------------- #


def test_a_refused_duplicate_changes_not_a_byte(client, make_brain, isolated_registry):
    root = make_brain(disk=["Nymo"])
    isolated_registry.agents["Nymo"] = AgentEntry.from_dict(
        "Nymo",
        {"role": "l2_project", "category": "projects", "display_name": "Nymo",
         "mind_subtree": f"{PROJECTS_RELATIVE}/Nymo"},
    )
    before = snapshot(root)

    response = create(client, {"id": "nymo", "name": "nymo", "category": "projects"})

    assert response.status_code == 409, response.json()
    assert snapshot(root) == before
    assert live_registry().get("nymo") is None
    assert AgentRegistry.load(isolated_registry.path).get("nymo") is None


def test_a_refused_tree_owner_claim_changes_not_a_byte(client, make_brain, isolated_registry):
    """那棵树已挂在别条分身名下 → 拒；这条路径同样不许在声明里留下半个字。"""
    root = make_brain(disk=["Nymo"])
    isolated_registry.agents["Nymo"] = AgentEntry.from_dict(
        "Nymo",
        {"role": "l2_project", "category": "projects", "display_name": "别的名字",
         "mind_subtree": f"{PROJECTS_RELATIVE}/Nymo"},
    )
    before = snapshot(root)

    response = create(client, {"id": "nim", "name": "Nymo", "category": "projects"})

    assert response.status_code == 409, response.json()
    assert snapshot(root) == before


# --------------------------------------------------------------------------- #
# ③ 声明同步失败 = 创建失败：目录、名册行、§1 那行一起回滚
# --------------------------------------------------------------------------- #


def test_a_failed_index_generation_rolls_back_tree_row_and_declaration(
    client, make_brain, isolated_registry
):
    root = make_brain(disk=["Aura", "Kit"])
    before = snapshot(root)
    break_verifier(root, mode="always")

    response = create(client, {"id": "orbit", "name": "Orbit", "category": "projects"})

    assert response.status_code == 400, response.json()
    # 能行动：错误点名是哪个工具没跑成。
    message = response.json()["error"]
    assert VERIFIER_RELATIVE in message and "--fix-projects" in message
    # 磁盘、两处声明、名册全回到动手前。
    assert snapshot(root) == before
    assert "Orbit" not in disk_names(root)
    assert live_registry().get("orbit") is None
    assert AgentRegistry.load(isolated_registry.path).get("orbit") is None


def test_a_rejected_gate_rolls_back_everything_it_touched(
    client, make_brain, isolated_registry
):
    """--fix-projects 跑成了、但门禁不放行：同样整体回滚，绝不留「目录在、声明不对」的半成品。"""
    root = make_brain(disk=["Aura", "Kit"])
    before = snapshot(root)
    break_verifier(root, mode="gate")

    response = create(client, {"id": "orbit", "name": "Orbit", "category": "projects"})

    assert response.status_code == 400, response.json()
    assert "门禁" in response.json()["error"]
    assert snapshot(root) == before
    assert live_registry().get("orbit") is None


def test_a_verifier_that_cannot_run_fails_the_creation_and_leaves_no_orphan(
    client, make_brain, isolated_registry
):
    root = make_brain(disk=["Aura", "Kit"])
    before = snapshot(root)
    # 脚本自身异常（verifier 的退出码 2 那条路）
    break_verifier(root, mode="crash")

    response = create(client, {"id": "orbit", "name": "Orbit", "category": "projects"})

    assert response.status_code == 400, response.json()
    assert snapshot(root) == before
    assert live_registry().get("orbit") is None


def test_a_brain_without_its_generator_refuses_to_invent_one(
    client, tmp_path, monkeypatch, isolated_registry
):
    """带着 AGENTS.md 却没有 verifier：不许自己另写一套 INDEX 生成器，直接判创建失败。"""
    root = tmp_path / "half-brain"
    build_brain(root, disk=["Aura"])
    (root / VERIFIER_RELATIVE).unlink()
    monkeypatch.setenv("MIND_ROOT", str(root))
    before = snapshot(root)

    response = create(client, {"id": "orbit", "name": "Orbit", "category": "projects"})

    assert response.status_code == 400, response.json()
    assert VERIFIER_RELATIVE in response.json()["error"]
    assert snapshot(root) == before
    assert "Orbit" not in disk_names(root)


def test_a_registry_write_failure_undoes_the_two_declarations_too(
    client, make_brain, isolated_registry, monkeypatch
):
    """声明已同步、名册却没写成（磁盘满一类）：声明与项目树一起收回，不留「声明里有、名册里没有」。"""
    root = make_brain(disk=["Aura", "Kit"])
    before = snapshot(root)

    def fail_save(path=None):
        raise OSError("disk full")

    monkeypatch.setattr(isolated_registry, "save", fail_save)

    response = create(client, {"id": "orbit", "name": "Orbit", "category": "projects"})

    assert response.status_code == 400, response.json()
    assert snapshot(root) == before
    assert live_registry().get("orbit") is None


# --------------------------------------------------------------------------- #
# ④ 边界：库不带脑仓门禁时，只建三件套，不自己造声明
# --------------------------------------------------------------------------- #


def test_a_root_without_the_brain_gates_still_creates_just_the_tree(
    client, tmp_path, monkeypatch, isolated_registry
):
    root = tmp_path / "plain"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MIND_ROOT", str(root))

    response = create(client, {"id": "orbit", "name": "Orbit", "category": "projects"})

    assert response.status_code == 200, response.json()
    assert (root / PROJECTS_RELATIVE / "Orbit" / "plan.md").is_file()
    # 没有声明可同步 ≠ 自己造一份：那正是脑仓门禁与磁盘长期不一致的来源。
    assert not (root / AGENTS_RELATIVE).exists()
    assert not (root / INDEX_RELATIVE).exists()
