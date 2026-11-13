"""笔记那一族的三把细动作：``note_list`` / ``note_links`` / ``note_set_property``。

用户 11-13 22:1x：「**note-write 和 Agent 直接用 terminal 有什么区别呢？未免有些工具
太简陋了呢？**」——写侧原来只有「整篇覆写」这一把（改一个标签要重写全文），读侧只看
得见**自己名下**那一份笔记（公共知识那一块连清单都没有，而 ``note_read`` 是**按路径**
读的 ⇒ 没有清单就没有路径）。

这一档钉住：

  ① ``note_list``：列**整库**（含 ``belongs: []`` 的公共知识），**正文一个字不给**；
  ② ``note_links``：一篇的入链与出链（屏上 ``/api/surface/notes/links`` 那份真源）；
  ③ ``note_set_property``：只改 frontmatter 那几格（tags／belongs／relates），
     **正文一个字不动**；改标题不行——标题就是那篇的名字（文件名与入链都跟它一起动），
     那是 ``note_rename`` 的活；
  ④ 三把都归 ``note`` 门类，都能进 Agent 的工具表。
"""

from __future__ import annotations

import json
import textwrap

import pytest

from plobi.mind import provider as provider_module
from plobi.mind import writer as writer_module
from plobi.mind.provider import MindProvider


def _note(title: str, body: str, *, tags: str = "[物理]", belongs: str = "[Aura]") -> str:
    return (
        f"---\ntitle: {title}\ntags: {tags}\nbelongs: {belongs}\n---\n\n{body}\n"
    )


@pytest.fixture()
def roots(tmp_path, monkeypatch):
    mind = tmp_path / "mind"
    surface = tmp_path / "surface"
    (mind / "wiki").mkdir(parents=True)
    (surface / "notes" / "某主题").mkdir(parents=True)
    # 一篇属于 Aura，一篇是公共知识（belongs: []）——后者是「Agent 以前看不见」的那一类。
    (surface / "notes" / "某主题" / "某篇.md").write_text(
        _note("某篇", "# 某篇\n\n见 [[另一篇]]。"), encoding="utf-8"
    )
    (surface / "notes" / "某主题" / "另一篇.md").write_text(
        _note("另一篇", "# 另一篇\n\n公共知识。", belongs="[]"), encoding="utf-8"
    )
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("HUMAN_ROOT", str(surface))
    # 名册上落一行 Agent（「Aura」）——归属与校验都以它为准
    from plobi.agents.registry import AgentEntry, AgentRegistry
    from plobi_constants import get_plobi_home

    home = get_plobi_home()
    (home / "plobi").mkdir(parents=True, exist_ok=True)
    registry = AgentRegistry(home / "plobi" / "projects.yaml")
    registry.upsert(AgentEntry(name="Aura", role="l2_project"))
    registry.upsert(AgentEntry(name="SRTP", role="l2_project"))
    registry.save()
    provider = MindProvider()
    provider.initialize("sess-notes", agent_context="primary", agent_identity="Aura")
    return mind, surface, provider


def _open_gate(monkeypatch):
    """把写路径的闸开着（生产上它由用户决定）。"""
    monkeypatch.setattr(
        writer_module.MindWriter, "_gate_refusals", lambda self, batch: []
    )
    monkeypatch.setattr(
        provider_module,
        "MindWriter",
        lambda root=None: writer_module.MindWriter(root, commit=False, run_verifier=False),
    )


# ── ① note_list ────────────────────────────────────────────────────────────


def test_note_list_gives_the_whole_library_including_public_notes(roots):
    _mind, _surface, provider = roots

    result = json.loads(provider.handle_tool_call("note_list", {}))
    assert result["success"] is True, result
    titles = {n["title"] for n in result["notes"]}
    assert titles == {"某篇", "另一篇"}, result
    # 公共知识（belongs: []）也在清单里——以前 Agent 只拿得到自己名下那一份
    public = next(n for n in result["notes"] if n["title"] == "另一篇")
    assert public["belongs"] == []
    assert result["totals"]["library"] == 2


def test_note_list_never_gives_the_body(roots):
    """纪律同屏上：只给标题、逻辑路径、字数——**正文一个字不给**。"""
    _mind, _surface, provider = roots

    result = json.loads(provider.handle_tool_call("note_list", {}))
    blob = json.dumps(result, ensure_ascii=False)
    assert "正文" not in blob and "公共知识。" not in blob
    for note in result["notes"]:
        assert set(note) == {"path", "title", "chars", "belongs"}


def test_note_list_can_narrow_to_one_agent(roots):
    _mind, _surface, provider = roots

    result = json.loads(provider.handle_tool_call("note_list", {"agent": "Aura"}))
    assert result["success"] is True, result
    assert {n["title"] for n in result["notes"]} == {"某篇"}


