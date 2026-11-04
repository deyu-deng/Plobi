# -*- coding: utf-8 -*-
"""身份名改名要做成**全链**（WP-AGENT-RENAME-FULL-CHAIN，判据在纸 §1）。

为什么有这一刀（2026-11-02 现读）：名册那一行的 ``name`` 就是身份（裁定 192），可它
同时被十几处当钥匙用。以前 ``rename_identity`` 只搬三样（身份文件／它那一份排程／
总账的 ``agent_id``），剩下的仍按旧名躺在盘上——最坏的一处是看板：调度器认不出旧
assignee，就把卡**静默**停在 ``ready`` 不出声。

这里钉五件事：

1. **全链**：一次改名，记忆那份／信任账／删除台账／外部会话索引／笔记 ``belongs:``／
   看板三列／``config.yaml`` 那两格／Mind 子树目录与那一格字段，全都跟着走。
2. **旧写法照样认得到人**：别名表 + ``say``/``ask`` 的收件人解析。
3. **dry-run 不碰盘**。
4. **中途失败就全退回**：名册不留半截状态。
5. **有卡在该人身上跑时拒绝改名**，并留一份 journal。
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from plobi_cli import profiles as profiles_mod

REPO = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO / "plugins" / "plobi-north-star"

ROSTER_YAML = (
    "agents:\n"
    "  Aura:\n"
    "    role: l2_project\n"
    "    profile: aura\n"
    "    category: projects\n"
    "    mind_subtree: Surface/dossiers/projects/Aura\n"
    "    description: 名册上的那一行\n"
    "  agenda:\n"
    "    role: l2_agenda\n"
    "    profile: l2-agenda\n"
)


@pytest.fixture(scope="module")
def CH():
    """按包方式加载 north-star 插件（``channel.py`` 有相对导入）。"""
    pkg_name = "plobi_test_rename_ns_pkg"
    if pkg_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            pkg_name,
            PLUGIN_DIR / "__init__.py",
            submodule_search_locations=[str(PLUGIN_DIR)],
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        module.__package__ = pkg_name
        module.__path__ = [str(PLUGIN_DIR)]
        sys.modules[pkg_name] = module
        spec.loader.exec_module(module)
    return importlib.import_module(f"{pkg_name}.channel")


@pytest.fixture()
def full_home(tmp_path, monkeypatch):
    """一个带齐"改名要动的每一处"的假家：名册／总账／身份文件／排程／记忆／信任账／
    删除台账／外部会话索引／笔记／看板／config。"""
    home = tmp_path / "plobi-home"
    (home / "plobi").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("PLOBI_HOME", str(home))
    monkeypatch.delenv("PLOBI_STATE_HOME", raising=False)
    roster = home / "plobi" / "projects.yaml"
    roster.write_text(ROSTER_YAML, encoding="utf-8")
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(roster))

    surface = tmp_path / "surface"
    surface.mkdir(parents=True, exist_ok=True)
    (tmp_path / "mind").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SURFACE_ROOT", str(surface))
    monkeypatch.setenv("MIND_ROOT", str(tmp_path / "mind"))

    from plobi_state import SessionDB, get_plobi_state_home

    state_db = get_plobi_state_home() / "state.db"
    state_db.parent.mkdir(parents=True, exist_ok=True)
    db = SessionDB(db_path=state_db)
    db.create_session("sess-1", "desktop", agent_id="Aura")
    db.create_session("sess-2", "desktop", agent_id="agenda")
    db.close()

    from plobi.agents.registry import ensure_l2_identity_soul, load_registry

    entry = load_registry().agents["Aura"]
    assert ensure_l2_identity_soul(entry) is True

    store = home / "cron" / "agents" / "Aura"
    store.mkdir(parents=True)
    (store / "jobs.json").write_text("[]\n", encoding="utf-8")

    from tools.memory_tool import get_memory_dir, resolve_agent_slug

    mem = get_memory_dir() / "agents" / resolve_agent_slug("Aura")
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "MEMORY.md").write_text("- Aura 记的一条\n", encoding="utf-8")

    (get_memory_dir() / "deletions.jsonl").write_text(
        json.dumps({"agent": "Aura", "kind": "forget"}, ensure_ascii=False) + "\n"
        + json.dumps({"agent": "agenda", "kind": "forget"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    (home / "l1-trust.json").write_text(
        json.dumps({"memory::Aura": {"streak": 3}, "memory::agenda": {"streak": 1}}),
        encoding="utf-8",
    )

    ext = home / "external_sessions"
    ext.mkdir(parents=True, exist_ok=True)
    (ext / "index.json").write_text(
        json.dumps({"Aura": {"s1": {"vendor": "codebuddy"}}}, ensure_ascii=False),
        encoding="utf-8",
    )

    tree = surface / "dossiers" / "projects" / "Aura"
    tree.mkdir(parents=True, exist_ok=True)
    (tree / "plan.md").write_text(
        "---\ntype: engineering\nbelongs: Aura\n---\n\n# 计划\n", encoding="utf-8"
    )

    board = home / "kanban.db"
    conn = sqlite3.connect(str(board))
    conn.executescript(
        "create table tasks (id text, assignee text, status text);"
        "create table task_runs (id text, profile text);"
        "create table kanban_notify_subs (id text, notifier_profile text);"
    )
    conn.execute("insert into tasks values ('t1','Aura','ready')")
    conn.execute("insert into task_runs values ('r1','Aura')")
    conn.execute("insert into kanban_notify_subs values ('n1','Aura')")
    conn.commit()
    conn.close()

    (home / "config.yaml").write_text(
        "kanban:\n  default_assignee: Aura\n  orchestrator_profile: Aura\n",
        encoding="utf-8",
    )
    return home


def _rows(db: Path, sql: str):
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _roster_names():
    return [a["name"] for a in profiles_mod.list_roster_agents()]


def test_rename_moves_every_identity_keyed_artifact(full_home):
    """判据 1：一次改名，十几处归属全跟着走。"""
    from plobi.agents.registry import shared_soul_path_for

    profiles_mod.rename_identity("Aura", "AuraPrime")

    assert "AuraPrime" in _roster_names() and "Aura" not in _roster_names()
    # 身份文件搬走**且内容按新名重渲染**（那句「你是 **X**」不许还写着旧名）
    assert not (shared_soul_path_for("Aura")).exists()
    soul = shared_soul_path_for("AuraPrime").read_text(encoding="utf-8")
    assert "AuraPrime" in soul and "你是 **Aura**。" not in soul
    # 它那一份排程
    assert (full_home / "cron" / "agents" / "AuraPrime" / "jobs.json").is_file()
    assert not (full_home / "cron" / "agents" / "Aura").exists()
    # 总账归属
    from plobi_state import get_plobi_state_home

    state_db = get_plobi_state_home() / "state.db"
    assert [r[0] for r in _rows(state_db, "select agent_id from sessions")] == [
        "AuraPrime",
        "agenda",
    ]
    # 看板三列（调度器认不出旧 assignee 会把卡静默停在 ready）
    board = full_home / "kanban.db"
    assert _rows(board, "select assignee, status from tasks") == [("AuraPrime", "ready")]
    assert _rows(board, "select profile from task_runs") == [("AuraPrime",)]
    assert _rows(board, "select notifier_profile from kanban_notify_subs") == [("AuraPrime",)]
    # 记忆那份目录（目录名按 fixture 里那一步算出来的字面 slug 断言，不再问解析器——
    # 解析器本身带前缀模糊匹配，改名以后拿旧名去问它会落到新名上）
    from tools.memory_tool import get_memory_dir

    assert (get_memory_dir() / "agents" / "auraprime" / "MEMORY.md").is_file()
    assert not (get_memory_dir() / "agents" / "aura").exists()
    # 信任账那一格换键（别人的那格一字不动）
    trust = json.loads((full_home / "l1-trust.json").read_text(encoding="utf-8"))
    assert trust == {"memory::AuraPrime": {"streak": 3}, "memory::agenda": {"streak": 1}}
    # 删除台账
    deletions = (get_memory_dir() / "deletions.jsonl").read_text(encoding="utf-8")
    assert '"agent": "AuraPrime"' in deletions and '"agent": "agenda"' in deletions
    # 外部会话索引
    ext = json.loads((full_home / "external_sessions" / "index.json").read_text(encoding="utf-8"))
    assert list(ext) == ["AuraPrime"]
    # 笔记的 belongs: 与 Mind 子树目录＋那一格
    note = (full_home.parent / "surface" / "dossiers" / "projects" / "AuraPrime" / "plan.md").read_text(
        encoding="utf-8"
    )
    assert "belongs: AuraPrime" in note
    from plobi.agents.registry import load_registry

    assert load_registry().agents["AuraPrime"].mind_subtree == "Surface/dossiers/projects/AuraPrime"
    # config 那两格
    config_text = (full_home / "config.yaml").read_text(encoding="utf-8")
    assert "default_assignee: AuraPrime" in config_text
    assert "orchestrator_profile: AuraPrime" in config_text


def test_old_spelling_still_finds_the_agent_after_rename(full_home, CH):
    """判据 3：改过名以后，旧写法照样找得到人（别名表只服务于解析，不参与存在性判据）。"""
    from plobi.agents.identity_aliases import resolve_identity_alias

    profiles_mod.rename_identity("Aura", "AuraPrime")

    assert resolve_identity_alias("Aura") == "AuraPrime"
    entry, err = CH.resolve_recipient("Aura")
    assert entry is not None and entry.name == "AuraPrime", err
    # 现名照旧认得
    assert CH.resolve_recipient("AuraPrime")[0] is not None


def test_dry_run_touches_nothing(full_home):
    """判据 2：dry-run 只给清单，一个盘都不碰。"""
    preview = profiles_mod.preview_identity_rename("Aura", "AuraPrime")

    assert preview["dryRun"] is True and preview["steps"]
    assert any("看板" in line or "tasks.assignee" in line for line in preview["steps"])
    assert "Aura" in _roster_names()
    assert (full_home / "cron" / "agents" / "Aura").is_dir()
    assert _rows(full_home / "kanban.db", "select assignee from tasks") == [("Aura",)]


def test_failure_midway_rolls_everything_back(full_home, monkeypatch):
    """判据 4：任何一步失败 ⇒ 已经动过的原路退回，名册不留半截。"""
    real = profiles_mod._apply_rename_step
    calls = {"n": 0}

    def flaky(step, info):
        calls["n"] += 1
        if step.get("kind") == "json-rekey":
            raise OSError("模拟：写外部会话索引时炸了")
        return real(step, info)

    monkeypatch.setattr(profiles_mod, "_apply_rename_step", flaky)

    with pytest.raises(RuntimeError):
        profiles_mod.rename_identity("Aura", "AuraPrime")

    assert "Aura" in _roster_names() and "AuraPrime" not in _roster_names()
    assert (full_home / "cron" / "agents" / "Aura").is_dir()
    assert not (full_home / "cron" / "agents" / "AuraPrime").exists()
    assert _rows(full_home / "kanban.db", "select assignee from tasks") == [("Aura",)]
    from plobi_state import get_plobi_state_home

    state_db = get_plobi_state_home() / "state.db"
    assert "Aura" in [r[0] for r in _rows(state_db, "select agent_id from sessions")]
    assert (full_home.parent / "surface" / "dossiers" / "projects" / "Aura" / "plan.md").is_file()


def test_rename_refuses_while_a_card_is_running(full_home):
    """判据 5：那个人身上还有卡在跑 ⇒ 不改（改了那一趟落不到人）。"""
    conn = sqlite3.connect(str(full_home / "kanban.db"))
    conn.execute("update tasks set status='running' where assignee='Aura'")
    conn.commit()
    conn.close()

    with pytest.raises(ValueError, match="在跑"):
        profiles_mod.rename_identity("Aura", "AuraPrime")

    assert "Aura" in _roster_names()


def test_rename_refuses_reserved_names_and_system_rows(full_home):
    """判据 6：形状约束沿用名册校验；系统自带那两行不改名。"""
    with pytest.raises(ValueError):
        profiles_mod.rename_identity("Aura", "  ")
    with pytest.raises(ValueError):
        profiles_mod.rename_identity("Aura", "default")
    with pytest.raises(ValueError):
        profiles_mod.rename_identity("agenda", "Butler")
    with pytest.raises(FileExistsError):
        profiles_mod.rename_identity("Aura", "aura")


def test_rename_leaves_a_journal(full_home):
    """判据 7：留一份底，记这次动了哪几处。"""
    profiles_mod.rename_identity("Aura", "AuraPrime")

    entries = sorted((full_home / "renames").glob("*.json"))
    assert entries, "改名没留 journal"
    record = json.loads(entries[-1].read_text(encoding="utf-8"))
    assert record["old"] == "Aura" and record["new"] == "AuraPrime"
    assert len(record["steps"]) >= 8


def test_the_rename_endpoint_shows_the_plan_before_it_touches_anything(full_home):
    """B 段：桌面点「改名…」先要清单（dryRun），看过确认才真改。"""
    from plobi.console.router import rename_agent

    preview = rename_agent("Aura", {"dryRun": True, "newName": "AuraPrime"})

    assert preview["ok"] is True
    assert preview["data"]["dryRun"] is True and preview["data"]["steps"]
    assert "Aura" in _roster_names(), "dry-run 不该动名册"

    done = rename_agent("Aura", {"newName": "AuraPrime"})

    assert done["ok"] is True and done["data"]["id"] == "AuraPrime"
    assert "AuraPrime" in _roster_names()

    # 旧家名那一格仍是这一行的别名（裁 192：家名不动，旧写法照样找得到同一个人）⇒
    # 用「aura」喊它，改的还是那一条行，不会另起一个人。
    again = rename_agent("aura", {"newName": "AuraAgain"})

    assert again["ok"] is True and again["data"]["id"] == "AuraAgain"
    assert "AuraAgain" in _roster_names() and "AuraPrime" not in _roster_names()

    # 真空名字与查无此人：当场拒，不许留下半个改动。
    empty = rename_agent("AuraAgain", {"newName": "  "})
    assert getattr(empty, "status_code", 200) == 400
    missing = rename_agent("nobody-here", {"newName": "Whatever"})
    assert getattr(missing, "status_code", 200) == 404
    assert _roster_names() == ["AuraAgain", "agenda"]
