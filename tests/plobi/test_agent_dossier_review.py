"""WP-DOSSIER-AUTO-MAINTAIN: 后台档案维护 review 的行为测试。

三条线：
  1. prompt 与白名单——教的能力必须真存在（P6），且只碰自己那份 dossier；
  2. 触发计数——每 N 轮一次、只对该位（拿到 dossier 工具）生效、L1 不进；
  3. 隔离——fork 不写用户会话、不压缩、白名单只放 dossier 五把。

只测行为，不快照整段 prompt（避免 change-detector 假红）。
"""

from types import SimpleNamespace

import pytest

from agent import dossier_review as dr
from agent.turn_finalizer import finalize_turn


# ---------------------------------------------------------------------------
# 1. prompt 与工具白名单
# ---------------------------------------------------------------------------

def test_prompt_reads_own_dossier_first_and_refuses_guessing():
    p = dr.DOSSIER_REVIEW_PROMPT
    assert "dossier_read" in p
    assert "dossier_write" in p
    assert "mind_subtree" in p.lower() or "your identity" in p.lower()
    assert "never guess" in p.lower() or "stop and say so" in p.lower()


def test_prompt_only_names_four_fields_and_forbids_tasks_overwrite():
    p = dr.DOSSIER_REVIEW_PROMPT
    for field in ("goal", "status", "stages", "log_append"):
        assert field in p
    assert "tasks.json" in p
    assert "never overwrite" in p.lower() or "do not" in p.lower()


def test_prompt_requires_nothing_to_save_when_unchanged():
    p = dr.DOSSIER_REVIEW_PROMPT
    assert "Nothing to save" in p


def test_whitelist_is_exactly_the_five_dossier_tools():
    expected = {
        "dossier_read", "dossier_write", "dossier_list",
        "dossier_task_toggle", "dossier_stage_update",
    }
    assert set(dr._DOSSIER_TOOLS) == expected


def test_fork_runtime_falls_back_safely_without_runtime_method():
    agent = SimpleNamespace(provider="p", model="m", request_overrides={})
    rt = dr._fork_runtime(agent)
    assert rt["provider"] == "p"
    assert rt["model"] == "m"
    assert rt["api_key"] is None
    assert rt["api_mode"] is None


def test_fork_runtime_downgrades_codex_app_server():
    agent = SimpleNamespace(
        provider="p", model="m", request_overrides={},
        _current_main_runtime=lambda: {
            "api_mode": "codex_app_server", "api_key": "k", "base_url": "u",
        },
    )
    rt = dr._fork_runtime(agent)
    assert rt["api_mode"] == "codex_responses"
    assert rt["api_key"] == "k"


# ---------------------------------------------------------------------------
# 2. 触发计数（monkeypatch spawn，不起真线程）
# ---------------------------------------------------------------------------

def _stub_agent(*, has_dossier=True, interval=3):
    a = SimpleNamespace(
        valid_tool_names=["dossier_read", "dossier_write"] if has_dossier else [],
        _dossier_nudge_interval=interval,
        _turns_since_dossier=0,
        max_iterations=1,
        iteration_budget=SimpleNamespace(used=1, max_total=1, remaining=0),
        context_compressor=SimpleNamespace(last_prompt_tokens=0),
        model="stub", provider="stub", base_url="", session_id="s",
        quiet_mode=True, platform="cli",
        _interrupt_requested=False, _interrupt_message=None,
        _tool_guardrail_halt_decision=None, _response_was_previewed=False,
        _skill_nudge_interval=0, _iters_since_skill=0,
        _memory_nudge_interval=0,
        session_cost_status="ok", session_cost_source="stub",
    )
    for attr in (
        "session_input_tokens", "session_output_tokens", "session_cache_read_tokens",
        "session_cache_write_tokens", "session_reasoning_tokens", "session_prompt_tokens",
        "session_completion_tokens", "session_total_tokens", "session_estimated_cost_usd",
    ):
        setattr(a, attr, 0)
    # finalize_turn 读到的可选面全给空实现
    for name in (
        "_sync_external_memory_for_turn", "_save_trajectory", "_cleanup_task_resources",
        "_drop_trailing_empty_response_scaffolding", "clear_interrupt",
        "_persist_session", "_drain_pending_steer", "_emit_auxiliary_failure",
    ):
        setattr(a, name, lambda *args, **kwargs: None)
    a._should_review_memory = False
    return a


def _run_finalize(agent):
    return finalize_turn(
        agent,
        final_response="done",
        api_call_count=1,
        interrupted=False,
        failed=False,
        messages=[{"role": "user", "content": "hi"}],
        conversation_history=[],
        effective_task_id=None,
        turn_id="t1",
        user_message="hi",
        original_user_message="hi",
        _should_review_memory=False,
        _turn_exit_reason="done",
    )


def test_dossier_review_fires_every_n_turns(monkeypatch):
    calls = []
    monkeypatch.setattr(dr, "spawn_dossier_review", lambda a, m: calls.append(1))
    a = _stub_agent(interval=3)
    _run_finalize(a)
    assert a._turns_since_dossier == 1 and not calls
    _run_finalize(a)
    assert a._turns_since_dossier == 2 and not calls
    _run_finalize(a)
    assert a._turns_since_dossier == 0 and len(calls) == 1


def test_dossier_review_skips_agent_without_dossier_tool(monkeypatch):
    calls = []
    monkeypatch.setattr(dr, "spawn_dossier_review", lambda a, m: calls.append(1))
    a = _stub_agent(has_dossier=False, interval=1)
    _run_finalize(a)
    assert not calls  # L1 指挥拿不到 dossier 工具，绝不 fork


def test_dossier_review_disabled_when_interval_zero(monkeypatch):
    calls = []
    monkeypatch.setattr(dr, "spawn_dossier_review", lambda a, m: calls.append(1))
    a = _stub_agent(interval=0)
    _run_finalize(a)
    assert not calls


def test_dossier_review_skips_when_interrupted(monkeypatch):
    calls = []
    monkeypatch.setattr(dr, "spawn_dossier_review", lambda a, m: calls.append(1))
    a = _stub_agent(interval=1)
    finalize_turn(
        a, final_response="", api_call_count=1, interrupted=True, failed=False,
        messages=[], conversation_history=[], effective_task_id=None, turn_id="t",
        user_message="hi", original_user_message="hi", _should_review_memory=False,
        _turn_exit_reason="interrupt",
    )
    assert not calls


def test_spawn_failure_never_breaks_turn(monkeypatch):
    def boom(a, m):
        raise RuntimeError("spawn failed")
    monkeypatch.setattr(dr, "spawn_dossier_review", boom)
    a = _stub_agent(interval=1)
    result = _run_finalize(a)  # 不抛
    assert result is not None
