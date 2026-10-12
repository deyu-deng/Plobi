"""Supervision of the optional egress engine (WP-EGRESS-ROUTING).

Nothing here starts a real process or touches the network: the three things that
would are seams — ``_spawn``, ``listening`` and ``probe_engine`` — and every test
drives them.
"""

from __future__ import annotations

import json
import urllib.request

import pytest
import yaml

from agent.egress import engine
from agent.egress.engine import FAILED, FOREIGN, OFF, OURS, STOPPED, ensure, probe_engine, snapshot, status, stop_ours
from agent.egress.rules import load_table

_PORT = 7896


def _table(*, include=(), enabled=True, port=_PORT, binary=""):
    return load_table(
        config={
            "network": {
                "egress": {
                    "proxies": {"front": {"url": f"http://127.0.0.1:{port + 1}"}},
                    "rules": [{"match": "*", "via": "proxy:front"}],
                    "engine": {
                        "enabled": enabled,
                        "binary": binary,
                        "mixed_port": port,
                        "include_files": list(include),
                    },
                }
            }
        }
    )


@pytest.fixture
def pool(tmp_path):
    """A node pool the way a user keeps one: its own file, its own port."""
    d = tmp_path / "egress-pool"
    d.mkdir()
    cfg = d / "config.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "mixed-port": _PORT,
                "mode": "rule",
                "proxies": [{"name": "n1", "type": "ss", "server": "1.2.3.4", "port": 8388}],
                "rules": ["MATCH,n1"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return cfg


@pytest.fixture
def fake_binary(tmp_path):
    b = tmp_path / "mihomo.exe"
    b.write_text("not really a binary", encoding="utf-8")
    return str(b)


@pytest.fixture
def inert(monkeypatch):
    """Neutralise the side-effecting seams and record what they were asked to do."""
    state = {"spawn": [], "up": set(), "kill": []}
    monkeypatch.setattr(engine, "listening", lambda port, **kw: port in state["up"])
    monkeypatch.setattr(
        engine,
        "probe_engine",
        lambda port, **kw: (True, f"204 via http://127.0.0.1:{port}")
        if port in state["up"]
        else (False, "no listener"),
    )

    def _spawn(snap, log_path):
        state["spawn"].append([snap.binary, "-d", str(snap.workdir)])
        state["up"].add(snap.port)
        return 4242, 1000.0

    monkeypatch.setattr(engine, "_spawn", _spawn)
    return state


class FakePsutil:
    """Only the surface ``_pid_is_ours`` and ``stop_ours`` use.

    ``up``/``port`` are wired in so ``terminate`` also frees the listen port:
    otherwise the fake keeps answering "somebody is home" after the process is
    gone, and `ensure` — correctly refusing to steal a port it does not own —
    turns the test into a false FOREIGN verdict.
    """

    def __init__(self, procs, up=None, port=None):
        self.procs = procs
        self.terminated = []
        self.up = up
        self.port = port

    def Process(self, pid):  # noqa: N802 - psutil's own spelling
        if pid not in self.procs:
            raise KeyError(pid)
        info = self.procs[pid]
        outer = self

        class _P:
            def name(self):
                return info["name"]

            def create_time(self):
                return info["created"]

            def terminate(self):
                outer.terminated.append(pid)
                if outer.up is not None:
                    outer.up.discard(outer.port)

            def wait(self, timeout=None):
                return 0

        return _P()


def _record(home, **over):
    rec = {
        "pid": 4242,
        "created": 1000.0,
        "binary": "D:/fake/mihomo.exe",
        "workdir": "",
        "mixed_port": _PORT,
        "config_sha": engine._sha(b"pool bytes"),
        "source": "pool",
        "started_by": "plobi",
    }
    rec.update(over)
    path = home / "egress" / engine.STATE_BASENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec), encoding="utf-8")
    return rec


def test_switched_off_never_spawns(monkeypatch, tmp_path, fake_binary):
    """The switch must have a consumer that can also refuse.

    ``engine.enabled`` used to be read by nothing at all, which is the gap this
    module closes; if ``ensure`` spawned regardless, turning the feature off
    would still leave Plobi starting processes.
    """
    spawned = []
    monkeypatch.setattr(engine, "_spawn", lambda *a, **k: spawned.append(a))
    assert ensure(_table(binary=fake_binary, enabled=False), home=tmp_path, wait_s=0).state == OFF
    assert ensure(_table(enabled=False), home=tmp_path, wait_s=0).state == OFF
    assert spawned == []
    assert not (tmp_path / "egress" / engine.STATE_BASENAME).exists()


def test_a_missing_binary_is_named_not_downloaded(inert, tmp_path):
    rep = ensure(_table(binary="D:/nowhere/mihomo.exe"), home=tmp_path, wait_s=0)
    assert rep.state == FAILED
    assert "not on disk" in rep.detail
    assert inert["spawn"] == []


