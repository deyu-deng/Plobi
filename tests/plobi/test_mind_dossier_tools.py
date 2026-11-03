"""两对读写工具：一面一对（vault_* 给人读面，mind_* 给脑那一面）。

用户 2026-10-25：「档案严重过时了……Agent 会自己维护吗？」两答＝给模型一枚写档案的手、
白名单放开到全部项目与研究。用户 2026-10-29 再拍两问：名字要按读者分（``vault_write`` 才
对得上盘上那个根），并且**读写都给、真对称**。这里钉住四件事：**能写了**（以前静默 skip）、
**一枚工具只碰一面**（越面当场拒，不装作读不到）、**闸门关着时不许谎报落盘**（裁 199）、
**给人看的拼法不许出现盘上没有的那个词**（脑那一面对模型叫 ``Mind/…``，``Loom/…`` 是内部与
存量的旧拼法）。
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
    vault = tmp_path / "vault"
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
    (vault / "dossiers" / "projects" / "Aura").mkdir(parents=True)
    (vault / "dossiers" / "research" / "SRTP").mkdir(parents=True)
    (vault / "notes").mkdir()
    (vault / "dossiers" / "projects" / "Aura" / "progress.md").write_text(
        textwrap.dedent(
            """\
            # Aura · 进度

            上一句还停在九月。
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("VAULT_ROOT", str(vault))
    provider = MindProvider()
    provider.initialize("sess-dossier", agent_context="primary")
    return mind, vault, provider


def test_every_dossier_home_is_now_writable(roots):
    _mind, _vault, _provider = roots
    assert is_safe_relative("Vault/dossiers/projects/Aura/progress.md") is True
    assert is_safe_relative("Vault/dossiers/research/SRTP/progress.md") is True
    assert is_safe_relative("Vault/dossiers/courses/电工电子学/requirements.md") is True


def test_widening_vault_does_not_widen_the_memory_surface(roots):
    _mind, _vault, _provider = roots
    # 记忆的读写只认脑里那几格；人读面再宽也不是记忆区（用户 10-24/25 两次判）。
    assert is_memory_zone("Vault/dossiers/projects/Aura/progress.md") is False
    assert is_memory_zone("Vault/notes") is False
    assert is_memory_zone("Loom/wiki/daily/2026-10-25.md") is True


def test_paths_still_refuse_escapes_and_legacy_zones(roots):
    _mind, _vault, _provider = roots
    for bad in ("../etc/passwd", "Vault/../../Windows", "Loom/raw/chat-logs/x.md", "random/file.md"):
        assert is_safe_relative(bad) is False, bad


