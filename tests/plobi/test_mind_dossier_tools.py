"""两对读写工具：一面一对（surface_* 给人读面，mind_* 给脑那一面）＋档案那把编辑手。

用户 2026-10-25：「档案严重过时了……Agent 会自己维护吗？」两答＝给模型一枚写档案的手、
白名单放开到全部项目与研究。用户 2026-10-29 再拍两问：名字要按读者分（``surface_write`` 才
对得上盘上那个根），并且**读写都给、真对称**。这里钉住四件事：**能写了**（以前静默 skip）、
**一枚工具只碰一面**（越面当场拒，不装作读不到）、**闸门关着时不许谎报落盘**（裁 199）、
**给人看的拼法不许出现盘上没有的那个词**（脑那一面对模型叫 ``Mind/…``，``Mind/…`` 是内部与
存量的旧拼法）。

11-09：档案本体（``dossier.json``）是目标／现状的真源，``plan.md``／``progress.md``
那一对退役——这一档的契约跟着迁到 ``dossier.json``；另加第五把 ``dossier_write``
（档案本体的字段级编辑手）。
"""

from __future__ import annotations

import json
import textwrap

import pytest

from plobi.mind import provider as provider_module
from plobi.mind import writer as writer_module
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
    monkeypatch.setenv("SURFACE_ROOT", str(surface))
    provider = MindProvider()
    provider.initialize("sess-dossier", agent_context="primary")
    return mind, surface, provider


def test_every_dossier_home_is_now_writable(roots):
    _mind, _vault, _provider = roots
    assert is_safe_relative("Surface/dossiers/projects/Aura/dossier.json") is True
    assert is_safe_relative("Surface/dossiers/research/SRTP/dossier.json") is True
    assert is_safe_relative("Surface/dossiers/courses/电工电子学/requirements.md") is True


def test_widening_surface_does_not_widen_the_memory_surface(roots):
    _mind, _vault, _provider = roots
    # 记忆的读写只认脑里那几格；人读面再宽也不是记忆区（用户 10-24/25 两次判）。
    assert is_memory_zone("Surface/dossiers/projects/Aura/dossier.json") is False
    assert is_memory_zone("Surface/notes") is False
    assert is_memory_zone("Mind/wiki/daily/2026-10-25.md") is True


def test_paths_still_refuse_escapes_and_legacy_zones(roots):
    _mind, _vault, _provider = roots
    for bad in ("../etc/passwd", "Surface/../../Windows", "Mind/raw/chat-logs/x.md", "random/file.md"):
        assert is_safe_relative(bad) is False, bad


def test_the_tools_are_atomic(roots):
    _mind, _vault, provider = roots
    schemas = provider.get_tool_schemas()
    assert [schema["name"] for schema in schemas] == [
        "surface_read",
        "surface_write",
        "dossier_write",
        "mind_read",
        "mind_write",
    ]
    for schema in schemas:
        # 裁定 185：不许用 action/mode/surface 这类选分支的参数把两件事塞进一枚工具——
        # 「碰哪一面」由工具身份决定，不是让模型猜对才走对。
        assert not {"action", "mode", "surface", "intent"} & set(schema["parameters"]["properties"])
    for schema in schemas:
        if schema["name"].endswith("_read"):
            assert set(schema["parameters"]["properties"]) == {"path"}
        elif schema["name"] == "dossier_write":
            # 档案那把手是**字段级**的：改哪几格由参数说了算。
            assert set(schema["parameters"]["properties"]) == {
                "path",
                "agent",
                "goal",
                "status",
                "stages",
                "log_append",
            }
        else:
            assert set(schema["parameters"]["properties"]) == {"path", "content"}
    # 描述里不许出现**退役的旧拼法**（用户 10-29：「不是已经没有 Loom 这个东西了吗」；
    # 2026-11-04 起 Loom 与 Vault 两个旧名全线退役 ⇒ 两个都不许再泄进给模型看的文字。
    # 别把这条读成「不许出现 Mind」——脑仓给模型的说法就叫 Mind/，那是活名不是旧名。
    joined = " ".join(str(schema["description"]) for schema in schemas)
    assert "Loom" not in joined
    assert "Vault" not in joined
    # 档案那把手的描述按 ``dossier.json`` 的形状写：目标／现状／阶段。
    dossier = next(s for s in schemas if s["name"] == "dossier_write")
    assert "goal" in dossier["description"] and "status" in dossier["description"]
    assert "stages" in dossier["description"]


