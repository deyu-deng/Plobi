"""Tests for the detached proxy guard's decision logic (no registry touched).

The guard's brain is :func:`proxy_guard.plan_restore`, a pure function over the
live registry reading + the pre-takeover backup, with the port liveness probe
injected. That keeps these tests hermetic: nothing here writes HKCU.
"""
import pytest

from agent.egress import proxy_guard as pg

DEAD = lambda s: True   # "this port is not listening"
ALIVE = lambda s: False  # "this port is listening"


def test_no_restore_when_proxy_off():
    cur = {"ProxyEnable": 0, "ProxyServer": ""}
    assert pg.plan_restore(cur, None, DEAD) is None


def test_no_restore_when_no_server():
    cur = {"ProxyEnable": 1, "ProxyServer": ""}
    assert pg.plan_restore(cur, None, DEAD) is None


def test_no_restore_when_remote_proxy_is_set():
    # A real remote proxy is the user's own config — not ours to undo.
    cur = {"ProxyEnable": 1, "ProxyServer": "10.0.0.1:8080"}
    assert pg.plan_restore(cur, None, DEAD) is None


def test_no_restore_when_engine_still_alive():
    # Loopback proxy, but the engine is up — leave the takeover in place.
    cur = {"ProxyEnable": 1, "ProxyServer": "127.0.0.1:7895"}
    assert pg.plan_restore(cur, None, ALIVE) is None


def test_restore_loopback_dead_goes_direct_without_backup():
    cur = {"ProxyEnable": 1, "ProxyServer": "127.0.0.1:7895"}
    plan = pg.plan_restore(cur, None, DEAD)
    assert plan == {"ProxyEnable": 0, "ProxyServer": None}


def test_restore_loopback_dead_restores_real_remote_backup():
    # Pre-takeover was a real corporate proxy — hand it back verbatim.
    backup = {"ProxyEnable": 1, "ProxyServer": "10.0.0.1:8080", "ProxyOverride": "<local>"}
    cur = {"ProxyEnable": 1, "ProxyServer": "127.0.0.1:7895"}
    plan = pg.plan_restore(cur, backup, DEAD)
    assert plan["ProxyServer"] == "10.0.0.1:8080"
    assert plan["ProxyEnable"] == 1


def test_restore_loopback_dead_backup_also_dead_goes_direct():
    # Pre-takeover was a stale peer client's dead loopback — don't hand back a corpse.
    backup = {"ProxyEnable": 1, "ProxyServer": "127.0.0.1:7890"}
    cur = {"ProxyEnable": 1, "ProxyServer": "127.0.0.1:7895"}
    plan = pg.plan_restore(cur, backup, DEAD)
    assert plan == {"ProxyEnable": 0, "ProxyServer": None}


def test_restore_tolerates_backup_without_proxyenable():
    backup = {"ProxyServer": "10.0.0.1:8080"}  # missing ProxyEnable key
    cur = {"ProxyEnable": 1, "ProxyServer": "127.0.0.1:7895"}
    plan = pg.plan_restore(cur, backup, DEAD)
    assert plan["ProxyEnable"] == 0  # no enable recorded -> DIRECT


def test_probe_dead_parses_schemes_and_proto_maps():
    # localhost + dead => True
    assert pg._probe_dead("localhost:65530") is True
    # a non-loopback host is never "ours" to probe
    assert pg._probe_dead("example.com:443") is False
    # malformed => False (don't restore on a parse miss)
    assert pg._probe_dead("not-a-port") is False