def test_the_four_tools_are_atomic(roots):
    _mind, _vault, provider = roots
    schemas = provider.get_tool_schemas()
    assert [schema["name"] for schema in schemas] == [
        "vault_read",
        "vault_write",
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
        else:
            assert set(schema["parameters"]["properties"]) == {"path", "content"}
    # 描述里不许出现盘上没有的那个词（用户 10-29：「不是已经没有 Loom 这个东西了吗」）
    joined = " ".join(str(schema["description"]) for schema in schemas)
    assert "Loom" not in joined


def test_vault_read_returns_the_file_or_says_it_is_missing(roots):
    _mind, _vault, provider = roots

    ok = json.loads(provider.handle_tool_call("vault_read", {"path": "Vault/dossiers/projects/Aura/progress.md"}))
    assert ok["success"] is True
    assert "上一句还停在九月" in ok["content"]

    missing = json.loads(provider.handle_tool_call("vault_read", {"path": "Vault/dossiers/projects/Aura/plan.md"}))
    assert missing["success"] is False
    assert "还不存在" in missing["error"]

    # 工具面只认人读面：脑里的东西（原料区与 wiki 那两格都算）一律指名道姓拒掉。
    # 用户 2026-10-27 拍「读也改：vault_read + vault_write」，代价就是模型再没有查脑仓的手。
    for outside_path in ("Loom/raw/chat-logs/x.md", "Loom/wiki/concepts/x.md", "random/file.md"):
        outside = json.loads(provider.handle_tool_call("vault_read", {"path": outside_path}))
        assert outside["success"] is False
        assert "Vault/" in outside["error"]


def test_vault_write_goes_to_the_pending_queue_when_the_gate_is_closed(roots, monkeypatch):
    _mind, vault, provider = roots
    staged = []

    def fake_stage(self, batch):
        staged.append([request.relative_path for request in batch])
        plan = type("Plan", (), {"digest": "abc123def456", "changes": [object()]})()
        return plan, {"id": "deadbeef"}

    monkeypatch.setattr(writer_module.MindWriter, "_stage_for_approval", fake_stage)

    result = json.loads(
        provider.handle_tool_call(
            "vault_write", {"path": "Vault/dossiers/research/SRTP/progress.md", "content": "# SRTP · 进度\n\n新东西。\n"}
        )
    )
    assert result["success"] is False
    assert result["staged"] is True
    assert result["pending_id"] == "deadbeef"
    assert "approve" in result["detail"]
    assert staged == [["Vault/dossiers/research/SRTP/progress.md"]]
    # 待批就是待批：盘上不许有半个字节
    assert not (vault / "dossiers" / "research" / "SRTP" / "progress.md").exists()


def test_vault_write_lands_when_the_gate_opens(roots, monkeypatch):
    _mind, vault, provider = roots
    monkeypatch.setattr(writer_module.MindWriter, "_gate_refusals", lambda self, batch: [])
    monkeypatch.setattr(
        provider_module,
        "MindWriter",
        lambda root=None: writer_module.MindWriter(root, commit=False, run_verifier=False),
    )

    result = json.loads(
        provider.handle_tool_call(
            "vault_write", {"path": "Vault/dossiers/projects/Aura/progress.md", "content": "# Aura · 进度\n\n今天写完了。\n"}
        )
    )
    assert result["success"] is True, result
    assert (vault / "dossiers" / "projects" / "Aura" / "progress.md").read_text(encoding="utf-8").startswith("# Aura · 进度")


def test_vault_write_refuses_empty_oversized_and_unknown_tools(roots):
    _mind, _vault, provider = roots

    empty = json.loads(provider.handle_tool_call("vault_write", {"path": "Vault/dossiers/projects/Aura/plan.md", "content": "  "}))
    assert empty["success"] is False and "non-empty" in empty["error"]

    big = json.loads(
        provider.handle_tool_call(
            "vault_write",
            {"path": "Vault/dossiers/projects/Aura/plan.md", "content": "x" * (provider_module.VAULT_WRITE_MAX_CHARS + 1)},
        )
    )
    assert big["success"] is False and "too large" in big["error"]

    unknown = json.loads(provider.handle_tool_call("mind_search", {"q": "x"}))
    assert unknown["success"] is False and "does not handle" in unknown["error"]


def test_each_tool_touches_only_its_own_surface(roots):
    """越面就当场拒：vault_* 读不到脑的页，mind_* 写不了人的笔记。"""
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
        ("vault_read", "Mind/wiki/concepts/plobi-gate.md"),
        ("vault_write", "Mind/wiki/concepts/plobi-gate.md"),
        ("mind_read", "Vault/dossiers/projects/Aura/progress.md"),
        ("mind_write", "Vault/dossiers/projects/Aura/progress.md"),
    ]
    for tool, path in cross:
        result = call(tool, path)
        assert result["success"] is False, (tool, path)
        assert "不在这一枚工具的面里" in result["error"], (tool, result)
        assert "vault_" in result["error"] and "mind_" in result["error"]  # 要说清该用哪一枚


def test_mind_read_speaks_mind_not_loom(roots):
    """脑那一面对模型的说法是 ``Mind/wiki/…``；内部仍按 ``Loom/…`` 解析，回给人看的也是 Mind。"""
    _mind, _vault, provider = roots

    ok = json.loads(provider.handle_tool_call("mind_read", {"path": "Mind/wiki/concepts/plobi-gate.md"}))
    assert ok["success"] is True
    assert ok["path"] == "Mind/wiki/concepts/plobi-gate.md"
    assert "每次先点头" in ok["content"]

    legacy = json.loads(provider.handle_tool_call("mind_read", {"path": "Loom/wiki/concepts/plobi-gate.md"}))
    assert legacy["success"] is True  # 存量那圈旧拼法照样读得到，不改判成"不存在"
    assert legacy["path"] == "Loom/wiki/concepts/plobi-gate.md"
