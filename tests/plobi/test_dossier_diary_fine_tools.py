"""日记与档案那两族的细动作：列／接一行／勾一条／改一个阶段。

用户 11-14：「**dossier，diary 还有的工具也太少太基础了。你看看能不能扩充一下呢？
毕竟 dossier 里面还有很多情况呢。**」

这两族原来各只有两把（读 ＋ 整份写）。**每一把新工具都有产品侧现成的实现**
（屏上那一侧的同一份真源）——这一档只钉「工具面上有没有那一把」，不改产品行为：

  ① ``diary_list`` —— 日记有哪几天（原来只能按路径读一篇，而日记的路径是日期，
     Agent 根本猜不出来）；
  ② ``diary_append`` —— 往某天末尾接一行 ``- HH:MM …``（原来只能整篇覆写：要把
     今天的全文读出来再拼上再写回去）；
  ③ ``dossier_list`` —— 名册里每一行 ＋ 它那份档案的摘要；
  ④ ``dossier_task_toggle`` —— 勾掉/取消勾掉当日清单里的**一条**（原来只能整份覆写
     ``tasks.json``，而屏上那一条是**勾一下**；文件里记着谁勾的）；
  ⑤ ``dossier_stage_update`` —— 改**一个**阶段（``dossier_write`` 的 stages 是
     **整组替换**：不传的那几条等于被删了）。
"""

from __future__ import annotations

import json

import pytest

from plobi.mind import provider as provider_module
from plobi.mind import writer as writer_module
from plobi.mind.provider import MindProvider


