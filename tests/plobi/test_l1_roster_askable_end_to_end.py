"""WP-L1-ROSTER（R-072）③ —— 名单里的每一行都真的能问，问不到的在名单里就标出来。

用户 2026-10-07 要的是这条链走通：「只要新建了 Agent，L2 配套的东西都自动建立好并登记好，
L1 就可以看到一个名单，自己根据任务决定问谁。」

钉的是四件事：

1. **创建那一刻**：``POST /api/agents`` 之后，名册那条盖着「已登记」这一章、分身的家在
   盘上真的存在（``spawn`` 的产物），于是 ``run_project_status`` 的 ``roster`` 里有他、
   ``askable`` 里也有他——不用用户再补一步。
2. **问到跟前的那一路**：拿这份名单走 ``plobi_agent_ask``——目标解析、起子进程要用的
   argv/env/cwd 都钉在这个分身自己家上；它自己的会话记录里那条回答被原样读回来
   （``state.db`` 是真写的，读也是真读的）。子进程本身用 ``_run_child`` 桩替掉：这台
   Windows 跑不了 shebang 脚本（``tests/plobi/test_agent_ask.py`` 那 4 条基线红就是它），
   换掉它不换掉任何一段被判据的代码。
3. **问不到的那几行**：留在名单里，带 ``askable=false`` + ``not_askable_because``；
   工具那一侧回的是**同一句原因**，不是含混的「查无此人 / 不是项目分身」。
4. **名单与嘴同源**：roster 里 ``askable=true`` 的那些 == ``plobi_agent_ask`` 筛出来的那些。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import plobi.console.router as console_router
from plobi.agents.registry import AgentEntry, AgentRegistry
from plobi.delegation.tracker import SubagentTracker, set_tracker

REPO = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO / "plugins" / "plobi-north-star"

_ANSWER = "分身本人的回答：导出这块本周能接上，还剩测试没补。"
_SESSION = "roster-e2e-session-1"


def _load_ask_module():
    """按插件的真实加载方式拿到 ``agent_ask``（目录名带连字符，不能点号导入）。"""
    import importlib.util

    name = "plobi_test_roster_ask_ns_pkg"
    if name in sys.modules:
        return sys.modules[name].AA
    spec = importlib.util.spec_from_file_location(
        name, PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)]
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    module.__package__ = name
    module.__path__ = [str(PLUGIN_DIR)]
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module.AA


AA = _load_ask_module()


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(console_router.router, prefix="/api")
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """隔离名册 + 假 Mind 库 + **真的** spawn 落盘。

    ``plobi_cli.profiles`` 的那几个路径函数在这台 Windows 上和 pytest 的 tmp 家对不上
    （``tests/plobi/test_agent_ask.py`` 里那 4 条基线红的就是这个），所以这里把
    profile 根的解析钉在 tmp 上——判据读的仍是同一个 ``profile_exists``，只是家挪到
    tmp。落盘这一步（``create_profile``）缩成 mkdir，其余照真的走。
    """
    plobi_home = tmp_path / ".plobi"
    (plobi_home / "plobi").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("PLOBI_HOME", str(plobi_home))
    monkeypatch.delenv("PLOBI_MODELS_CONFIG", raising=False)
    monkeypatch.delenv("PLOBI_PROJECTS_CONFIG", raising=False)
    monkeypatch.delenv("PLOBI_TUI", raising=False)

    registry_path = plobi_home / "plobi" / "projects.yaml"
    monkeypatch.setattr("plobi.agents.registry.default_path", lambda: registry_path)
    monkeypatch.setattr(
        "plobi.agents.registry.DEFAULT_PROJECT_PATHS",
        {
            "projects": str(tmp_path / "projects"),
            "events": str(tmp_path / "cloud" / "events"),
            "research": str(tmp_path / "cloud" / "research"),
            "butler": "",
        },
    )
    (tmp_path / "mind").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MIND_ROOT", str(tmp_path / "mind"))

    import plobi_cli.profiles as profiles_mod

    def profile_dir(name: str) -> Path:
        return plobi_home / "profiles" / name

    def fake_create_profile(name, **kwargs):
        directory = profile_dir(name)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "plobi").mkdir(exist_ok=True)
        (directory / "config.yaml").write_text("model: {}\n", encoding="utf-8")
        return directory

    monkeypatch.setattr(profiles_mod, "create_profile", fake_create_profile)
    monkeypatch.setattr(profiles_mod, "get_profile_dir", profile_dir)
    monkeypatch.setattr(
        profiles_mod,
        "profile_exists",
        lambda name: (profile_dir(name).is_dir() if name != "default" else True),
    )

    def fake_resolve_profile_env(name):
        directory = profile_dir(name)
        if not directory.is_dir():
            raise FileNotFoundError(f"Profile '{name}' does not exist.")
        return str(directory)

    monkeypatch.setattr(profiles_mod, "resolve_profile_env", fake_resolve_profile_env)

    registry = AgentRegistry(path=registry_path)
    console_router.set_registry(registry)
    set_tracker(SubagentTracker())
    yield tmp_path
    console_router.set_registry(None)
    set_tracker(None)


def _registry(home) -> AgentRegistry:
    return AgentRegistry.load(home / ".plobi" / "plobi" / "projects.yaml")


def _seed_child_answer(profile_dir: Path, question: str, answer: str) -> None:
    """分身自己家里那条会话记录——问答的真源就是它。"""
    from plobi_state import SessionDB

    db = SessionDB(db_path=profile_dir / "state.db")
    db.create_session(_SESSION, source="cli")
    db.append_message(_SESSION, role="user", content=question)
    db.append_message(_SESSION, role="assistant", content=answer)


_SEEN_ARGV: list[list[str]] = []


def _child_ran(argv, env, cwd, limit):
    _SEEN_ARGV.append(list(argv))
    return {
        "timed_out": False,
        "missing_binary": False,
        "returncode": 0,
        "stdout": "decoy stdout must never become the answer",
        "stderr": "",
        "duration": 1.0,
    }


# ---------------------------------------------------------------------------
# ① 新建那一刻就登记好 + 物化好，名单里立刻有他
# ---------------------------------------------------------------------------


def test_created_agent_is_registered_and_materialized_without_a_second_step(
    client, home
):
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

    entry = _registry(home).get("stithy")
    assert entry is not None
    # 章是创建那一步盖的，不是事后从描述里反推的。
    assert entry.registered is True
    # 配套的东西也真的落了盘：profile 家 + 那个分身自己的身份段。
    profile_dir = home / ".plobi" / "profiles" / "stithy"
    assert profile_dir.is_dir()
    assert (profile_dir / "SOUL.md").is_file()
    assert ":::PLOBI_L2_IDENTITY:::" in (profile_dir / "SOUL.md").read_text(encoding="utf-8")

    from plobi.agents.registry import run_project_status

    payload = run_project_status("手下有谁", registry=_registry(home))
    assert "stithy" in payload["roster"], payload
    assert "stithy" in payload["askable"], payload
    row = next(r for r in payload["projects"] if r["id"] == "stithy")
    assert (row["registered"], row["askable"], row["not_askable_because"]) == (True, True, "")


# ---------------------------------------------------------------------------
# ② 名单 → 可问 → 回答
# ---------------------------------------------------------------------------


def test_roster_row_is_askable_and_answers_end_to_end(client, home, monkeypatch):
    """这条就是用户要的那一句：看到名单 → 挑一个问 → 拿到他本人的回答。"""
    response = client.post(
        "/api/agents",
        json={"id": "stithy", "name": "Stithy", "category": "projects"},
    )
    assert response.status_code == 200, response.json()

    from plobi.agents.registry import run_project_status

    registry = _registry(home)
    payload = run_project_status("各项目怎么样了", registry=registry)
    assert payload["askable"] == ["stithy"]

    profile_dir = home / ".plobi" / "profiles" / "stithy"
    question = "导出那块推到哪了？"
    # 分身在自己的家里答了一次（真写的 sqlite），问答的记录只在他那儿。
    _seed_child_answer(profile_dir, question, _ANSWER)
    monkeypatch.setattr(AA, "_run_child", _child_ran)
    _SEEN_ARGV.clear()

    body = json.loads(AA.handle_agent_ask({"agent": "Stithy", "question": question}))
    assert body.get("ok") is True, body
    assert body["agent"] == "stithy"
    # 回答来自他自己那条会话记录，不是子进程顺手打印的那行。
    assert body["answer"] == _ANSWER
    assert body["session_id"] == _SESSION

    # 起子进程那一路也是钉在这个分身自己家的（不是秘书的家）。
    entry = registry.get("stithy")
    argv, child_env, cwd = AA.build_child_command(entry, question)
    assert child_env["PLOBI_HOME"] == str(profile_dir)
    assert child_env["PLOBI_PROFILE"] == "stithy"
    # 前缀是「这台机器怎么找到 plobi 可执行」的问题（POSIX 是 plobi 一条，
    # Windows 是 python.exe -m plobi_cli 几条），所以从同一个解析器取长度，
    # 只钉我们真正要约定的那五个参数。
    from plobi_cli.plobi_bin import resolve_plobi_argv

    prefix = resolve_plobi_argv()
    assert argv[: len(prefix)] == list(prefix)
    assert argv[len(prefix) : len(prefix) + 5] == ["-p", "stithy", "--cli", "--accept-hooks", "chat"]
    assert argv[len(prefix) + 5 : len(prefix) + 6] == ["-q"]
    assert not any("yolo" in part.casefold() for part in argv)

    # 问题带着「谁在问」进到他那一轮——证据是**发出去的那条 `-q`**：
    # 这里的子进程是假的，分身家里那条会话记录是测试自己预先种的裸问题，
    # 拿库里的行断言「谁在问」只会测到测试桩，测不到 compose_question。
    assert len(_SEEN_ARGV) == 1, _SEEN_ARGV
    sent = _SEEN_ARGV[0]
    asked = sent[sent.index("-q") + 1]
    assert "总秘书" in asked and question in asked
    # 问答的记录只落在他自己家里（秘书那边看不到第二份）。
    conn = sqlite3.connect(str(profile_dir / "state.db"))
    try:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id",
            (_SESSION,),
        ).fetchall()
    finally:
        conn.close()
    assert [role for role, _ in rows] == ["user", "assistant"]


# ---------------------------------------------------------------------------
# ③ 问不到的：留在名单里 + 同一句原因
# ---------------------------------------------------------------------------


def _mixed_registry(home, monkeypatch) -> AgentRegistry:
    registry = _registry(home)
    registry.agents = {
        # 登记过 + 家已落地 → 名单里、且问得到。
        "live": AgentEntry(name="live", role="l2_project", category="projects", registered=True),
        # 登记过 + 家没落地 → 名单里、但标问不到。
        "no-home": AgentEntry(
            name="no-home", display_name="No Home", role="l2_project",
            category="projects", registered=True,
        ),
        # Mind 投影 → 候选，不算手下。
        "seed": AgentEntry(name="seed", role="l2_project", category="projects", registered=False),
        # 归档的手下 → 哪儿都不该出现。
        "gone": AgentEntry(
            name="gone", role="l2_project", category="projects",
            registered=True, archived=True,
        ),
        # 日程 L2 有它自己那张嘴。
        "agenda": AgentEntry(name="agenda", role="l2_agenda", profile="l2-agenda", registered=True),
    }
    registry.save()
    monkeypatch.setattr(
        "plobi.agents.registry.default_path",
        lambda: home / ".plobi" / "plobi" / "projects.yaml",
    )
    (home / ".plobi" / "profiles" / "live").mkdir(parents=True, exist_ok=True)
    return registry


def test_unaskable_row_stays_on_the_roster_and_says_why(home, monkeypatch):
    from plobi.agents.registry import run_project_status

    registry = _mixed_registry(home, monkeypatch)
    payload = run_project_status("手下有谁", registry=registry)

    # 没被偷偷删掉——他还在名单里。
    assert payload["roster"] == ["live", "no-home"]
    assert payload["askable"] == ["live"]
    assert payload["mind_candidates"] == ["seed"]
    # 归档的那条哪儿都不出现（裁定 19：留在盘上，但从默认名单里藏掉）。
    assert "gone" not in {row["id"] for row in payload["projects"]}

    row = next(r for r in payload["projects"] if r["id"] == "no-home")
    assert row["registered"] is True and row["askable"] is False
    assert "还没在这台机器上落地" in row["not_askable_because"]

    # 工具那一侧不许把他说成「查无此人」：给的是名单里同一句原因。
    body = json.loads(AA.handle_agent_ask({"agent": "no-home", "question": "在吗"}))
    assert body["ok"] is False
    assert "还没在这台机器上落地" in body["error"]
    assert body["available"] == ["live"]


def test_archived_and_agenda_rows_get_their_own_reason(home, monkeypatch):
    _mixed_registry(home, monkeypatch)

    gone = json.loads(AA.handle_agent_ask({"agent": "gone", "question": "在吗"}))
    assert gone["ok"] is False and "归档" in gone["error"]

    agenda = json.loads(AA.handle_agent_ask({"agent": "agenda", "question": "在吗"}))
    assert agenda["ok"] is False and "不是项目分身" in agenda["error"]
    # 拒答里报出来的「能问的」必须是真能问的那一行。
    assert agenda["available"] == ["live"]


def test_roster_askable_set_and_the_tools_gate_are_the_same_list(home, monkeypatch):
    """同源判据：名单说问得到的，和这张嘴筛出来的，必须是同一批人。"""
    from plobi.agents.registry import run_project_status

    registry = _mixed_registry(home, monkeypatch)
    payload = run_project_status("手下有谁", registry=registry)

    assert sorted(payload["askable"]) == sorted(e.name for e in AA.askable_entries())
    # 服务门也走同一份判据：有能问的人才把这张嘴端给模型。
    assert AA.has_askable_agents() is True
    assert bool(payload["askable"]) == AA.has_askable_agents()


def test_no_somebody_to_ask_means_the_gate_closes(home, monkeypatch):
    from plobi.agents.registry import AgentEntry, run_project_status

    registry = _registry(home)
    registry.agents = {
        "seed": AgentEntry(name="seed", role="l2_project", category="projects", registered=False),
    }
    registry.save()
    monkeypatch.setattr(
        "plobi.agents.registry.default_path",
        lambda: home / ".plobi" / "plobi" / "projects.yaml",
    )

    payload = run_project_status("手下有谁", registry=registry)
    assert payload["roster"] == [] and payload["askable"] == []
    assert AA.askable_entries() == []