def test_note_list_rejects_an_agent_the_roster_does_not_know(roots):
    _mind, _surface, provider = roots

    result = json.loads(provider.handle_tool_call("note_list", {"agent": "查无此人"}))
    assert result["success"] is False
    assert "名册" in result["error"]


# ── ② note_links ───────────────────────────────────────────────────────────


def test_note_links_reports_outgoing_and_backlinks(roots):
    _mind, _surface, provider = roots

    outgoing = json.loads(
        provider.handle_tool_call("note_links", {"path": "Notes/某主题/某篇.md"})
    )
    assert outgoing["success"] is True, outgoing
    assert outgoing["outgoing"] == ["Notes/某主题/另一篇.md"], outgoing
    assert outgoing["backlinks"] == []

    backlinks = json.loads(
        provider.handle_tool_call("note_links", {"path": "Notes/某主题/另一篇.md"})
    )
    assert backlinks["success"] is True, backlinks
    assert backlinks["backlinks"] == ["Notes/某主题/某篇.md"], backlinks


def test_note_links_says_so_when_the_note_does_not_exist(roots):
    """查不到的那一篇要**说清楚**，不能回一句「没有链接」。"""
    _mind, _surface, provider = roots

    result = json.loads(
        provider.handle_tool_call("note_links", {"path": "Notes/某主题/查无此篇.md"})
    )
    assert result["success"] is False
    assert "还不存在" in result["error"]


# ── ③ note_set_property ───────────────────────────────────────────────────


def test_note_set_property_changes_the_metadata_and_leaves_the_body_alone(
    roots, monkeypatch
):
    _mind, surface, provider = roots
    _open_gate(monkeypatch)
    before = (surface / "notes" / "某主题" / "某篇.md").read_text(encoding="utf-8")

    result = json.loads(
        provider.handle_tool_call(
            "note_set_property",
            {
                "path": "Notes/某主题/某篇.md",
                "tags": ["化学", "实验"],
                "belongs": ["Aura", "SRTP"],
            },
        )
    )
    assert result["success"] is True, result
    assert result["changed"] == ["belongs", "tags"]

    after = (surface / "notes" / "某主题" / "某篇.md").read_text(encoding="utf-8")
    # 正文一个字没动
    assert after.split("---", 2)[2] == before.split("---", 2)[2]
    # 那几格换了
    assert "tags: [化学, 实验]" in after, after
    assert "belongs: [Aura, SRTP]" in after, after
    # 其余的格原样留着
    assert "title: 某篇" in after, after


def test_note_set_property_refuses_a_title_change(roots):
    """标题就是那篇的名字 ⇒ 改它走 note_rename（文件名与入链一起动）。"""
    _mind, _surface, provider = roots

    result = json.loads(
        provider.handle_tool_call(
            "note_set_property",
            {"path": "Notes/某主题/某篇.md", "title": "新名字"},
        )
    )
    assert result["success"] is False
    assert "note_rename" in result["error"]


def test_note_set_property_rejects_an_unknown_owner(roots, monkeypatch):
    """契约还在：``belongs`` 里名册不认的名字要拒（盘上一个字节不动）。"""
    _mind, surface, provider = roots
    _open_gate(monkeypatch)
    target = surface / "notes" / "某主题" / "某篇.md"
    before = target.read_bytes()

    result = json.loads(
        provider.handle_tool_call(
            "note_set_property",
            {"path": "Notes/某主题/某篇.md", "belongs": ["查无此人"]},
        )
    )
    assert result["success"] is False
    assert result["problems"], result
    assert target.read_bytes() == before


def test_note_set_property_needs_at_least_one_key(roots):
    _mind, _surface, provider = roots

    result = json.loads(
        provider.handle_tool_call(
            "note_set_property", {"path": "Notes/某主题/某篇.md"}
        )
    )
    assert result["success"] is False
    assert "tags" in result["error"]


# ── ④ 门类与工具表 ────────────────────────────────────────────────────────


def test_the_three_new_tools_belong_to_the_note_category():
    from agent.memory_manager import _PROVIDER_TOOL_TOOLSET

    for name in ("note_list", "note_links", "note_set_property"):
        assert _PROVIDER_TOOL_TOOLSET.get(name) == "note", name


def test_the_three_new_tools_reach_the_agent_tool_table():
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

    agent = _Agent(["note"])
    inject_memory_provider_tools(agent)
    names = {t["function"]["name"] for t in agent.tools}
    assert {"note_list", "note_links", "note_set_property"} <= names, names


def test_the_new_tools_are_atomic_no_branch_parameters():
    """裁定 185：不许用 action/mode 这类参数把两件事塞进一枚工具。"""
    provider = MindProvider()
    schemas = {s["name"]: s for s in provider.get_tool_schemas()}
    for name in ("note_list", "note_links", "note_set_property"):
        assert name in schemas, name
        properties = set(schemas[name]["parameters"]["properties"])
        assert not {"action", "mode", "surface", "intent"} & properties, name
