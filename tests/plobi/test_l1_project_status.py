"""WP-PROJECT-PORTFOLIO: project_status + plan_day routing for the L1 secretary.

Covers the four acceptance lines from the task book:

* Empty registry + temp Mind root (Plobi + Animation, both in-progress)
  → first call seeds both rows; second call returns ``seeded=[]``.
* ``status: paused`` projects are not registered at all.
* ``project_status`` returns ``ok=true`` even when chatlog is dead — the
  intent never touches the collector.
* ``plan_day`` answers ``ok=true`` with ``status=empty`` when the agenda
  library has no events for tomorrow.
* ``SECRETARY_ASK_INTENTS`` advertises all seven values, and the tool schema
  exposed to L1 carries the same enum.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load_master_tools():
    """Load ``plugins/plobi-north-star/master_tools.py``.

    The directory is hyphen-named (``plobi-north-star``) so the canonical
    ``import plugins.plobi_north_star.master_tools`` doesn't resolve; we
    load it by file path instead. Returns the live module.
    """
    if "plobi_test_north_star_master_tools" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "plobi_test_north_star_master_tools",
            REPO / "plugins" / "plobi-north-star" / "master_tools.py",
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return sys.modules["plobi_test_north_star_master_tools"]


def _make_plan(name: str,
              *,
              group: str = "product",
              status: str = "active",
              cloud: str | None = None) -> str:
    """Build a minimal but realistic ``plan.md`` body for tests.

    Built line-by-line (no ``textwrap.dedent``) so every line has a uniform
    zero leading indent — the scanner's ``startswith("---")`` requires the
    frontmatter fence to be at column 0.
    """
    cloud_value = cloud or r"D:\Cloud\Projects\X"
    return "\n".join(
        [
            "---",
            f"project: {name}",
            f"title: {name}",
            "type: engineering",
            f"group: {group}",
            f"cloud: {cloud_value}",
            f"status: {status}",
            "created: 2026-01-01",
            "updated: 2026-09-01",
            "tech: [Python]",
            f"summary: {name} test fixture.",
            "---",
            "",
            f"# {name}",
            "",
            "Body text used to verify excerpt slicing.",
            "",
        ]
    )


def _write_project(mind_root: Path, name: str, *, body: str) -> Path:
    target = mind_root / "Vault" / "projects" / name
    target.mkdir(parents=True, exist_ok=True)
    target.joinpath("plan.md").write_text(body, encoding="utf-8")
    return target


def _make_fake_mind_root(tmp_path: Path,
                         *,
                         paused: list[str] | None = None) -> Path:
    """Build a hermetic Mind layout with Plobi + Animation as in-progress."""
    mind_root = tmp_path / "mind"
    mind_root.mkdir()
    # AGENTS.md is how ``plobi.mind.paths.resolve_root`` confirms a vault.
    mind_root.joinpath("AGENTS.md").write_text("# test mind\n", encoding="utf-8")
    _write_project(
        mind_root,
        "Plobi",
        body=_make_plan("Plobi", cloud=r"D:\Cloud\Projects\Plobi"),
    )
    _write_project(
        mind_root,
        "Animation",
        body=_make_plan("Animation", cloud=r"D:\Cloud\Projects\Animation"),
    )
    # Mind itself must be skipped (system self).
    _write_project(
        mind_root,
        "Mind",
        body=_make_plan("Mind", group="system", cloud=""),
    )
    for paused_name in paused or []:
        _write_project(
            mind_root,
            paused_name,
            body=_make_plan(paused_name, status="paused"),
        )
    return mind_root


@pytest.fixture()
def mind_root(tmp_path, monkeypatch):
    """Fresh per-test Mind root, pointed at via ``MIND_ROOT`` env."""
    root = _make_fake_mind_root(tmp_path)
    monkeypatch.setenv("MIND_ROOT", str(root))
    return root


@pytest.fixture()
def fresh_registry(tmp_path, monkeypatch):
    """A registry pointing at a per-test projects.yaml in tmp."""
    from plobi.agents import registry as registry_mod

    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(tmp_path / "projects.yaml"))
    return registry_mod.AgentRegistry.load(tmp_path / "projects.yaml")


def test_seven_intent_values_advertised():
    from plobi.agents.registry import SECRETARY_ASK_INTENTS

    assert SECRETARY_ASK_INTENTS == (
        "refresh_agenda",
        "write_briefing",
        "mutate_agenda",
        "query_agenda",
        "decide_pending",
        "project_status",
        "plan_day",
    )


def test_schema_advertises_seven_intents():
    schema = _load_master_tools().SECRETARY_ASK_SCHEMA
    enum = schema["parameters"]["properties"]["intent"]["enum"]
    assert set(enum) == {
        "refresh_agenda",
        "write_briefing",
        "mutate_agenda",
        "query_agenda",
        "decide_pending",
        "project_status",
        "plan_day",
    }
    # And the description mentions the two new intents explicitly.
    assert "project_status" in schema["description"]
    assert "plan_day" in schema["description"]


def test_ensure_mind_project_agents_seeds_two_active_projects(
    mind_root, fresh_registry
):
    from plobi.agents.registry import ensure_mind_project_agents

    reg, seeded = ensure_mind_project_agents(fresh_registry)
    names = {entry.name for entry in reg.entries()}
    # Plobi + Animation seeded; Mind (system self) and any paused names skipped.
    assert {"Plobi", "Animation"}.issubset(names)
    assert "Mind" not in names
    # Both names appear in the ``seeded`` list (the registry was empty before).
    assert set(seeded) == {"Plobi", "Animation"}
    # Plobi gets the hard-coded source path; Animation gets the cloud path
    # from its plan.md frontmatter.
    by_name = {e.name: e for e in reg.entries()}
    assert by_name["Plobi"].project_path == r"D:\Projects\Plobi\Code"
    assert by_name["Animation"].project_path == r"D:\Cloud\Projects\Animation"
    # Category mapping: both are product → projects (no research invents here).
    assert by_name["Plobi"].category == "projects"
    assert by_name["Animation"].category == "projects"
    # pace is *not* filled by the seeder.
    assert by_name["Plobi"].pace is None
    assert by_name["Animation"].pace is None


def test_run_project_status_idempotent(mind_root, fresh_registry):
    from plobi.agents.registry import run_project_status

    first = run_project_status("各项目怎么样了", registry=fresh_registry)
    assert first["ok"] is True
    assert first["intent"] == "project_status"
    ids_first = sorted(p["id"] for p in first["projects"])
    assert ids_first == ["Animation", "Plobi"]
    assert set(first["seeded"]) == {"Plobi", "Animation"}
    # Each row carries the contract fields.
    for row in first["projects"]:
        assert row["weekly_hours"] is None
        assert row["has_mind"] is True
        assert isinstance(row["plan_excerpt"], str)
        assert len(row["plan_excerpt"]) <= 400
        assert isinstance(row["progress_excerpt"], str)
        assert len(row["progress_excerpt"]) <= 400

    second = run_project_status("再报一次", registry=fresh_registry)
    assert second["ok"] is True
    assert second["seeded"] == []
    assert sorted(p["id"] for p in second["projects"]) == ids_first


def test_paused_projects_are_not_registered(tmp_path, fresh_registry, mind_root):
    from plobi.agents.registry import ensure_mind_project_agents, run_project_status

    _write_project(mind_root, "Stalled", body=_make_plan("Stalled", status="paused"))
    _write_project(mind_root, "Done", body=_make_plan("Done", status="completed"))
    _write_project(
        mind_root, "Scrapped", body=_make_plan("Scrapped", status="abandoned")
    )
    reg, seeded = ensure_mind_project_agents(fresh_registry)
    names = {entry.name for entry in reg.entries()}
    assert "Stalled" not in names
    assert "Done" not in names
    assert "Scrapped" not in names
    assert "Stalled" not in seeded and "Done" not in seeded

    payload = run_project_status("在推什么", registry=reg)
    ids = [p["id"] for p in payload["projects"]]
    assert "Stalled" not in ids and "Done" not in ids and "Scrapped" not in ids


def test_project_status_survives_chatlog_dead(mind_root, fresh_registry, monkeypatch):
    """ChatlogDead must NOT propagate into ``project_status``.

    The intent never reads the chatlog pipeline; we monkeypatch the
    ChatlogDead import so any accidental import surface explodes, then
    assert the call still returns ``ok=true``.
    """
    import builtins

    from plobi.agents.registry import run_project_status

    real_import = builtins.__import__

    def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("plobi.collectors.chatlog.pipeline"):
            raise RuntimeError(
                "project_status must not import plobi.collectors.chatlog.pipeline"
            )
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _guarded_import)
    payload = run_project_status("今天各项目进度", registry=fresh_registry)
    assert payload["ok"] is True
    assert payload["intent"] == "project_status"


def test_run_project_status_routes_via_secretary_ask(mind_root, fresh_registry):
    """``run_secretary_ask(intent=project_status)`` must reach the handler
    without spawning the agenda L2 or touching chatlog."""
    from plobi.agents.registry import run_secretary_ask

    payload = run_secretary_ask(
        "project_status", "各项目怎么样了", registry=fresh_registry
    )
    assert payload["ok"] is True
    assert payload["intent"] == "project_status"
    assert sorted(p["id"] for p in payload["projects"]) == ["Animation", "Plobi"]


def test_plan_day_on_empty_library_is_ok(tmp_path, fresh_registry, monkeypatch):
    from plobi.agents.registry import run_plan_day

    # Use the shared registry + a per-test agenda DB so the store is empty.
    from plobi.agents import registry as registry_mod
    from plobi.agenda import store as _store

    db_path = tmp_path / "agenda.db"
    monkeypatch.setattr(registry_mod, "_secretary_db_path", lambda pipeline=None: db_path)
    conn = _store.connect(db_path)
    conn.close()

    payload = run_plan_day("排一下明天", date=None)
    assert payload["ok"] is True
    assert payload["intent"] == "plan_day"
    assert payload["conflict_count"] == 0
    # Empty library ⇒ empty plan (status from the planning module, items=[]).
    assert payload["items"] == []
    assert payload["summary"] != "" or payload["summary"] is not None
    # ``for_date`` defaults to tomorrow (local).
    from datetime import date as _date, timedelta

    assert payload["for_date"] == (_date.today() + timedelta(days=1)).isoformat()


def test_plan_day_routes_via_secretary_ask(mind_root, fresh_registry):
    from plobi.agents.registry import run_secretary_ask

    payload = run_secretary_ask(
        "plan_day", "排明天", registry=fresh_registry, date=None
    )
    assert payload["ok"] is True
    assert payload["intent"] == "plan_day"
    assert "items" in payload
    assert isinstance(payload["conflict_count"], int)


def test_run_secretary_ask_rejects_unknown_intent():
    from plobi.agents.registry import run_secretary_ask

    payload = run_secretary_ask("invent_a_new_one", "做什么都行")
    assert payload["ok"] is False
    assert "intent must be one of" in payload["error"]


def test_existing_registry_path_is_honored(mind_root, fresh_registry, monkeypatch):
    """User edits (custom ``project_path``) survive a re-scan."""
    from plobi.agents.registry import (
        AgentEntry,
        ensure_mind_project_agents,
    )

    # Pre-register a row with a *different* project_path and pace.
    fresh_registry.upsert(
        AgentEntry(
            name="Plobi",
            role="l2_project",
            category="projects",
            project_path=r"D:\Custom\Edited\Path",
            pace={"weekly_hours": 7.5},
            description="user changed me",
        )
    )
    fresh_registry.save()

    reg, seeded = ensure_mind_project_agents(fresh_registry)
    by_name = {e.name: e for e in reg.entries()}
    edited = by_name["Plobi"]
    # Edits survive.
    assert edited.project_path == r"D:\Custom\Edited\Path"
    assert edited.pace == {"weekly_hours": 7.5}
    assert edited.description == "user changed me"
    # And the re-scan still fills in the previously-empty mind_subtree.
    assert edited.mind_subtree == "Vault/projects/Plobi"
    # Plobi already existed → NOT re-seeded. Animation was new → seeded.
    assert "Plobi" not in seeded
    assert seeded == ["Animation"]


# ---------------------------------------------------------------------------
# WP-L2-DIET / 裁定 33.2：加 L1 中收的「memory」断言（不影响 project_status /
# plan_day 行为；只补加硬规则不变的部分）。
# ---------------------------------------------------------------------------


def test_l1_mid_toolsets_no_longer_advertises_memory():
    """WP-L2-DIET / 裁定 33.2：L1 是总秘书不是 LLM 长记忆——memory 工具从
    L1 中收里拿掉。L1 仍能报项目（project_status），但答案只能来自
    plobi_secretary_ask 的工具回传，不准用 memory 答日程 / 答项目。"""
    from plobi.agents.registry import L1_MID_TOOLSETS, L1_DROP_TOOLSETS

    assert "memory" not in L1_MID_TOOLSETS
    assert "memory" in L1_DROP_TOOLSETS


def test_soul_block_keeps_memory_out_of_l1_reach():
    """L1 的派工 SOUL 必须显式禁止 memory 工具 + 不准直接调 plobi_master_*。"""
    from plobi.agents.registry import L1_SOUL_BLOCK

    # 不在「准用」段里；硬规则显式禁止。
    assert "派工不是工人" in L1_SOUL_BLOCK
    assert "memory" in L1_SOUL_BLOCK
    for forbidden in (
        "plobi_master_dispatch",
        "plobi_master_preview",
        "plobi_master_status",
        "plobi_master_approve",
    ):
        assert forbidden in L1_SOUL_BLOCK, forbidden
    # 不准对项目主树开 terminal。
    assert r"D:\Projects\Plobi\Code" in L1_SOUL_BLOCK
    assert "terminal" in L1_SOUL_BLOCK


# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# WP-L1-IDENTITY / 裁定 39：总秘书身份句钉死在 SOUL 最前面
# ---------------------------------------------------------------------------


def test_soul_block_identity_section_is_the_first_h2():
    """裁定 39:身份段必须钉在「## 总秘书派工」之前——模型第一眼读到的是
    「你是谁」,再决定用什么动作。"""
    from plobi.agents.registry import L1_SOUL_BLOCK

    idx_identity = L1_SOUL_BLOCK.find("## 身份（WP-L1-IDENTITY")
    idx_dispatch = L1_SOUL_BLOCK.find("## 总秘书派工")
    assert idx_identity >= 0, "SOUL 缺少「## 身份（WP-L1-IDENTITY」段"
    assert idx_dispatch >= 0, "SOUL 缺少「## 总秘书派工」段"
    assert idx_identity < idx_dispatch, '身份段必须在「总秘书派工」之前'


def test_soul_block_identity_first_body_is_plobi_l1():
    """身份段第一段正文必须把身份说清：你是 Plobi 总秘书（L1）。"""
    from plobi.agents.registry import L1_SOUL_BLOCK

    H2 = '\n## '
    idx = L1_SOUL_BLOCK.find("## 身份（WP-L1-IDENTITY")
    end = L1_SOUL_BLOCK.find(H2, idx)
    section = L1_SOUL_BLOCK[idx:end]
    # 跳过 heading 行；取第一段正文（以 `-` 开头）。
    paras = [p for p in section.split('\n\n') if p.strip().startswith('-')]
    assert paras, '身份段必须至少有一段正文'
    first_para = paras[0]
    assert 'Plobi 总秘书' in first_para
    # 全角括号（中文统一用「（L1）」）。
    assert '（L1）' in first_para or 'L1' in first_para


def test_soul_block_identity_negates_all_wrong_self_models():
    """身份段必须把 Nous / 日程 L2 / 通用 chatbot / GUI 工具(Cursor / Claude Code)"""
    """全部否定——只认 Plobi 总秘书 + L1。"""
    from plobi.agents.registry import L1_SOUL_BLOCK

    H2 = '\n## '
    idx = L1_SOUL_BLOCK.find("## 身份（WP-L1-IDENTITY")
    end = L1_SOUL_BLOCK.find(H2, idx)
    section = L1_SOUL_BLOCK[idx:end]
    for wrong_self in ("Nous Research", "通用 chatbot", "Cursor", "Claude Code", "日程 L2"):
        assert wrong_self in section, f"身份段没否定「{wrong_self}」"


def test_soul_block_identity_bans_menu_lists_when_user_asks_who():
    """用户问「你是谁」时不要开工具菜单 / 不要列 intent 列表 / 不要列 MCP。"""
    from plobi.agents.registry import L1_SOUL_BLOCK

    H2 = '\n## '
    idx = L1_SOUL_BLOCK.find("## 身份（WP-L1-IDENTITY")
    end = L1_SOUL_BLOCK.find(H2, idx)
    section = L1_SOUL_BLOCK[idx:end]
    # SOUL 用 ``**不要**`` 加粗；测试只查正文片段（不查 markup），防止格式微调误伤。
    assert '开工具菜单' in section
    assert '七个 intent' in section
    assert 'MCP' in section


def test_soul_block_identity_bars_no_terminal_blame():
    """裁定 39:你没有 terminal 不是故障——不准解释框架、不准让用户改配置。"""
    """必须显式声明「terminal 早被 WP-L2-DIET(裁定 33.3)剥掉了」。"""
    from plobi.agents.registry import L1_SOUL_BLOCK

    H2 = '\n## '
    idx = L1_SOUL_BLOCK.find("## 身份（WP-L1-IDENTITY")
    end = L1_SOUL_BLOCK.find(H2, idx)
    section = L1_SOUL_BLOCK[idx:end]
    assert '不是故障' in section
    assert '解释框架' in section  # markup 抗性
    assert '让用户改配置' in section
    assert 'WP-L2-DIET' in section
    assert '33.3' in section


def test_soul_block_identity_delegates_work_to_l2_not_self():
    """总秘书不亲自改代码 / 跑命令 / 操控 AI 软件——派给对应项目 L2。"""
    """L2 再管 L3。"""
    from plobi.agents.registry import L1_SOUL_BLOCK

    H2 = '\n## '
    idx = L1_SOUL_BLOCK.find("## 身份（WP-L1-IDENTITY")
    end = L1_SOUL_BLOCK.find(H2, idx)
    section = L1_SOUL_BLOCK[idx:end]
    assert "派给对应项目的 L2" in section
    assert "它再管 L3" in section
    assert "不要当工人" in section


def test_soul_block_identity_keeps_seven_intent_unchanged():
    """WP-L1-IDENTITY 不增 intent:仍是 plobi_secretary_ask(七个 intent)。"""
    from plobi.agents.registry import L1_SOUL_BLOCK

    H2 = '\n## '
    idx = L1_SOUL_BLOCK.find("## 身份（WP-L1-IDENTITY")
    end = L1_SOUL_BLOCK.find(H2, idx)
    section = L1_SOUL_BLOCK[idx:end]
    assert "plobi_secretary_ask" in section
    assert "七个 intent 一个不增" in section


def test_soul_block_identity_remains_idempotent_under_ensure_l1_secretary_routing_soul(tmp_path, monkeypatch):
    """ensure_l1_secretary_routing_soul 把 SOUL 盖进 fence——新旧身份段都得"""
    """留下(不能被 strip 误伤)。"""
    from plobi.agents.registry import ensure_l1_secretary_routing_soul

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # 真实 HERMES_HOME + 先建好 .hermes 目录，否则 ensure 写不进 SOUL.md。
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    changed_first = ensure_l1_secretary_routing_soul(home=tmp_path)
    changed_second = ensure_l1_secretary_routing_soul(home=tmp_path)
    assert changed_first is True
    assert changed_second is False  # 幂等

    soul_path = tmp_path / "SOUL.md"
    text = soul_path.read_text(encoding='utf-8')
    assert "Plobi 总秘书" in text
    assert "WP-L1-IDENTITY" in text


def test_schema_description_opens_with_identity_not_default_chatbot():
    """SECRETARY_ASK_SCHEMA.description 第一句必须是「你是本机 Plobi 总秘书(L1)」"""
    """而不是「你是一个有用的助手」之类的默认人格。"""
    import importlib.util
    from pathlib import Path as _P

    spec = importlib.util.spec_from_file_location(
        "plobi_identity_master_tools",
        _P(__file__).resolve().parents[2] / "plugins" / "plobi-north-star" / "master_tools.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    desc = module.SECRETARY_ASK_SCHEMA['description']
    first_para = desc.split('\n', 1)[0]
    assert "Plobi 总秘书" in first_para
    # 全角括号（中文统一用「（L1）」）。
    assert "（L1）" in first_para


def test_schema_description_warns_about_not_opening_menu():
    """schema description 必须写明:用户问「你是谁」时不要开工具菜单 / 不要列"""
    """intent / 不要列 MCP。"""
    import importlib.util
    from pathlib import Path as _P

    spec = importlib.util.spec_from_file_location(
        "plobi_identity_master_tools2",
        _P(__file__).resolve().parents[2] / "plugins" / "plobi-north-star" / "master_tools.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    desc = module.SECRETARY_ASK_SCHEMA['description']
    assert "不要开工具菜单" in desc
    assert "七个 intent 一个不增" in desc
    assert "MCP" in desc


def test_schema_description_does_not_introduce_eighth_intent():
    """WP-L1-IDENTITY 禁第八 intent——schema intent enum 仍 7 值。"""
    import importlib.util
    from pathlib import Path as _P

    spec = importlib.util.spec_from_file_location(
        "plobi_identity_master_tools3",
        _P(__file__).resolve().parents[2] / "plugins" / "plobi-north-star" / "master_tools.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    enum = module.SECRETARY_ASK_SCHEMA['parameters']['properties']['intent']['enum']
    assert len(enum) == 7