def test_the_pool_dir_is_handed_to_the_engine_untouched(tmp_path, pool, fake_binary, inert):
    """Membership is a workdir, not a key we render (mihomo ignores ``include``)."""
    before = pool.read_bytes()
    home = tmp_path / "home"
    rep = ensure(_table(include=[str(pool)], binary=fake_binary), home=home, wait_s=0)
    assert rep.state == OURS, rep
    assert inert["spawn"] == [[fake_binary, "-d", str(pool.parent)]]
    assert rep.config_source == "pool"
    assert pool.read_bytes() == before
    rec = json.loads((home / "egress" / engine.STATE_BASENAME).read_text(encoding="utf-8"))
    assert rec["pid"] == 4242 and rec["mixed_port"] == _PORT and rec["source"] == "pool"


def test_a_foreign_listener_is_adopted_not_killed(monkeypatch, tmp_path, pool, fake_binary):
    """The hand-started engine on this box is exactly this case."""
    monkeypatch.setattr(engine, "listening", lambda port, **kw: True)
    monkeypatch.setattr(engine, "probe_engine", lambda port, **kw: (True, "204 via x"))
    spawned = []
    monkeypatch.setattr(engine, "_spawn", lambda *a, **k: spawned.append(a))
    home = tmp_path / "home"
    table = _table(include=[str(pool)], binary=fake_binary)
    assert ensure(table, home=home, wait_s=0).state == FOREIGN
    assert spawned == []
    assert stop_ours(table, home=home).state == STOPPED
    assert not (home / "egress" / engine.STATE_BASENAME).exists()


def test_a_recycled_pid_is_not_ours(monkeypatch, tmp_path, pool, fake_binary):
    """Windows reuses pids: name and creation time are what make one ours."""
    monkeypatch.setattr(engine, "listening", lambda port, **kw: True)
    monkeypatch.setattr(engine, "probe_engine", lambda port, **kw: (True, "204 via x"))
    home = tmp_path / "home"
    table = _table(include=[str(pool)], binary=fake_binary)
    _record(home, binary=fake_binary)

    monkeypatch.setattr(engine, "_psutil", lambda: FakePsutil({4242: {"name": "notepad.exe", "created": 1000.0}}))
    assert status(table, home=home).state == FOREIGN
    assert stop_ours(table, home=home).state == STOPPED

    # stopping a record we no longer trust is exactly when it gets dropped, so the
    # next two readings need a record of their own.
    _record(home, binary=fake_binary)

    monkeypatch.setattr(
        engine, "_psutil", lambda: FakePsutil({4242: {"name": "mihomo.exe", "created": 1900.0}})
    )
    assert status(table, home=home).state == FOREIGN

    monkeypatch.setattr(
        engine, "_psutil", lambda: FakePsutil({4242: {"name": "mihomo.exe", "created": 1000.0}})
    )
    assert status(table, home=home).state == OURS


def test_a_healthy_engine_is_never_restarted_for_its_own_sake(tmp_path, pool, fake_binary, inert, monkeypatch):
    """One outage must not become a loop (裁定 102: no silent retry)."""
    monkeypatch.setattr(engine, "_psutil", lambda: FakePsutil({4242: {"name": "mihomo.exe", "created": 1000.0}}))
    home = tmp_path / "home"
    table = _table(include=[str(pool)], binary=fake_binary)
    assert ensure(table, home=home, wait_s=0).state == OURS
    assert ensure(table, home=home, wait_s=0).state == OURS
    assert len(inert["spawn"]) == 1


def test_a_changed_config_reloads_only_what_plobi_started(tmp_path, pool, fake_binary, inert, monkeypatch):
    home = tmp_path / "home"
    table = _table(include=[str(pool)], binary=fake_binary)
    ensure(table, home=home, wait_s=0)
    rec_path = home / "egress" / engine.STATE_BASENAME
    rec = json.loads(rec_path.read_text(encoding="utf-8"))
    rec["config_sha"] = "0123456789abcdef"
    rec_path.write_text(json.dumps(rec), encoding="utf-8")
    fake = FakePsutil({4242: {"name": "mihomo.exe", "created": 1000.0}})
    monkeypatch.setattr(engine, "_psutil", lambda: fake)

    assert ensure(table, home=home, wait_s=0).state == OURS
    assert fake.terminated == [4242]
    assert len(inert["spawn"]) == 2


