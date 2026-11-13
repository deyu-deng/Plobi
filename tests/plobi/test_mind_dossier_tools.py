"""十一把原子工具：人读面那三样各有各的读写，脑那一面一对，外加一把「列自己名下有哪些笔记」
与两把「改名／删除」（这两样**永不自动**）。

用户 2026-10-25：「档案严重过时了……Agent 会自己维护吗？」两答＝给模型一枚写档案的手、
白名单放开到全部项目与研究。用户 2026-10-29 再拍两问：名字要按读者分，并且**读写都给、
真对称**。用户 2026-11-10 再拍一刀：**别再叫 surface 了**，日记／笔记／档案其实是三样
不一样的东西，原子工具**按种类拆**。这里钉住五件事：**能写了**（以前静默 skip）、
**一枚工具只碰一类**（越类当场拒，不装作读不到）、**闸门关着时不许谎报落盘**（裁 199）、
**给人看的拼法不许出现盘上没有的那个词**（脑那一面对模型叫 ``Mind/…``）、
**旧工具名退役不留别名**。

11-09：档案本体（``dossier.json``）是目标／现状的真源，``plan.md``／``progress.md``
那一对退役——这一档的契约跟着迁到 ``dossier.json``。
"""

from __future__ import annotations

import json
import textwrap

import pytest

from plobi.mind import provider as provider_module
from plobi.mind import writer as writer_module
from tools import write_approval as write_approval_module
from plobi.mind.paths import is_memory_zone, is_safe_relative
from plobi.mind.provider import MindProvider