def test_surface_read_returns_the_file_or_says_it_is_missing(roots):
    _mind, _vault, provider = roots

    ok = json.loads(provider.handle_tool_call("surface_read", {"path": "Surface/dossiers/projects/Aura/dossier.json"}))
    assert ok["success"] is True
    assert "上一句还停在九月" in ok["content"]

    missing = json.loads(provider.handle_tool_call("surface_read", {"path": "Surface/dossiers/projects/Aura/plan.md"}))
    assert missing["success"] is False
    assert "还不存在" in missing["error"]

    # 工具面只认人读面：脑里的东西（原料区与 wiki 那两格都算）一律指名道姓拒掉。
    # 用户 2026-10-27 拍「读也改：surface_read + surface_write」，代价就是模型再没有查脑仓的手。
    for outside_path in ("Mind/raw/chat-logs/x.md", "Mind/wiki/concepts/x.md", "random/file.md"):
        outside = json.loads(provider.handle_tool_call("surface_read", {"path": outside_path}))
        assert outside["success"] is False
        assert "Surface/" in outside["error"]


def test_surface_write_goes_to_the_pending_queue_when_the_gate_is_closed(roots, monkeypatch):
    _mind, surface, provider = roots
    staged = []
    target = surface / "dossiers" / "research" / "SRTP" / "dossier.json"
    before = target.read_bytes()

    def fake_stage(self, batch):
        staged.append([request.relative_path for request in batch])
        plan = type("Plan", (), {"digest": "abc123def456", "changes": [object()]})()
        return plan, {"id": "deadbeef"}

    monkeypatch.setattr(writer_module.MindWriter, "_stage_for_approval", fake_stage)

    result = json.loads(
        provider.handle_tool_call(
            "surface_write",
            {
                "path": "Surface/dossiers/research/SRTP/dossier.json",
                "content": json.dumps({"agent": "SRTP", "status": "新东西。"}, ensure_ascii=False),
            },
        )
    )
    assert result["success"] is False
    assert result["staged"] is True
    assert result["pending_id"] == "deadbeef"
    assert "approve" in result["detail"]
    assert staged == [["Surface/dossiers/research/SRTP/dossier.json"]]
    # 待批就是待批：盘上那一份一个字节都不许动。
    assert target.read_bytes() == before


def test_surface_write_lands_when_the_gate_opens(roots, monkeypatch):
    _mind, surface, provider = roots
    monkeypatch.setattr(writer_module.MindWriter, "_gate_refusals", lambda self, batch: [])
    monkeypatch.setattr(
        provider_module,
        "MindWriter",
        lambda root=None: writer_module.MindWriter(root, commit=False, run_verifier=False),
    )

    result = json.loads(
        provider.handle_tool_call(
            "surface_write",
            {
                "path": "Surface/dossiers/projects/Aura/dossier.json",
                "content": json.dumps(
                    {"agent": "Aura", "goal": "今天写完了。"}, ensure_ascii=False
                ),
            },
        )
    )
    assert result["success"] is True, result
    written = (surface / "dossiers" / "projects" / "Aura" / "dossier.json").read_text(encoding="utf-8")
    assert json.loads(written)["goal"] == "今天写完了。"


def test_surface_write_refuses_empty_oversized_and_unknown_tools(roots):
    _mind, _vault, provider = roots

    empty = json.loads(
        provider.handle_tool_call(
            "surface_write", {"path": "Surface/dossiers/projects/Aura/dossier.json", "content": "  "}
        )
    )
    assert empty["success"] is False and "non-empty" in empty["error"]

    big = json.loads(
        provider.handle_tool_call(
            "surface_write",
            {
                "path": "Surface/dossiers/projects/Aura/dossier.json",
                "content": "x" * (provider_module.VAULT_WRITE_MAX_CHARS + 1),
            },
        )
    )
    assert big["success"] is False and "too large" in big["error"]

    unknown = json.loads(provider.handle_tool_call("mind_search", {"q": "x"}))
    assert unknown["success"] is False and "does not handle" in unknown["error"]


def test_each_tool_touches_only_its_own_surface(roots):
    """越面就当场拒：surface_* 读不到脑的页，mind_* 写不了人的笔记。"""
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
        ("surface_read", "Mind/wiki/concepts/plobi-gate.md"),
        ("surface_write", "Mind/wiki/concepts/plobi-gate.md"),
        ("mind_read", "Surface/dossiers/projects/Aura/dossier.json"),
        ("mind_write", "Surface/dossiers/projects/Aura/dossier.json"),
    ]
    for tool, path in cross:
        result = call(tool, path)
        assert result["success"] is False, (tool, path)
        assert "不在这一枚工具的面里" in result["error"], (tool, result)
        assert "surface_" in result["error"] and "mind_" in result["error"]  # 要说清该用哪一枚


def test_dossier_write_only_touches_dossier_records(roots):
    """档案那把手只认档案本体：脑里的页、人读面别的文件，一律当场拒。"""
    _mind, _vault, provider = roots

    for path in (
        "Mind/wiki/concepts/plobi-gate.md",
        "Surface/dossiers/projects/Aura/plan.md",
        "Surface/notes/某篇.md",
    ):
        result = json.loads(provider.handle_tool_call("dossier_write", {"path": path, "goal": "x"}))
        assert result["success"] is False, path
        assert "不是档案本体" in result["error"], (path, result)


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
