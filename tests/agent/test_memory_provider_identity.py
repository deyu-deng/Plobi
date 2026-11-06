"""记忆 provider 那把身份＝**会话身份**（2026-11-06 补；裁 179 那条欠账）。

为什么要有这一档：以前 ``agent/agent_init.py`` 给记忆 provider 的 ``agent_identity``
取的是**进程级**的 ``get_active_profile_name()``——它只看 ``PLOBI_HOME`` 落在哪儿，
于是**一台后端替多个 Agent 干活时，那个值对每个 Agent 都一样**（这台机器上是
"custom"，因为 ``PLOBI_HOME`` 不是 ``~/.plobi``）。记忆 provider 那边（hindsight 的
``agent_identity`` 元数据、supermemory 的 peer、bank_id 模板里的 ``profile=``）于是
分不出人：裁 179 说的「记忆按 Agent 分持」等于没分。

真源是 ``plobi.agents.registry._active_agent_profile_name()``（``PLOBI_AGENT_ID`` →
会话 ContextVar → 旧家目录形状 → ''）。这一档钉住它**优先**，并钉住共享层那个会话
**行为一字不动**（仍回落进程级那一份）。

跑法：``bash scripts/run_tests.sh tests/agent/test_memory_provider_identity.py``
"""

from __future__ import annotations


def test_the_session_identity_wins(monkeypatch):
    """子进程注入的 AGENT_ID 就是那个 Agent 的身份（不再被进程级那一份盖住）。"""
    from agent.agent_init import _memory_provider_identity

    monkeypatch.setenv("PLOBI_AGENT_ID", "Stithy")
    assert _memory_provider_identity() == "Stithy"


def test_a_shared_session_keeps_the_process_level_identity(monkeypatch, tmp_path):
    """没有 per-agent 身份的会话：仍回落进程级那一份（行为一字不动）。"""
    from plobi_cli.agent_identity import get_active_profile_name

    from agent.agent_init import _memory_provider_identity

    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path))
    assert _memory_provider_identity() == get_active_profile_name()