@pytest.fixture()
def roots(tmp_path, monkeypatch):
    mind = tmp_path / "mind"
    surface = tmp_path / "surface"
    (mind / "wiki" / "daily").mkdir(parents=True)
    (mind / "wiki" / "concepts").mkdir(parents=True)
    (mind / "wiki" / "concepts" / "plobi-gate.md").write_text(
        textwrap.dedent(
            """\
            # 写脑那道闸

            初期每次先点头。
            """
        ),
        encoding="utf-8",
    )
    (surface / "dossiers" / "projects" / "Aura").mkdir(parents=True)
    (surface / "dossiers" / "research" / "SRTP").mkdir(parents=True)
    (surface / "notes").mkdir()
    (surface / "notes" / "某主题").mkdir()
    (surface / "notes" / "某主题" / "某篇.md").write_text(
        "---\ntitle: 某篇\ntags: [物理]\nbelongs: [Aura]\n---\n\n正文不该出现在清单里。\n",
        encoding="utf-8",
    )
    # **档案本体**（``dossier.json``）是目标／现状的真源（11-09）；``plan.md``／
    # ``progress.md`` 那一对退役了——这一档的契约跟着迁到 ``dossier.json``。
    (surface / "dossiers" / "projects" / "Aura" / "dossier.json").write_text(
        json.dumps(
            {
                "agent": "Aura",
                "goal": "把情绪记录做成一个能用的应用。",
                "status": "上一句还停在九月。",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (surface / "dossiers" / "research" / "SRTP" / "dossier.json").write_text(
        json.dumps(
            {
                "agent": "SRTP",
                "goal": "把电动装载机那件事做成学位论文。",
                "status": "在写第三章。",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("HUMAN_ROOT", str(surface))
    provider = MindProvider()
    provider.initialize("sess-dossier", agent_context="primary", agent_identity="Aura")
    return mind, surface, provider


def test_every_dossier_home_is_now_writable(roots):
    _mind, _vault, _provider = roots
    assert is_safe_relative("Dossiers/projects/Aura/dossier.json") is True
    assert is_safe_relative("Dossiers/research/SRTP/dossier.json") is True
    assert is_safe_relative("Dossiers/courses/电工电子学/requirements.md") is True


def test_the_old_logical_prefix_is_no_longer_writable(roots):
    """2026-11-13「全清，不留过渡态」：``Surface/…`` 这条旧写法连解析都不再认。

    逻辑头只有种类一种写法。旧写法若还放行，等于把「两套布局并存」留在暗处，
    下一次布局变更又得再猜一遍谁认谁。
    """
    _mind, _vault, _provider = roots
    assert is_safe_relative("Surface/dossiers/projects/Aura/dossier.json") is False
    assert is_safe_relative("Surface/notes/某主题/某篇.md") is False


def test_widening_surface_does_not_widen_the_memory_surface(roots):
    _mind, _vault, _provider = roots
    # 记忆的读写只认脑里那几格；人读面再宽也不是记忆区（用户 10-24/25 两次判）。
    assert is_memory_zone("Dossiers/projects/Aura/dossier.json") is False
    assert is_memory_zone("Notes") is False
    assert is_memory_zone("Mind/wiki/daily/2026-10-25.md") is True


def test_paths_still_refuse_escapes_and_legacy_zones(roots):
    _mind, _vault, _provider = roots
    for bad in (
        "../etc/passwd",
        "Surface/../../Windows",
        "Notes/../../Windows",
        "Mind/raw/chat-logs/x.md",
        "random/file.md",
    ):
        assert is_safe_relative(bad) is False, bad


def test_the_tools_are_atomic(roots):
    _mind, _vault, provider = roots
    schemas = provider.get_tool_schemas()
    assert [schema["name"] for schema in schemas] == [
        "note_read",
        "note_write",
        "note_rename",
        "note_delete",
        "diary_read",
        "diary_write",
        "dossier_read",
        "dossier_write",
        "mind_read",
        "mind_write",
        "agent_notes",
    ]
    for schema in schemas:
        # 裁定 185：不许用 action/mode/surface 这类选分支的参数把两件事塞进一枚工具——
        # 「碰哪一类」由工具身份决定，不是让模型猜对才走对。
        assert not {"action", "mode", "surface", "intent"} & set(schema["parameters"]["properties"])
    for schema in schemas:
        name = schema["name"]
        if name.endswith("_read"):
            assert set(schema["parameters"]["properties"]) == {"path"}, name
        elif name == "dossier_write":
            # 档案那把手是**字段级**的：改哪几格由参数说了算（``content`` 是同一目录
            # 里**另一个**文件那一路，例如当日清单 ``tasks.json``）。
            assert set(schema["parameters"]["properties"]) == {
                "path",
                "agent",
                "goal",
                "status",
                "stages",
                "log_append",
                "content",
            }
        elif name == "agent_notes":
            assert set(schema["parameters"]["properties"]) == {"agent"}
        elif name == "note_rename":
            # 改名那把只收两个：哪一篇 ＋ 新名字（裁 185：一枚工具只干一件事）。
            assert set(schema["parameters"]["properties"]) == {"path", "name"}
        elif name == "note_delete":
            assert set(schema["parameters"]["properties"]) == {"path"}
        else:
            assert set(schema["parameters"]["properties"]) == {"path", "content"}
    # 描述里不许出现**退役的旧拼法**（用户 10-29：「不是已经没有 Loom 这个东西了吗」；
    # 2026-11-04 起 Loom 与 Vault 两个旧名全线退役 ⇒ 两个都不许再泄进给模型看的文字。
    # 别把这条读成「不许出现 Mind」——脑仓给模型的说法就叫 Mind/，那是活名不是旧名。
    joined = " ".join(str(schema["description"]) for schema in schemas)
    assert "Loom" not in joined
    assert "Vault" not in joined
    # 旧工具名退役：连描述里都不许再提（那两把已经不存在了）。
    assert "surface_read" not in joined
    assert "surface_write" not in joined
    # 档案那把手的描述按 ``dossier.json`` 的形状写：目标／现状／阶段。
    dossier = next(s for s in schemas if s["name"] == "dossier_write")
    assert "goal" in dossier["description"] and "status" in dossier["description"]
    assert "stages" in dossier["description"]


def test_the_old_tool_names_are_retired_without_aliases(roots):
    """``surface_read``／``surface_write`` 退役，**不留别名**。

    留别名等于那一把通吃的工具还在模型手上——按种类拆就白拆了。
    """
    _mind, _vault, provider = roots
    for name in ("surface_read", "surface_write"):
        result = json.loads(provider.handle_tool_call(name, {"path": "Notes/x.md"}))
        assert result["success"] is False
        assert "does not handle" in result["error"]


def test_dossier_read_returns_the_file_or_says_it_is_missing(roots):
    _mind, _vault, provider = roots

    ok = json.loads(
        provider.handle_tool_call("dossier_read", {"path": "Dossiers/projects/Aura/dossier.json"})
    )
    assert ok["success"] is True
    assert "上一句还停在九月" in ok["content"]

    missing = json.loads(
        provider.handle_tool_call("dossier_read", {"path": "Dossiers/projects/Aura/plan.md"})
    )
    assert missing["success"] is False
    assert "还不存在" in missing["error"]

    # 工具面只认档案：脑里的东西（原料区与 wiki 那两格都算）一律指名道姓拒掉。
    for outside_path in ("Mind/raw/chat-logs/x.md", "Mind/wiki/concepts/x.md", "random/file.md"):
        outside = json.loads(provider.handle_tool_call("dossier_read", {"path": outside_path}))
        assert outside["success"] is False
        assert "Dossiers" in outside["error"]


def test_rename_and_delete_always_wait_for_the_user(roots, monkeypatch):
    """改名与删除：**永不自动**（用户 10-23 四拍：永不自动＝日记＋画像＋删除）。

    Agent 调它们 ⇒ 一律进待批，盘上那一份一个字节都不许动；人点头才落。
    这两把走自己的载荷（``kind: "note_manage"``）——``mind_plan`` 那条路只认脑里的落点。
    """
    _mind, surface, provider = roots
    monkeypatch.setenv("PLOBI_HOME", str(surface.parent / "home"))
    staged = []

    def fake_stage(subsystem, payload, *, summary, origin):
        staged.append((subsystem, payload, summary))
        return {"id": "cafe1234"}

    monkeypatch.setattr(write_approval_module, "stage_write", fake_stage)
    target = surface / "notes" / "某主题" / "某篇.md"
    before = target.read_bytes()

    renamed = json.loads(
        provider.handle_tool_call(
            "note_rename", {"path": "Notes/某主题/某篇.md", "name": "新篇"}
        )
    )
    assert renamed["success"] is False
    assert renamed["staged"] is True
    assert renamed["pending_id"] == "cafe1234"
    assert "approve" in renamed["detail"]

    deleted = json.loads(
        provider.handle_tool_call("note_delete", {"path": "Notes/某主题/某篇.md"})
    )
    assert deleted["success"] is False
    assert deleted["staged"] is True

    # 待批就是待批：盘上那一份一个字节都不许动。
    assert target.read_bytes() == before

    assert [payload["kind"] for _sub, payload, _summary in staged] == [
        "note_manage",
        "note_manage",
    ]
    assert staged[0][1]["op"]["kind"] == "rename"
    assert staged[0][1]["op"]["new_path"].endswith("新篇.md")
    assert staged[1][1]["op"]["kind"] == "delete"
    assert staged[1][1]["op"]["trash"].startswith(".trash/")


def test_rename_and_delete_refuse_the_other_faces(roots):
    """一枚工具只碰一类：日记与档案不归笔记那两把。"""
    _mind, _surface, provider = roots
    for tool, args in (
        ("note_rename", {"path": "Diary/2026-11-10-Tuesday.md", "name": "新篇"}),
        ("note_delete", {"path": "Dossiers/projects/Aura/dossier.json"}),
    ):
        result = json.loads(provider.handle_tool_call(tool, args))
        assert result["success"] is False
        assert "Notes" in result["error"], result


def test_note_write_goes_to_the_pending_queue_when_the_gate_is_closed(roots, monkeypatch):
    _mind, surface, provider = roots
    staged = []
    target = surface / "notes" / "某主题" / "某篇.md"
    before = target.read_bytes()

    def fake_stage(self, batch):
        staged.append([request.relative_path for request in batch])
        plan = type("Plan", (), {"digest": "abc123def456", "changes": [object()]})()
        return plan, {"id": "deadbeef"}

    monkeypatch.setattr(writer_module.MindWriter, "_stage_for_approval", fake_stage)

    result = json.loads(
        provider.handle_tool_call(
            "note_write",
            {
                "path": "Notes/某主题/某篇.md",
                "content": "---\ntitle: 某篇\ntags: [物理]\nbelongs: [Aura]\n---\n\n新的正文。\n",
            },
        )
    )
    assert result["success"] is False
    assert result["staged"] is True
    assert result["pending_id"] == "deadbeef"
    assert "approve" in result["detail"]
    assert staged == [["Notes/某主题/某篇.md"]]
    # 待批就是待批：盘上那一份一个字节都不许动。
    assert target.read_bytes() == before


def test_note_write_lands_when_the_gate_opens(roots, monkeypatch):
    _mind, surface, provider = roots
    monkeypatch.setattr(writer_module.MindWriter, "_gate_refusals", lambda self, batch: [])
    monkeypatch.setattr(
        provider_module,
        "MindWriter",
        lambda root=None: writer_module.MindWriter(root, commit=False, run_verifier=False),
    )

    result = json.loads(
        provider.handle_tool_call(
            "note_write",
            {
                "path": "Notes/某主题/某篇.md",
                "content": "---\ntitle: 某篇\ntags: [物理]\nbelongs: [Aura]\n---\n\n新的正文。\n",
            },
        )
    )
    assert result["success"] is True, result
    written = (surface / "notes" / "某主题" / "某篇.md").read_text(encoding="utf-8")
    assert "新的正文。" in written


def test_note_write_refuses_empty_oversized_and_unknown_tools(roots):
    _mind, _vault, provider = roots

    empty = json.loads(
        provider.handle_tool_call(
            "note_write", {"path": "Notes/某主题/某篇.md", "content": "  "}
        )
    )
    assert empty["success"] is False and "non-empty" in empty["error"]

    big = json.loads(
        provider.handle_tool_call(
            "note_write",
            {
                "path": "Notes/某主题/某篇.md",
                "content": "x" * (provider_module.VAULT_WRITE_MAX_CHARS + 1),
            },
        )
    )
    assert big["success"] is False and "too large" in big["error"]

    unknown = json.loads(provider.handle_tool_call("mind_search", {"q": "x"}))
    assert unknown["success"] is False and "does not handle" in unknown["error"]


def test_each_tool_touches_only_its_own_kind(roots):
    """越类就当场拒：笔记那两把读不到档案，档案那两把写不了人的笔记，脑那两把碰不到人读面。"""
    _mind, _vault, provider = roots

    def call(tool, path):
        content = textwrap.dedent(
            """\
            # x

            正文。
            """
        )
        return json.loads(provider.handle_tool_call(tool, {"path": path, "content": content}))

    cross = [
        ("note_read", "Dossiers/projects/Aura/dossier.json"),
        ("note_write", "Dossiers/projects/Aura/dossier.json"),
        ("diary_read", "Notes/某主题/某篇.md"),
        ("diary_write", "Notes/某主题/某篇.md"),
        ("dossier_read", "Notes/某主题/某篇.md"),
        ("dossier_write", "Notes/某主题/某篇.md"),
        ("mind_read", "Dossiers/projects/Aura/dossier.json"),
        ("mind_write", "Notes/某主题/某篇.md"),
    ]
    for tool, path in cross:
        result = call(tool, path)
        assert result["success"] is False, (tool, path)
        assert "不在这一枚工具的面里" in result["error"], (tool, result)
        # 要说清该用哪一枚：人读面那几把各自的名字，脑那两把是 ``mind_*``。
        assert "mind_" in result["error"] or "note_" in result["error"] or "diary_" in result["error"] \
            or "dossier_" in result["error"], (tool, result)


def test_dossier_write_only_touches_dossier_records(roots):
    """档案那把手只认档案本体：脑里的页、人读面别的文件，一律当场拒。"""
    _mind, _vault, provider = roots

    for path in (
        "Mind/wiki/concepts/plobi-gate.md",
        "Dossiers/projects/Aura/plan.md",
        "Notes/某主题/某篇.md",
    ):
        result = json.loads(provider.handle_tool_call("dossier_write", {"path": path, "goal": "x"}))
        assert result["success"] is False, path
        assert "不是档案本体" in result["error"], (path, result)


def test_dossier_write_changes_only_the_named_fields(roots):
    """档案本体走**字段级**那一路：点名的格子改掉，其余原样留着（不过 L1 那道闸）。"""
    _mind, surface, provider = roots
    target = surface / "dossiers" / "projects" / "Aura" / "dossier.json"
    before = json.loads(target.read_text(encoding="utf-8"))

    result = json.loads(
        provider.handle_tool_call(
            "dossier_write",
            {"path": "Dossiers/projects/Aura/dossier.json", "status": "今天写完了。"},
        )
    )
    assert result["success"] is True, result
    after = json.loads(target.read_text(encoding="utf-8"))
    assert after["status"] == "今天写完了。"
    assert after["goal"] == before["goal"]


def test_dossier_write_refuses_a_raw_overwrite_of_the_record(roots):
    """档案本体**不许整份覆盖**——那是字段级那把手存在的理由（形状一坏就报废）。"""
    _mind, _vault, provider = roots
    result = json.loads(
        provider.handle_tool_call(
            "dossier_write",
            {"path": "Dossiers/projects/Aura/dossier.json", "content": "{ broken"},
        )
    )
    assert result["success"] is False
    assert "字段级" in result["error"]


def test_dossier_write_can_write_the_checklist_beside_it(roots, monkeypatch):
    """档案目录里的**另一个文件**（当日清单 ``tasks.json``）走 ``content`` 那一路。

    旧那把通吃的 ``surface_write`` 有这个能力，按种类拆开之后不许丢。
    """
    _mind, surface, provider = roots
    monkeypatch.setattr(writer_module.MindWriter, "_gate_refusals", lambda self, batch: [])
    monkeypatch.setattr(
        provider_module,
        "MindWriter",
        lambda root=None: writer_module.MindWriter(root, commit=False, run_verifier=False),
    )
    result = json.loads(
        provider.handle_tool_call(
            "dossier_write",
            {
                "path": "Dossiers/projects/Aura/tasks.json",
                "content": json.dumps({"days": []}, ensure_ascii=False),
            },
        )
    )
    assert result["success"] is True, result
    assert (surface / "dossiers" / "projects" / "Aura" / "tasks.json").is_file()


def test_agent_notes_lists_the_notes_that_belong_to_you(roots):
    """列自己名下有哪些笔记——与屏上「相关笔记」同一份投影；**只给清单，不给正文**。"""
    _mind, _vault, provider = roots

    result = json.loads(provider.handle_tool_call("agent_notes", {}))
    assert result["success"] is True, result
    paths = [note["path"] for note in result["notes"]]
    assert paths == ["Notes/某主题/某篇.md"], paths
    titles = [note["title"] for note in result["notes"]]
    assert titles == ["某篇"], titles
    # 正文一个字都不许漏进来。
    assert "正文不该出现在清单里" not in json.dumps(result, ensure_ascii=False)


def test_agent_notes_without_an_agent_says_so(roots):
    """连当前身份都没有 ⇒ 说清要一个名字，不许瞎猜一份清单出来。"""
    _mind, _vault, _provider = roots
    bare = MindProvider()
    bare.initialize("sess-bare", agent_context="primary")
    result = json.loads(bare.handle_tool_call("agent_notes", {}))
    assert result["success"] is False
    assert "Agent" in result["error"]


def test_mind_read_speaks_mind_not_loom(roots):
    """脑那一面对模型的说法是 ``Mind/wiki/…``；内部仍按 ``Mind/…`` 解析，回给人看的也是 Mind。"""
    _mind, _vault, provider = roots

    ok = json.loads(provider.handle_tool_call("mind_read", {"path": "Mind/wiki/concepts/plobi-gate.md"}))
    assert ok["success"] is True
    assert ok["path"] == "Mind/wiki/concepts/plobi-gate.md"
    assert "每次先点头" in ok["content"]

    legacy = json.loads(provider.handle_tool_call("mind_read", {"path": "Mind/wiki/concepts/plobi-gate.md"}))
    assert legacy["success"] is True  # 存量那圈旧拼法照样读得到，不改判成"不存在"
    assert legacy["path"] == "Mind/wiki/concepts/plobi-gate.md"
