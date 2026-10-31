"""L2 的身份必须先从**会话**认，再从 env 认；认出来了就不许读顶层那份 L1 秘书 SOUL。

现场：Sindri 那屏问「你是谁」，它答「我是你的 Plobi 总秘书」。根因是桌面主会话进程里没有
`PLOBI_AGENT_ID`，`load_soul_md` 只看 env ⇒ 读了顶层 SOUL（写着「你是 Plobi 总秘书（L1）」）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent import prompt_builder as PB


def test_soul_agent_id_prefers_env_then_session(monkeypatch):
    monkeypatch.setenv("PLOBI_AGENT_ID", "Aura")
    assert PB._soul_agent_id() == "Aura"

    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)
    monkeypatch.setattr(
        "tools.memory_tool.resolve_agent_slug", lambda *a, **k: "Sindri", raising=True
    )
    assert PB._soul_agent_id() == "Sindri"

    monkeypatch.setattr(
        "tools.memory_tool.resolve_agent_slug", lambda *a, **k: "l1", raising=True
    )
    assert PB._soul_agent_id() == "", "L1 读顶层那份，这是对的"


def test_an_agent_without_its_own_soul_gets_its_own_identity_not_the_secretary(monkeypatch):
    """有身份但共享层那份还没生成 ⇒ 按名册生成，**不许**回退到顶层 L1 SOUL。"""
    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)
    monkeypatch.setattr("tools.memory_tool.resolve_agent_slug", lambda *a, **k: "Sindri", raising=True)

    import plobi.agents.registry as R

    entry = SimpleNamespace(name="Sindri", role="l2_project", profile_name="", display_name="Sindri")
    monkeypatch.setattr(R, "shared_soul_path_for", lambda name: __import__("pathlib").Path("/nonexistent/x.md"))
    monkeypatch.setattr(R, "load_registry", lambda *a, **k: SimpleNamespace(entries=lambda: [entry]))
    monkeypatch.setattr(R, "build_l2_identity_soul_block", lambda e, **kw: "你是 Sindri（测试用身份块）")

    text = PB.load_soul_md()
    assert text and "Sindri" in text
    assert "总秘书" not in text