def test_table_and_pool_disagreeing_about_the_port_is_said_out_loud(tmp_path, pool, fake_binary, inert):
    """Spawned children get the table's number; the engine listens elsewhere."""
    pool.write_text(
        yaml.safe_dump({"mixed-port": 15000, "mode": "rule", "proxies": [], "rules": []}),
        encoding="utf-8",
    )
    snap = snapshot(_table(include=[str(pool)], binary=fake_binary), home=tmp_path)
    assert any("the pool config says" in n for n in snap.notes), snap.notes


def test_a_compiled_projection_is_validated_before_it_is_started(tmp_path, fake_binary, inert, monkeypatch):
    monkeypatch.setattr(
        engine, "verify_with_binary", lambda b, p: ("REJECTED", "proxy [front] resolves to itself")
    )
    home = tmp_path / "home"
    rep = ensure(_table(include=[], binary=fake_binary), home=home, wait_s=0)
    assert rep.state == FAILED
    assert rep.config_source == "compiled"
    assert inert["spawn"] == []
    assert (home / "egress" / engine.RUNTIME_BASENAME / "config.yaml").exists()


def test_a_pool_reference_that_does_not_resolve_is_refused(tmp_path, fake_binary, inert):
    home = tmp_path / "home"
    rep = ensure(_table(include=["D:/nowhere/egress/config.yaml"], binary=fake_binary), home=home, wait_s=0)
    assert rep.state == FAILED
    assert inert["spawn"] == []


def test_a_start_that_never_carries_shows_the_engines_own_words(monkeypatch, tmp_path, pool, fake_binary, inert):
    """Measured on this box: an engine workdir without GeoSite.dat binds nothing.

    ``connection refused`` alone would send the next reader to the engine's log by
    hand; the report has to carry the reason.
    """

    def _silent_spawn(snap, log_path):
        log_path.write_text(
            'time="x" level=info msg="Geodata Loader mode: memconservative"\n'
            'time="x" level=info msg="Can\'t find GeoSite.dat, start download"\n',
            encoding="utf-8",
        )
        return 9999, 1000.0

    monkeypatch.setattr(engine, "_spawn", _silent_spawn)
    home = tmp_path / "home"
    rep = ensure(_table(include=[str(pool)], binary=fake_binary), home=home, wait_s=0)
    assert rep.state == FAILED, rep
    assert any("GeoSite.dat" in n for n in rep.notes), rep.notes


class _Resp:
    def __init__(self, status_code):
        self.status = status_code

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_a_probe_is_only_a_probe(monkeypatch):
    """Listening != carrying: the known answer is what counts."""

    def opener(status_code):
        return lambda *a, **k: type("O", (), {"open": lambda s, u, timeout=None: _Resp(status_code)})()

    monkeypatch.setattr(urllib.request, "build_opener", opener(200))
    ok, detail = probe_engine(_PORT)
    assert not ok and "expected 204" in detail

    monkeypatch.setattr(urllib.request, "build_opener", opener(204))
    ok, detail = probe_engine(_PORT)
    assert ok and detail.startswith("204")

    ok, detail = probe_engine(0)
    assert not ok and "mixed_port" in detail


@pytest.fixture
def clean_liveness(monkeypatch):
    monkeypatch.setattr(engine, "listening", lambda port, **kw: False)
    engine.reset_reach_cache()
    engine.reset_downgrades()
    yield
    engine.reset_reach_cache()
    engine.reset_downgrades()


def test_a_dead_local_exit_is_not_handed_to_anyone(clean_liveness):
    """Reproduced on this box, 09:3x: the table pointed everything at 7895, count of
    mihomo processes was 0, and direct fetches to github.com were returning 200."""
    exit_url, reason = engine.usable_exit("http://127.0.0.1:7895")
    assert exit_url is None
    assert reason == "nothing is listening on 127.0.0.1:7895"
    assert engine.recent_downgrades() == [
        {"exit": "http://127.0.0.1:7895", "reason": reason}
    ]


def test_a_live_local_exit_is_used_and_not_logged_as_down(clean_liveness, monkeypatch):
    monkeypatch.setattr(engine, "listening", lambda port, **kw: True)
    monkeypatch.setattr(engine, "_probe_once", lambda port, **kw: (engine.UP, "204 via http://127.0.0.1:7895"))
    exit_url, reason = engine.usable_exit("http://127.0.0.1:7895")
    assert exit_url == "http://127.0.0.1:7895" and reason is None
    assert engine.recent_downgrades() == []


def test_a_port_in_listening_but_not_carrying_is_a_downgrade(clean_liveness, monkeypatch):
    """Ruled 10-12: the judgement is the known answer, not the socket.

    The old reading stopped at "somebody is home", so an engine that is up but
    whose nodes are all dead kept getting handed to every child.
    """
    monkeypatch.setattr(engine, "listening", lambda port, **kw: True)
    monkeypatch.setattr(engine, "_probe_once", lambda port, **kw: (engine.DOWN, "10061 via http://127.0.0.1:7895"))
    exit_url, reason = engine.usable_exit("http://127.0.0.1:7895")
    assert exit_url is None and reason == "10061 via http://127.0.0.1:7895"
    assert len(engine.recent_downgrades()) == 1


