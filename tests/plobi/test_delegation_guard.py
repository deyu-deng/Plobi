"""B4 — L3 delegation guard tests.

Covers the three behaviours that matter: the process-wide concurrency ceiling
(overflow queues, then degrades), the quota breaker seam, and the subagent
lifecycle feed whose shape must match the §5 contract.

Determinism notes:
* Timeout paths use the real monotonic clock with ~50ms budgets — a fake clock
  cannot work there because ``Condition.wait`` sleeps in real time, so the
  deadline would never be reached.
* TTL expiry uses an injected fake clock and only calls paths that never wait.
* Singletons are reset around every test; ``config.load`` is monkeypatched
  rather than relying on whatever config.yaml happens to say.
"""

from __future__ import annotations

import importlib.util
import os
import threading
import time
from pathlib import Path

import pytest

from plobi.delegation import config, guard as guard_mod, quota as quota_mod
from plobi.delegation import tracker as tracker_mod

PLUGIN_INIT = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "plobi-delegation-guard"
    / "__init__.py"
)


def _load_plugin():
    """Load by path: plugin dirs carry hyphens and are not importable packages."""
    spec = importlib.util.spec_from_file_location(
        "plobi_delegation_guard_plugin", PLUGIN_INIT
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Isolate config resolution and reset process-wide singletons.

    ``PLOBI_IGNORE_USER_CONFIG`` suppresses whatever config.yaml says (that is
    the loader's own contract) so these tests assert on DEFAULTS/env rather
    than on the developer's machine. Note we do NOT stub ``config.load``
    here — the config tests need the real resolution path.
    """
    for key in list(os.environ):
        if key.startswith(config._ENV_PREFIX):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PLOBI_IGNORE_USER_CONFIG", "1")
    yield
    guard_mod.set_guard(None)
    quota_mod.set_quota_circuit(None)
    tracker_mod.set_tracker(None)


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


def test_config_defaults_are_sane():
    cfg = config.load()
    assert cfg["global_max_children"] == 6
    assert cfg["queue_timeout_seconds"] == 30.0
    assert cfg["guard_enabled"] is True
    assert cfg["quota_daily_budget_usd"] == 5.0


def test_config_env_overrides(monkeypatch):
    monkeypatch.setenv("PLOBI_DELEGATION_GLOBAL_MAX_CHILDREN", "3")
    monkeypatch.setenv("PLOBI_DELEGATION_QUOTA_FAIL_OPEN", "yes")
    cfg = config.load()
    assert cfg["global_max_children"] == 3
    assert cfg["quota_fail_open"] is True


def test_config_coercion_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("PLOBI_DELEGATION_GLOBAL_MAX_CHILDREN", "not-a-number")
    cfg = config.load()
    assert cfg["global_max_children"] == config.DEFAULTS["global_max_children"]


def test_config_floors_the_cap(monkeypatch):
    monkeypatch.setenv("PLOBI_DELEGATION_GLOBAL_MAX_CHILDREN", "0")
    # A cap below 1 would stall every delegation.
    assert config.load()["global_max_children"] == 1


# ---------------------------------------------------------------------------
# guard — concurrency ceiling
# ---------------------------------------------------------------------------


def test_acquire_grants_up_to_cap():
    g = guard_mod.DelegationGuard(cap=2, queue_timeout=0.05)
    assert g.acquire(requested=1).granted is True
    assert g.acquire(requested=1).granted is True
    third = g.acquire(requested=1, timeout=0.05)
    assert third.granted is False
    assert third.reason == "concurrency-cap"


def test_release_frees_a_slot():
    g = guard_mod.DelegationGuard(cap=1, queue_timeout=0.05)
    first = g.acquire(requested=1, session_id="s1", turn_id="t1")
    assert first.granted is True
    assert g.acquire(requested=1, timeout=0.05).granted is False

    assert g.release_for("s1", "t1") == 1
    assert g.acquire(requested=1).granted is True


def test_over_cap_request_is_clamped_not_refused():
    """Count is uncapped by ruling — asking for more than the cap is legal."""
    g = guard_mod.DelegationGuard(cap=2, queue_timeout=0.05)
    admission = g.acquire(requested=10)
    assert admission.granted is True
    assert admission.slots == 2  # clamped to the cap, never refused


def test_five_concurrent_delegations_run_at_the_cap_and_queue_the_rest():
    """Acceptance drill: cap=2, five callers → 2 run, 3 queue out."""
    g = guard_mod.DelegationGuard(cap=2, queue_timeout=0.05)
    results = {}
    barrier = threading.Barrier(5)

    def worker(index: int) -> None:
        barrier.wait()
        results[index] = g.acquire(
            requested=1, session_id=f"s{index}", timeout=0.05
        )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    granted = [index for index, adm in results.items() if adm.granted]
    degraded = [index for index, adm in results.items() if not adm.granted]
    assert len(granted) == 2, results
    assert len(degraded) == 3, results
    assert g.stats()["active"] == 2

    # Queued work is not lost — once the runners finish, the rest get through.
    for index in granted:
        g.release_for(f"s{index}")
    assert g.acquire(requested=1, timeout=0.05).granted is True


def test_no_head_of_line_blocking():
    """A large waiter must not strand a smaller one behind it."""
    g = guard_mod.DelegationGuard(cap=3, queue_timeout=0.3)
    g.acquire(requested=1, session_id="held")
    g.acquire(requested=1, session_id="held")
    # 2 of 3 slots taken. A waiter wanting 3 cannot fit; one wanting 1 can.
    big = {}
    small = {}

    big_thread = threading.Thread(
        target=lambda: big.update(
            adm=g.acquire(requested=3, session_id="big", timeout=0.3)
        )
    )
    big_thread.start()
    time.sleep(0.05)  # let the big waiter register first

    small_thread = threading.Thread(
        target=lambda: small.update(
            adm=g.acquire(requested=1, session_id="small", timeout=0.3)
        )
    )
    small_thread.start()
    small_thread.join()
    big_thread.join()

    assert small["adm"].granted is True
    assert big["adm"].granted is False


def test_lease_ttl_reclaims_abandoned_slots():
    clock = FakeClock()
    g = guard_mod.DelegationGuard(cap=1, queue_timeout=0.05, lease_ttl=10.0, clock=clock)
    assert g.acquire(requested=1, session_id="s1").granted is True

    # State check rather than a blocking acquire: under a fake clock the wait
    # deadline never approaches (Condition.wait sleeps in real time), so an
    # un-grantable acquire here would spin forever.
    clock.advance(5)
    assert g.active == 1  # still held

    clock.advance(10)  # past the TTL: the child never reported back
    # acquire reaps expired leases before deciding, so this is grantable.
    assert g.acquire(requested=1, session_id="s2").granted is True
    assert g.active == 1


def test_release_session_drops_every_lease():
    g = guard_mod.DelegationGuard(cap=4, queue_timeout=0.05)
    g.acquire(requested=2, session_id="s1", turn_id="t1")
    g.acquire(requested=1, session_id="s1", turn_id="t2")
    assert g.active == 3

    freed = g.release_session("s1")
    assert freed == 3
    assert g.active == 0


def test_release_for_unknown_session_is_a_noop():
    g = guard_mod.DelegationGuard(cap=2, queue_timeout=0.05)
    g.acquire(requested=1, session_id="s1")
    assert g.release_for("nope") == 0
    assert g.active == 1


def test_stats_report_capacity():
    g = guard_mod.DelegationGuard(cap=3, queue_timeout=1.0)
    g.acquire(requested=2, session_id="s1")
    stats = g.stats()
    assert stats["cap"] == 3
    assert stats["active"] == 2
    assert stats["available"] == 1


# ---------------------------------------------------------------------------
# quota
# ---------------------------------------------------------------------------


def test_unknown_quota_never_blocks():
    circuit = quota_mod.QuotaCircuit([])
    verdict = circuit.check()
    assert verdict.allowed is True
    assert verdict.remaining is None


def test_budget_provider_trips_and_stays_tripped():
    provider = quota_mod.BudgetProvider(1.0)
    circuit = quota_mod.QuotaCircuit([provider])

    assert circuit.check().allowed is True
    provider.record(1.0)

    tripped = circuit.check()
    assert tripped.allowed is False
    assert "额度耗尽" in tripped.reason
    # Sticky: still refused on the next check even without new spend.
    assert circuit.check().allowed is False
    assert circuit.tripped is True

    circuit.reset()
    provider.reset()
    assert circuit.check().allowed is True


def test_worst_provider_wins():
    generous = quota_mod.BudgetProvider(10.0)
    thin = quota_mod.BudgetProvider(0.5)
    circuit = quota_mod.QuotaCircuit([generous, thin])
    verdict = circuit.check()
    assert verdict.allowed is True
    assert verdict.remaining == 0.5
    assert verdict.source == "budget"


def test_record_spend_fans_out_to_providers():
    provider = quota_mod.BudgetProvider(2.0)
    circuit = quota_mod.QuotaCircuit([provider])
    circuit.record_spend(0.75)
    assert circuit.check().remaining == pytest.approx(1.25)


def test_broken_provider_is_skipped_not_fatal():
    class Boom:
        name = "boom"

        def remaining_usd(self):
            raise RuntimeError("probe down")

    circuit = quota_mod.QuotaCircuit([Boom()])
    # A failing probe must not block delegation; it reports no data.
    assert circuit.check().allowed is True


# ---------------------------------------------------------------------------
# tracker — §5 shape
# ---------------------------------------------------------------------------


def test_start_marks_working_and_stop_returns_to_idle():
    tracker = tracker_mod.SubagentTracker()
    tracker.on_start(
        child_session_id="sess-1",
        child_subagent_id="sub-1",
        parent_session_id="parent-1",
        child_goal="提取昨日消息",
    )
    # id is the session id: it is the only identifier both hooks carry, and
    # subagent_stop omits child_subagent_id entirely.
    assert tracker.subagents("parent-1") == [
        {"id": "sess-1", "name": "提取昨日消息", "status": "working"}
    ]

    tracker.on_stop(child_session_id="sess-1", child_status="completed")
    assert tracker.subagents("parent-1")[0]["status"] == "idle"


def test_failed_child_reports_error():
    tracker = tracker_mod.SubagentTracker()
    tracker.on_start(child_session_id="s2", child_subagent_id="sub-2")
    tracker.on_stop(child_session_id="s2", child_status="timeout")
    assert tracker.subagents("")[0]["status"] == "error"


def test_session_binding_maps_to_l2_agent_id():
    tracker = tracker_mod.SubagentTracker()
    tracker.bind_session("runtime-session", "agenda-secretary")
    tracker.on_start(
        child_session_id="s3",
        child_subagent_id="sub-3",
        parent_session_id="runtime-session",
        child_goal="生成提醒",
    )
    # The workbench asks by agent id, hooks only carry a session id.
    assert tracker.subagents("agenda-secretary")[0]["id"] == "s3"
    assert tracker.subagents("runtime-session") == []


def test_payload_has_exactly_the_contract_fields():
    tracker = tracker_mod.SubagentTracker()
    tracker.on_start(child_session_id="s4", child_subagent_id="sub-4", child_goal="x")
    row = tracker.subagents("")[0]
    assert set(row) == {"id", "name", "status"}
    assert row["status"] in tracker_mod.AGENT_STATUSES


def test_long_goal_is_truncated_for_the_left_rail():
    tracker = tracker_mod.SubagentTracker()
    tracker.on_start(
        child_session_id="s5",
        child_subagent_id="sub-5",
        child_goal="这是一个非常非常非常长的目标描述确实需要被截断掉才能显示在左栏",
    )
    name = tracker.subagents("")[0]["name"]
    assert len(name) == 25  # 24 chars of goal + the ellipsis
    assert name.endswith("…")


def test_awaiting_approval_is_explicit_opt_in():
    tracker = tracker_mod.SubagentTracker()
    tracker.on_start(child_session_id="s6", child_subagent_id="sub-6")
    # The subagent hooks carry no approval signal, so idle stays the default.
    assert tracker.subagents("")[0]["status"] == "working"
    tracker.mark_awaiting_approval("s6")
    assert tracker.subagents("")[0]["status"] == "awaiting_approval"


def test_clear_session_removes_children():
    tracker = tracker_mod.SubagentTracker()
    tracker.on_start(child_session_id="s7", child_subagent_id="sub-7", parent_session_id="p7")
    assert tracker.clear_session("p7") == 1
    assert tracker.subagents("p7") == []


# ---------------------------------------------------------------------------
# plugin wiring
# ---------------------------------------------------------------------------


def test_plugin_ignores_other_tools():
    plugin = _load_plugin()
    assert plugin.on_pre_tool_call(tool_name="write_file", args={}) is None


def test_plugin_allows_delegation_under_the_cap():
    plugin = _load_plugin()
    guard_mod.set_guard(guard_mod.DelegationGuard(cap=2, queue_timeout=0.05))
    quota_mod.set_quota_circuit(quota_mod.QuotaCircuit([]))

    assert plugin.on_pre_tool_call(
        tool_name="delegate_task", args={"goal": "a"}, session_id="s1"
    ) is None


def test_plugin_degrades_to_a_block_message_when_saturated():
    plugin = _load_plugin()
    guard_mod.set_guard(guard_mod.DelegationGuard(cap=1, queue_timeout=0.05))
    quota_mod.set_quota_circuit(quota_mod.QuotaCircuit([]))

    assert plugin.on_pre_tool_call(
        tool_name="delegate_task", args={"goal": "a"}, session_id="s1"
    ) is None

    directive = plugin.on_pre_tool_call(
        tool_name="delegate_task", args={"goal": "b"}, session_id="s2"
    )
    assert directive is not None
    assert directive["action"] == "block"
    assert "全局上限" in directive["message"]
    assert directive["message"]  # block directives require a message


def test_plugin_counts_batch_tasks_as_children():
    assert _load_plugin().requested_children({"tasks": [{"goal": "a"}, {"goal": "b"}]}) == 2
    assert _load_plugin().requested_children({"goal": "solo"}) == 1
    assert _load_plugin().requested_children(None) == 1


def test_plugin_blocks_on_quota_breaker():
    plugin = _load_plugin()
    guard_mod.set_guard(guard_mod.DelegationGuard(cap=5, queue_timeout=0.05))
    provider = quota_mod.BudgetProvider(1.0)
    provider.record(1.0)
    quota_mod.set_quota_circuit(quota_mod.QuotaCircuit([provider]))

    directive = plugin.on_pre_tool_call(
        tool_name="delegate_task", args={"goal": "a"}, session_id="s1"
    )
    assert directive is not None
    assert directive["action"] == "block"
    assert "额度熔断" in directive["message"]


def test_quota_fail_open_lets_work_through(monkeypatch):
    plugin = _load_plugin()
    guard_mod.set_guard(guard_mod.DelegationGuard(cap=5, queue_timeout=0.05))
    provider = quota_mod.BudgetProvider(1.0)
    provider.record(1.0)
    quota_mod.set_quota_circuit(quota_mod.QuotaCircuit([provider]))

    cfg = config.load()
    cfg["quota_fail_open"] = True
    monkeypatch.setattr(config, "load", lambda: cfg)

    assert plugin.on_pre_tool_call(
        tool_name="delegate_task", args={"goal": "a"}, session_id="s1"
    ) is None


def test_guard_disabled_never_blocks(monkeypatch):
    plugin = _load_plugin()
    guard_mod.set_guard(guard_mod.DelegationGuard(cap=1, queue_timeout=0.05))
    guard_mod.get_guard().acquire(requested=1, session_id="s1")  # saturate

    cfg = config.load()
    cfg["guard_enabled"] = False
    monkeypatch.setattr(config, "load", lambda: cfg)

    assert plugin.on_pre_tool_call(
        tool_name="delegate_task", args={"goal": "a"}, session_id="s2"
    ) is None


def test_lifecycle_hooks_feed_the_left_rail_and_free_slots():
    plugin = _load_plugin()
    tracker = tracker_mod.SubagentTracker()
    tracker_mod.set_tracker(tracker)
    g = guard_mod.DelegationGuard(cap=1, queue_timeout=0.05)
    guard_mod.set_guard(g)
    quota_mod.set_quota_circuit(quota_mod.QuotaCircuit([]))

    tracker.bind_session("parent-session", "agenda-secretary")
    plugin.on_pre_tool_call(
        tool_name="delegate_task", args={"goal": "g"}, session_id="parent-session", turn_id="t1"
    )
    plugin.on_subagent_start(
        parent_session_id="parent-session",
        parent_turn_id="t1",
        child_session_id="child-1",
        child_subagent_id="sub-1",
        child_role="leaf",
        child_goal="跑一个子任务",
    )

    assert g.active == 1
    assert tracker_mod.subagents_for_agent("agenda-secretary") == [
        {"id": "child-1", "name": "跑一个子任务", "status": "working"}
    ]

    plugin.on_subagent_stop(
        parent_session_id="parent-session",
        parent_turn_id="t1",
        child_session_id="child-1",
        child_status="completed",
    )

    assert g.active == 0  # slot returned, queued work can proceed
    assert tracker_mod.subagents_for_agent("agenda-secretary")[0]["status"] == "idle"


def test_session_end_reclaims_slots():
    plugin = _load_plugin()
    g = guard_mod.DelegationGuard(cap=2, queue_timeout=0.05)
    guard_mod.set_guard(g)
    quota_mod.set_quota_circuit(quota_mod.QuotaCircuit([]))

    plugin.on_pre_tool_call(
        tool_name="delegate_task", args={"goal": "g"}, session_id="s1", turn_id="t1"
    )
    assert g.active == 1

    plugin.on_session_end(session_id="s1")
    assert g.active == 0


def test_plugin_register_declares_all_hooks():
    plugin = _load_plugin()
    registered = []

    class Ctx:
        def register_hook(self, name, callback):
            registered.append(name)

    plugin.register(Ctx())
    assert set(registered) == {
        "pre_tool_call",
        "subagent_start",
        "subagent_stop",
        "on_session_end",
    }