@pytest.fixture()
def roots(tmp_path, monkeypatch):
    mind = tmp_path / "mind"
    human = tmp_path / "human"
    (mind / "wiki").mkdir(parents=True)
    (human / "diary").mkdir(parents=True)
    (human / "dossiers" / "projects" / "Aura").mkdir(parents=True)
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("HUMAN_ROOT", str(human))

    (human / "diary" / "2026-11-13-Friday.md").write_text(
        "- 09:00 早上那件事\n", encoding="utf-8"
    )
    (human / "diary" / "2026-11-12-Thursday.md").write_text(
        "- 10:00 昨天那件事\n", encoding="utf-8"
    )

    (human / "dossiers" / "projects" / "Aura" / "dossier.json").write_text(
        json.dumps(
            {
                "version": 1,
                "agent": "Aura",
                "title": "Aura 的项目",
                "category": "projects",
                "goal": "把情绪记录做成一个能用的应用。",
                "status": "上一句还停在九月。",
                "stages": [
                    {"id": "s1", "title": "先跑通", "detail": "", "done": True},
                    {"id": "s2", "title": "再打磨", "detail": "", "done": False},
                ],
                "updated": "2026-11-01",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (human / "dossiers" / "projects" / "Aura" / "tasks.json").write_text(
        json.dumps(
            {
                "days": [
                    {
                        "date": "2026-11-13",
                        "items": [
                            {"id": "t1", "title": "先做这个", "done": False},
                            {"id": "t2", "title": "再做那个", "done": True},
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # 名册上落一行（档案的家就是名册那一格 mind_subtree 指到的那一处）
    from plobi.agents.registry import AgentEntry, AgentRegistry
    from plobi_constants import get_plobi_home

    home = get_plobi_home()
    (home / "plobi").mkdir(parents=True, exist_ok=True)
    registry = AgentRegistry(home / "plobi" / "projects.yaml")
    registry.upsert(
        AgentEntry(
            name="Aura",
            role="l2_project",
            mind_subtree="Dossiers/projects/Aura",
        )
    )
    registry.save()

    provider = MindProvider()
    provider.initialize("sess-dossier-tools", agent_context="primary", agent_identity="Aura")
    return mind, human, provider


def _open_gate(monkeypatch):
    monkeypatch.setattr(
        writer_module.MindWriter, "_gate_refusals", lambda self, batch: []
    )
    monkeypatch.setattr(
        provider_module,
        "MindWriter",
        lambda root=None: writer_module.MindWriter(root, commit=False, run_verifier=False),
    )


# ── ① diary_list ───────────────────────────────────────────────────────────


def test_diary_list_gives_the_days_not_the_text(roots):
    _mind, _human, provider = roots

    result = json.loads(provider.handle_tool_call("diary_list", {}))
    assert result["success"] is True, result
    days = [row["date"] for row in result["entries"]]
    assert days == ["2026-11-13", "2026-11-12"], days
    # 正文一个字都不许漏进来
    assert "早上那件事" not in json.dumps(result, ensure_ascii=False)


# ── ② diary_append ────────────────────────────────────────────────────────


def test_diary_append_adds_one_line_and_keeps_the_rest(roots, monkeypatch):
    _mind, human, provider = roots
    _open_gate(monkeypatch)

    result = json.loads(
        provider.handle_tool_call(
            "diary_append", {"day": "2026-11-13", "line": "顺手记一句"}
        )
    )
    assert result["success"] is True, result
    assert result["line"].startswith("- ")
    assert "顺手记一句" in result["line"]

    text = (human / "diary" / "2026-11-13-Friday.md").read_text(encoding="utf-8")
    assert "早上那件事" in text, text
    assert "顺手记一句" in text, text
    assert text.count("\n") == 2, text


def test_diary_append_refuses_an_empty_or_multiline_line(roots):
    _mind, _human, provider = roots

    empty = json.loads(provider.handle_tool_call("diary_append", {"line": "  "}))
    assert empty["success"] is False

    multi = json.loads(
        provider.handle_tool_call("diary_append", {"line": "第一行\n第二行"})
    )
    assert multi["success"] is False
    assert "一行" in multi["error"]


def test_diary_append_refuses_a_day_it_cannot_read(roots):
    _mind, _human, provider = roots

    result = json.loads(
        provider.handle_tool_call("diary_append", {"day": "13/11/2026", "line": "x"})
    )
    assert result["success"] is False


# ── ③ dossier_list ─────────────────────────────────────────────────────────


def test_dossier_list_gives_one_row_per_agent(roots):
    _mind, _human, provider = roots

    result = json.loads(provider.handle_tool_call("dossier_list", {}))
    assert result["success"] is True, result
    row = next(a for a in result["agents"] if a["name"] == "Aura")
    assert row["goal"] == "把情绪记录做成一个能用的应用。"
    assert row["summary"] == "上一句还停在九月。"
    assert row["path"] == "Dossiers/projects/Aura/dossier.json"
    assert row["milestones"] == {"done": 1, "total": 2}
    assert row["profileExists"] is True


# ── ④ dossier_task_toggle ─────────────────────────────────────────────────


def test_dossier_task_toggle_ticks_one_item_and_records_who(roots):
    _mind, human, provider = roots

    result = json.loads(
        provider.handle_tool_call(
            "dossier_task_toggle",
            {"agent": "Aura", "date": "2026-11-13", "id": "t1", "done": True},
        )
    )
    assert result["success"] is True, result
    assert result["doneBy"] == "agent"

    raw = json.loads(
        (human / "dossiers" / "projects" / "Aura" / "tasks.json").read_text(
            encoding="utf-8"
        )
    )
    items = raw["days"][0]["items"]
    assert items[0]["done"] is True
    assert items[0]["doneBy"] == "agent"
    # 别的条目原样留着
    assert items[1]["done"] is True
    assert "doneBy" not in items[1]


def test_dossier_task_toggle_unticks_and_says_so_when_unknown(roots):
    _mind, human, provider = roots

    unknown = json.loads(
        provider.handle_tool_call(
            "dossier_task_toggle",
            {"agent": "Aura", "date": "2026-11-13", "id": "t9", "done": True},
        )
    )
    assert unknown["success"] is False

    missing = json.loads(
        provider.handle_tool_call(
            "dossier_task_toggle",
            {"agent": "Aura", "date": "2026-11-13", "id": "t1"},
        )
    )
    assert missing["success"] is False
    assert "done" in missing["error"]


# ── ⑤ dossier_stage_update ─────────────────────────────────────────────────


def test_dossier_stage_update_touches_one_stage_only(roots):
    _mind, human, provider = roots

    result = json.loads(
        provider.handle_tool_call(
            "dossier_stage_update",
            {"agent": "Aura", "stage_id": "s1", "title": "先跑通（改了）"},
        )
    )
    assert result["success"] is True, result

    raw = json.loads(
        (human / "dossiers" / "projects" / "Aura" / "dossier.json").read_text(
            encoding="utf-8"
        )
    )
    stages = {row["id"]: row for row in raw["stages"]}
    assert stages["s1"]["title"] == "先跑通（改了）"
    # 其余那条一个字没动
    assert stages["s2"] == {"id": "s2", "title": "再打磨", "detail": "", "done": False}
    assert stages["s1"]["done"] is True


def test_dossier_stage_update_can_mark_a_stage_done(roots):
    _mind, human, provider = roots

    result = json.loads(
        provider.handle_tool_call(
            "dossier_stage_update",
            {"agent": "Aura", "stage_id": "s2", "done": True},
        )
    )
    assert result["success"] is True, result
    assert result["stages"] == {"done": 2, "total": 2}

    raw = json.loads(
        (human / "dossiers" / "projects" / "Aura" / "dossier.json").read_text(
            encoding="utf-8"
        )
    )
    stages = {row["id"]: row for row in raw["stages"]}
    assert stages["s2"]["done"] is True


def test_dossier_stage_update_says_so_for_an_unknown_stage(roots):
    _mind, _human, provider = roots

    result = json.loads(
        provider.handle_tool_call(
            "dossier_stage_update",
            {"agent": "Aura", "stage_id": "s9", "done": True},
        )
    )
    assert result["success"] is False
    assert "s9" in result["error"]


# ── 门类与工具表 ──────────────────────────────────────────────────────────


def test_the_new_tools_belong_to_their_own_category():
    from agent.memory_manager import _PROVIDER_TOOL_TOOLSET

    for name in ("diary_list", "diary_append"):
        assert _PROVIDER_TOOL_TOOLSET.get(name) == "diary", name
    for name in (
        "dossier_list",
        "dossier_task_toggle",
        "dossier_stage_update",
    ):
        assert _PROVIDER_TOOL_TOOLSET.get(name) == "dossier", name


def test_the_new_tools_reach_the_agent_tool_table():
    from agent.memory_manager import inject_memory_provider_tools

    class _MM:
        def __init__(self):
            self._providers = [MindProvider()]

        def get_all_tool_schemas(self):
            out = []
            for p in self._providers:
                out.extend(p.get_tool_schemas())
            return out

    class _Agent:
        def __init__(self, enabled):
            self.enabled_toolsets = enabled
            self.tools = []
            self.valid_tool_names = set()
            self._memory_manager = _MM()

    agent = _Agent(["diary", "dossier"])
    inject_memory_provider_tools(agent)
    names = {t["function"]["name"] for t in agent.tools}
    assert {
        "diary_list",
        "diary_append",
        "dossier_list",
        "dossier_task_toggle",
        "dossier_stage_update",
    } <= names, names


def test_the_new_tools_are_atomic_no_branch_parameters():
    """裁定 185：不许用 action/mode 这类参数把两件事塞进一枚工具。"""
    provider = MindProvider()
    schemas = {s["name"]: s for s in provider.get_tool_schemas()}
    for name in (
        "diary_list",
        "diary_append",
        "dossier_list",
        "dossier_task_toggle",
        "dossier_stage_update",
    ):
        assert name in schemas, name
        properties = set(schemas[name]["parameters"]["properties"])
        assert not {"action", "mode", "surface", "intent"} & properties, name
