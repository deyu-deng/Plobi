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

    state = {"state": engine.OURS, "ensured": 0, "ours": True, "taken": 0, "handed_back": 0}

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
    monkeypatch.setattr(
        system_proxy, "disable",
        lambda **kw: (state.__setitem__("handed_back", state["handed_back"] + 1), (True, "交还 fake"))[1],
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


# ── 谁来看门狗（11-02：它 10:06 被外力杀掉，之后三小时没人挂回去）──────────────

class _WideTable:
    def __init__(self, proxy: bool = False, tun: bool = False):
        self.system_proxy_enabled = proxy
        self.engine_tun = {"enabled": tun}


def test_the_supervisor_does_not_fork_itself(monkeypatch):
    """看门狗每 tick 都调 `engine.ensure`——那条路若也负责挂监督者，就会给自己生一堆兄弟。"""
    monkeypatch.setenv(watchdog.MARKER, "1")
    started: list = []
    monkeypatch.setattr(watchdog, "start", lambda **kw: started.append(kw) or (True, "started"))

    ok, why = watchdog.ensure_running()

    assert ok is True and started == [] and "不 spawn" in why


def test_arming_outside_the_child_calls_start_with_the_same_home(monkeypatch):
    monkeypatch.delenv(watchdog.MARKER, raising=False)
    started: list = []
    monkeypatch.setattr(watchdog, "start", lambda **kw: (started.append(kw), (True, "ok"))[1])

    assert watchdog.ensure_running(home="H")[0] is True
    assert started == [{"home": "H", "interval": watchdog.INTERVAL_SECONDS}]


def test_a_table_that_takes_over_nothing_starts_no_watcher(monkeypatch):
    calls: list = []
    monkeypatch.setattr(watchdog, "ensure_running", lambda **kw: calls.append(kw) or (True, ""))

    engine._arm_supervisor(_WideTable())
    assert calls == [], "没东西要监督，就别起一个每 20 秒读注册表的进程"

    engine._arm_supervisor(_WideTable(proxy=True))
    engine._arm_supervisor(_WideTable(tun=True))
    assert len(calls) == 2, "两种整机承载都算「有东西要监督」"


def test_an_already_running_engine_still_arms_its_supervisor(monkeypatch, tmp_path):
    """那三小时的形状：引擎活着、接管早就生效 ⇒ `ensure()` 走"什么都不做"的早退分支，
    而看门狗已经没了。早退现在也要问一句。"""
    calls: list = []
    monkeypatch.setattr(engine, "_arm_supervisor", lambda table, **kw: calls.append(kw.get("home")))
    snap = engine._Snapshot(True, "mihomo", 7895, None, None, "compiled", {}, ())
    monkeypatch.setattr(engine, "snapshot", lambda table, **kw: snap)
    monkeypatch.setattr(engine, "status", lambda table, **kw: engine.EngineReport(engine.OURS, "fake"))

    report = engine.ensure(_WideTable(proxy=True), home=tmp_path)

    assert report.state == engine.OURS
    assert calls == [tmp_path]


def test_a_failing_supervisor_never_breaks_the_engine_path(monkeypatch):
    """监督者起不来是一句警告，不是把「引擎已就绪」这条读数改成失败。"""
    def boom(**kw):
        raise OSError("spawn denied")

    monkeypatch.setattr(watchdog, "ensure_running", boom)
    engine._arm_supervisor(_WideTable(proxy=True))



class _EngineTable:
    """A table that can describe an engine: here `engine_enabled` is the switch that matters."""

    def __init__(self, *, engine_on: bool, takeover: bool) -> None:
        self.system_proxy_enabled = takeover
        self.engine_enabled = engine_on
        self.engine_mixed_port = 7896


def test_a_switched_off_engine_is_stopped_even_when_nothing_depends_on_it(fakes, monkeypatch):
    """两个开关各管一段：系统代理关着，不代表那个已被关掉的引擎可以继续活着。"""
    stopped = []
    monkeypatch.setattr(engine, "stop_ours", lambda table, **kw: stopped.append(table))

    assert watchdog.tick(_EngineTable(engine_on=False, takeover=False)) == "idle"
    assert len(stopped) == 1, "没有任何路径会再来管它，所以就在这里停"


def test_an_off_state_cleans_up_a_leftover_before_idling(fakes, monkeypatch):
    """状态 OFF 只说明「表里关了」；盘上还活着的那个进程得有人收尾。"""
    stopped = []
    monkeypatch.setattr(engine, "stop_ours", lambda table, **kw: stopped.append(table))
    fakes["state"] = engine.OFF

    assert watchdog.tick(_EngineTable(engine_on=False, takeover=True)) == "idle"
    assert len(stopped) == 1


def test_a_running_engine_is_never_stopped_by_the_cleanup(fakes, monkeypatch):
    """收尾不许误伤：引擎开着、状态健康，清理路径一次都不许碰它。"""
    stopped = []
    monkeypatch.setattr(engine, "stop_ours", lambda table, **kw: stopped.append(table))

    assert watchdog.tick(_EngineTable(engine_on=True, takeover=True)) == "healthy"
    assert stopped == []



def test_stopping_a_switched_off_engine_also_hands_the_registry_back(fakes, monkeypatch):
    """停进程只做了一半：注册表还指着那个空端口，全机照旧不联网（10-29 现场）。"""
    monkeypatch.setattr(engine, "stop_ours", lambda table, **kw: engine.EngineReport(engine.STOPPED, "stopped"))
    fakes["state"] = engine.OFF

    assert watchdog.tick(_EngineTable(engine_on=False, takeover=True)) == "idle"
    assert fakes["handed_back"] == 1, "引擎停了，注册表也得跟着交还"


def test_a_registry_that_is_not_ours_is_left_alone(fakes, monkeypatch):
    """交还只针对我们写进去的那一份，别人的设置一概不碰。"""
    monkeypatch.setattr(engine, "stop_ours", lambda table, **kw: engine.EngineReport(engine.STOPPED, "stopped"))
    fakes["state"] = engine.OFF
    fakes["ours"] = False

    assert watchdog.tick(_EngineTable(engine_on=False, takeover=True)) == "idle"
    assert fakes["handed_back"] == 0