def test_a_timeout_is_unknown_and_keeps_the_table_in_force(clean_liveness, monkeypatch):
    """Three states, not two: downgrading a slow engine sends children direct.

    That would be worse than the bug — direct is exactly the route the table
    refuses — so only a refusal steps aside.
    """
    monkeypatch.setattr(engine, "listening", lambda port, **kw: True)
    monkeypatch.setattr(engine, "_probe_once", lambda port, **kw: (engine.UNKNOWN, "timed out after 1.5s"))
    exit_url, reason = engine.usable_exit("http://127.0.0.1:7895")
    assert exit_url == "http://127.0.0.1:7895" and reason is None
    assert engine.recent_downgrades() == []


def test_the_spawn_path_probe_is_shorter_than_the_supervision_one():
    """A command must not stall 8s behind a proxy that is never going to answer."""
    assert engine.SPAWN_PROBE_TIMEOUT < engine.PROBE_TIMEOUT


def test_somebody_elses_proxy_is_not_probed_from_here(clean_liveness, monkeypatch):
    """We do not open a socket to a remote exit on every spawn, and a TCP check
    could not tell reachability from a firewall anyway."""
    calls = []
    monkeypatch.setattr(engine, "listening", lambda port, **kw: calls.append(port) or False)
    monkeypatch.setattr(engine, "_probe_once", lambda port, **kw: calls.append(("probe", port)) or (engine.DOWN, "x"))
    for url in ["http://proxy.internal.test:3128", "socks5h://10.9.9.9:1080"]:
        assert engine.usable_exit(url) == (url, None)
    assert calls == []
    assert engine.recent_downgrades() == []


def test_the_liveness_reading_is_reused_within_its_ttl(clean_liveness, monkeypatch):
    seen = []
    monkeypatch.setattr(engine, "listening", lambda port, **kw: seen.append("listen") or True)
    monkeypatch.setattr(engine, "_probe_once", lambda port, **kw: seen.append("probe") or (engine.UP, "204"))
    for _ in range(4):
        assert engine.usable_exit("http://127.0.0.1:7895")[0] == "http://127.0.0.1:7895"
    assert seen == ["listen", "probe"]


def test_a_downgrade_is_written_down_once_while_it_stays_down(clean_liveness, monkeypatch):
    monkeypatch.setattr(engine, "listening", lambda port, **kw: False)
    for _ in range(5):
        assert engine.usable_exit("http://127.0.0.1:7895")[0] is None
    assert len(engine.recent_downgrades()) == 1

    monkeypatch.setattr(engine, "listening", lambda port, **kw: True)
    monkeypatch.setattr(engine, "_probe_once", lambda port, **kw: (engine.UP, "204"))
    engine.reset_reach_cache()
    assert engine.usable_exit("http://127.0.0.1:7895")[0] == "http://127.0.0.1:7895"

    monkeypatch.setattr(engine, "listening", lambda port, **kw: False)
    engine.reset_reach_cache()
    assert engine.usable_exit("http://127.0.0.1:7895")[0] is None
    assert len(engine.recent_downgrades()) == 2


def test_reload_restarts_our_engine_and_refuses_a_foreign_one(monkeypatch, tmp_path, pool, fake_binary, inert):
    home = tmp_path / "home"
    table = _table(include=[str(pool)], binary=fake_binary)
    fake = FakePsutil({4242: {"name": "mihomo.exe", "created": 1000.0}}, up=inert["up"], port=_PORT)
    monkeypatch.setattr(engine, "_psutil", lambda: fake)

    ensure(table, home=home, wait_s=0)
    rep = engine.reload_engine(table, home=home)
    assert rep.state == OURS, rep
    assert fake.terminated == [4242]
    assert len(inert["spawn"]) == 2

    # a listener we did not start is reported, never taken over
    monkeypatch.setattr(engine, "_pid_is_ours", lambda rec: (False, "record is stale"))
    engine._clear_record(home)
    rep = engine.reload_engine(table, home=home)
    assert rep.state == FOREIGN
    assert any("was not started by Plobi" in n for n in rep.notes), rep.notes
    assert len(inert["spawn"]) == 2


def test_the_visible_surface_prints_this_object_not_a_second_reading():
    """裁定 94: one reader — ``lines()`` is the report's only rendering."""
    rep = engine.EngineReport(OURS, "started by Plobi", _PORT, "/w", "pool", 4242, "204 via x", ("n1",))
    text = "\n".join(rep.lines())
    assert "running-ours" in text and "7896" in text and "n1" in text and "204 via x" in text
