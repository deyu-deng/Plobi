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
from tools import human_diary_tools, human_dossier_tools, human_note_tools


# ── 人读面那 17 把现在是注册表工具（WP-HUMAN-REGISTRY-TOOLS，11-14）──────────
#
# provider 只管脑页了 ⇒ 人读面的工具在 ``tools/human_*_tools.py``，
# 脑页那两把还在 provider。这一只口把两边的调用收在一处 ⇒ 断言一个字不动。

from tools import human_diary_tools, human_dossier_tools, human_note_tools

_HUMAN_TOOL_MODULES = (human_note_tools, human_diary_tools, human_dossier_tools)


def _call(name, args):
    """人读面的工具走新模块；脑页那两把还走 provider。"""
    for module in _HUMAN_TOOL_MODULES:
        fn = getattr(module, name, None)
        if fn is not None:
            return fn(args)
    provider = MindProvider()
    provider.initialize("sess-registry-tools", agent_context="primary")
    return provider.handle_tool_call(name, args)


@pytest.fixture()
def roots(tmp_path, monkeypatch):
    mind = tmp_path / "mind"
    human = tmp_path / "human"
    (mind / "wiki").mkdir(parents=True)
    (human / "diary").mkdir(parents=True)
    (human / "dossiers" / "projects" / "Aura").mkdir(parents=True)
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("HUMAN_ROOT", str(human))
    # **当前是谁**：注册表工具在**调用时**问身份（``PLOBI_AGENT_ID`` 优先）
    # ⇒ 这几把的会话身份就在这根环境变量里（provider 原来是 init 时问一次）。
    monkeypatch.setenv("PLOBI_AGENT_ID", "Aura")

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
            role="principal",
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

    result = json.loads(_call("diary_list", {}))
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
        _call(
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

    empty = json.loads(_call("diary_append", {"line": "  "}))
    assert empty["success"] is False

    multi = json.loads(
        _call("diary_append", {"line": "第一行\n第二行"})
    )
    assert multi["success"] is False
    assert "一行" in multi["error"]


def test_diary_append_refuses_a_day_it_cannot_read(roots):
    _mind, _human, provider = roots

    result = json.loads(
        _call("diary_append", {"day": "13/11/2026", "line": "x"})
    )
    assert result["success"] is False


# ── ③ dossier_list ─────────────────────────────────────────────────────────


def test_dossier_list_gives_one_row_per_agent(roots):
    _mind, _human, provider = roots

    result = json.loads(_call("dossier_list", {}))
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
        _call(
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
        _call(
            "dossier_task_toggle",
            {"agent": "Aura", "date": "2026-11-13", "id": "t9", "done": True},
        )
    )
    assert unknown["success"] is False

    missing = json.loads(
        _call(
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
        _call(
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
    assert stages["s2"] == {
        "id": "s2",
        "title": "再打磨",
        "detail": "",
        "done": False,
        # 落盘只写真有的东西：kind／due 空着就不出现，别补成空串污染档案。
    }
    assert stages["s1"]["done"] is True


def test_dossier_stage_update_can_mark_a_stage_done(roots):
    _mind, human, provider = roots

    result = json.loads(
        _call(
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
        _call(
            "dossier_stage_update",
            {"agent": "Aura", "stage_id": "s9", "done": True},
        )
    )
    assert result["success"] is False
    assert "s9" in result["error"]


# ── 门类与工具表 ──────────────────────────────────────────────────────────


def test_the_new_tools_belong_to_their_own_category():
    """它们是**注册表工具** ⇒ 门类归注册表（provider 那张表只剩脑页两把）。"""
    from tools.registry import registry

    for name in ("diary_list", "diary_append"):
        assert registry.get_toolset_for_tool(name) == "diary", name
    for name in (
        "dossier_list",
        "dossier_task_toggle",
        "dossier_stage_update",
    ):
        assert registry.get_toolset_for_tool(name) == "dossier", name


def test_the_new_tools_reach_the_agent_tool_table():
    """下发路换了：注册表工具走 ``model_tools.get_tool_definitions``（门类开着就在）。"""
    from model_tools import get_tool_definitions

    names = {
        t["function"]["name"]
        for t in get_tool_definitions(
            enabled_toolsets=["diary", "dossier"], quiet_mode=True
        )
    }
    assert {
        "diary_list",
        "diary_append",
        "dossier_list",
        "dossier_task_toggle",
        "dossier_stage_update",
    } <= names, names
    # 门类关掉 ⇒ 工具表里就没有它们（Tools 页那一格仍然管用）。
    off = {
        t["function"]["name"]
        for t in get_tool_definitions(enabled_toolsets=["todo"], quiet_mode=True)
    }
    assert not {"diary_list", "diary_append", "dossier_list"} & off, off


def test_the_new_tools_are_atomic_no_branch_parameters():
    """裁定 185：不许用 action/mode 这类参数把两件事塞进一枚工具。"""
    from tools.registry import registry

    schemas = {}
    for name in (
        "diary_list",
        "diary_append",
        "dossier_list",
        "dossier_task_toggle",
        "dossier_stage_update",
    ):
        schema = registry.get_schema(name)
        assert schema is not None, name
        properties = set(schema["parameters"]["properties"])
        assert not {"action", "mode", "surface", "intent"} & properties, name
