"""The engine watchdog (WP-EGRESS-ROUTING §39): keep the engine up while the machine needs it.

10-29 这台机器上引擎一天掉两次，两次都没有任何东西把它拉起来——系统代理还指着那个端口，
表现就是"没网"。这一档钉住三件事：**该拉的时候拉**、**不该动的时候不动**、**别人的代理不抢**。
"""

from __future__ import annotations

import pytest

from agent.egress import engine, watchdog


class _Table:
    def __init__(self, on: bool):
        self.system_proxy_enabled = on


@pytest.fixture
def fakes(monkeypatch):
    """engine.status / engine.ensure replaced by recorders."""
    import types

    state = {"state": engine.OURS, "ensured": 0, "ours": True, "taken": 0}

    monkeypatch.setattr(engine, "status", lambda table, **kw: engine.EngineReport(state["state"], "fake"))
    from agent.egress import system_proxy

    monkeypatch.setattr(
        system_proxy, "status",
        lambda table, **kw: {"declared": bool(table.system_proxy_enabled), "ours": state["ours"]},
    )
    monkeypatch.setattr(
        system_proxy, "enable",
        lambda table, **kw: (state.__setitem__("taken", state["taken"] + 1), types.SimpleNamespace(ok=True))[1],
    )
    monkeypatch.setattr(engine, "ensure", lambda table, **kw: (state.__setitem__("ensured", state["ensured"] + 1),
                                                              engine.EngineReport(engine.OURS, "fake"))[1])
    return state


def test_a_healthy_engine_is_left_alone(fakes):
    assert watchdog.tick(_Table(True)) == "healthy"
    assert fakes["ensured"] == 0, "健康的不许重启（裁定 102：一次故障不能变成循环）"


@pytest.mark.parametrize("state", [engine.STOPPED, engine.FAILED])
def test_a_dead_engine_is_pulled_back_up(fakes, state):
    fakes["state"] = state
    assert watchdog.tick(_Table(True)) == "restarted"
    assert fakes["ensured"] == 1


def test_a_foreign_proxy_on_the_port_is_not_fought(fakes):
    fakes["state"] = engine.FOREIGN
    assert watchdog.tick(_Table(True)) == "gave-up"
    assert fakes["ensured"] == 0, "端口上是别人的代理，看门狗不许去抢"


def test_nothing_depends_on_us_when_the_takeover_is_off(fakes):
    fakes["state"] = engine.STOPPED
    assert watchdog.tick(_Table(False)) == "idle"
    assert fakes["ensured"] == 0, "表没接管整机 ⇒ 引擎死活与我们无关"


def test_the_ownership_check_needs_a_matching_command_line_and_time(monkeypatch):
    class FakeProcess:
        def __init__(self, cmd, created):
            self._cmd = cmd
            self._created = created

        def cmdline(self):
            return self._cmd

        def create_time(self):
            return self._created

    rec = {"pid": 11, "created": 100.0}

    class P1:
        @staticmethod
        def Process(pid):
            return FakeProcess(["python", "-m", "agent.egress.watchdog"], 100.0)

    import sys
    import types

    monkeypatch.setitem(sys.modules, "psutil", types.SimpleNamespace(Process=P1.Process))
    ok, why = watchdog._pid_is_ours(rec)
    assert ok, why

    class P2:
        @staticmethod
        def Process(pid):
            return FakeProcess(["python", "-m", "agent.egress.watchdog"], 999.0)

    monkeypatch.setitem(sys.modules, "psutil", types.SimpleNamespace(Process=P2.Process))
    ok, why = watchdog._pid_is_ours(rec)
    assert not ok and "创建时间" in why, why


def test_stopping_kills_the_children_too(monkeypatch):
    order: list[tuple[str, int]] = []

    class FakeProc:
        def __init__(self, pid, kids=(), stubborn=False):
            self.pid = pid
            self._kids = list(kids)
            self.running = True
            self.stubborn = stubborn

        def children(self, recursive=False):
            return list(self._kids)

        def terminate(self):
            order.append(("terminate", self.pid))
            if not self.stubborn:
                self.running = False

        def is_running(self):
            return self.running

        def kill(self):
            order.append(("kill", self.pid))

    child = FakeProc(2, stubborn=True)
    parent = FakeProc(1, kids=[child])

    family = watchdog._terminate_tree(parent, types_simple())

    assert [p.pid for p in family] == [2, 1]
    assert ("kill", 2) in order, "赖着不走的子进程必须被 kill"


def types_simple():
    import types

    return types.SimpleNamespace(wait_procs=lambda procs, timeout=0: ([], []))

def test_a_takeover_that_went_missing_is_taken_back(fakes):
    """10-31 实测：ikuuu 退出时把注册表还原成它自己的值，我们的接管静默消失 ⇒ 整机直连 15 小时。"""
    fakes["ours"] = False
    assert watchdog.tick(_Table(True)) == "retaken"
    assert fakes["taken"] == 1
    assert fakes["ensured"] == 0, "引擎健康，不该因为接管丢了就重启引擎"


def test_a_healthy_takeover_is_left_alone(fakes):
    assert watchdog.tick(_Table(True)) == "healthy"
    assert fakes["taken"] == 0
