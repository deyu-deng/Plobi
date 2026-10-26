"""档案写腿：白名单放开到全部项目＋研究，以及 mind_read / mind_write 两枚原子工具。

用户 2026-10-25：「档案严重过时了……Agent 会自己维护吗？」两答＝给模型一枚写档案的手、
白名单放开到全部项目与研究。这里钉住三件事：**能写了**（以前静默 skip）、**放开 vault 不
等于放开记忆面**（用户 10-24/25 两次判「vault 不要进记忆系统」）、**闸门关着时不许谎报
落盘**（裁 199）。
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
    (vault / "projects" / "Aura").mkdir(parents=True)
    (vault / "research" / "SRTP").mkdir(parents=True)
    (vault / "notes").mkdir()
    (vault / "projects" / "Aura" / "progress.md").write_text(
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
    assert is_safe_relative("Vault/projects/Aura/progress.md") is True
    assert is_safe_relative("Vault/research/SRTP/progress.md") is True
    assert is_safe_relative("Vault/courses/电工电子学/requirements.md") is True


def test_widening_vault_does_not_widen_the_memory_surface(roots):
    _mind, _vault, _provider = roots
    # 记忆的读写只认脑里那几格；人读面再宽也不是记忆区（用户 10-24/25 两次判）。
    assert is_memory_zone("Vault/projects/Aura/progress.md") is False
    assert is_memory_zone("Vault/notes") is False
    assert is_memory_zone("Loom/wiki/daily/2026-10-25.md") is True


def test_paths_still_refuse_escapes_and_legacy_zones(roots):
    _mind, _vault, _provider = roots
    for bad in ("../etc/passwd", "Vault/../../Windows", "Loom/raw/chat-logs/x.md", "random/file.md"):
        assert is_safe_relative(bad) is False, bad


def test_the_two_tools_are_atomic(roots):
    _mind, _vault, provider = roots
    schemas = provider.get_tool_schemas()
    assert [schema["name"] for schema in schemas] == ["mind_read", "mind_write"]
    for schema in schemas:
        # 裁定 185：不许用 action/mode 这类选分支的参数把两件事塞进一枚工具。
        assert "action" not in schema["parameters"]["properties"]
        assert "mode" not in schema["parameters"]["properties"]
    assert set(schemas[1]["parameters"]["properties"]) == {"path", "content"}


def test_mind_read_returns_the_file_or_says_it_is_missing(roots):
    _mind, _vault, provider = roots

    ok = json.loads(provider.handle_tool_call("mind_read", {"path": "Vault/projects/Aura/progress.md"}))
    assert ok["success"] is True
    assert "上一句还停在九月" in ok["content"]

    missing = json.loads(provider.handle_tool_call("mind_read", {"path": "Vault/projects/Aura/plan.md"}))
    assert missing["success"] is False
    assert "还不存在" in missing["error"]

    # 白名单外（脑的原料区已作废）要指名道姓拒掉，并把可写的面报回去
    outside = json.loads(provider.handle_tool_call("mind_read", {"path": "Loom/raw/chat-logs/x.md"}))
    assert outside["success"] is False
    assert "Vault/projects" in outside["allowed_prefixes"]


def test_mind_write_goes_to_the_pending_queue_when_the_gate_is_closed(roots, monkeypatch):
    _mind, vault, provider = roots
    staged = []

    def fake_stage(self, batch):
        staged.append([request.relative_path for request in batch])
        plan = type("Plan", (), {"digest": "abc123def456", "changes": [object()]})()
        return plan, {"id": "deadbeef"}

    monkeypatch.setattr(writer_module.MindWriter, "_stage_for_approval", fake_stage)

    result = json.loads(
        provider.handle_tool_call(
            "mind_write", {"path": "Vault/research/SRTP/progress.md", "content": "# SRTP · 进度\n\n新东西。\n"}
        )
    )
    assert result["success"] is False
    assert result["staged"] is True
    assert result["pending_id"] == "deadbeef"
    assert "approve" in result["detail"]
    assert staged == [["Vault/research/SRTP/progress.md"]]
    # 待批就是待批：盘上不许有半个字节
    assert not (vault / "research" / "SRTP" / "progress.md").exists()


def test_mind_write_lands_when_the_gate_opens(roots, monkeypatch):
    _mind, vault, provider = roots
    monkeypatch.setattr(writer_module.MindWriter, "_gate_refusals", lambda self, batch: [])
    monkeypatch.setattr(
        provider_module,
        "MindWriter",
        lambda root=None: writer_module.MindWriter(root, commit=False, run_verifier=False),
    )

    result = json.loads(
        provider.handle_tool_call(
            "mind_write", {"path": "Vault/projects/Aura/progress.md", "content": "# Aura · 进度\n\n今天写完了。\n"}
        )
    )
    assert result["success"] is True, result
    assert (vault / "projects" / "Aura" / "progress.md").read_text(encoding="utf-8").startswith("# Aura · 进度")


def test_mind_write_refuses_empty_oversized_and_unknown_tools(roots):
    _mind, _vault, provider = roots

    empty = json.loads(provider.handle_tool_call("mind_write", {"path": "Vault/projects/Aura/plan.md", "content": "  "}))
    assert empty["success"] is False and "non-empty" in empty["error"]

    big = json.loads(
        provider.handle_tool_call(
            "mind_write",
            {"path": "Vault/projects/Aura/plan.md", "content": "x" * (provider_module.MIND_WRITE_MAX_CHARS + 1)},
        )
    )
    assert big["success"] is False and "too large" in big["error"]

    unknown = json.loads(provider.handle_tool_call("mind_search", {"q": "x"}))
    assert unknown["success"] is False and "does not handle" in unknown["error"]
