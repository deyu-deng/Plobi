"""The PAC server (WP-EGRESS-ROUTING): WinINET will not load a ``file://`` PAC.

The claim these tests pin is the one measured on 2026-10-25: with the static proxy
pointed at a dead port and ``AutoConfigURL`` at our file, WinINET used the static
entry and ignored the PAC; served over ``http://`` it used the PAC.  So the server
has to exist, answer with the table's own PAC, and say so when it is not up.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from agent.egress import pac_server, system_proxy
from agent.egress.rules import load_table

TABLE_RAW = {
    "proxies": {"gw": {"url": "http://127.0.0.1:7895"}},
    "rules": [
        {"match": "geosite:cn", "via": "direct"},
        {"match": "store.steampowered.com", "via": "direct"},
        {"match": "*", "via": "proxy:gw"},
    ],
    "engine": {"enabled": True, "mixed_port": 7895},
}


def _table(**extra):
    raw = {**TABLE_RAW, **extra}
    return load_table(config={"network": {"egress": raw}}, force_reload=True)


def _get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A fake Windows box: registry in a dict, home in tmp_path."""
    written: dict[str, object] = {"ProxyEnable": 0, "ProxyServer": "http://parent.test:8080"}

    def fake_write(values: dict[str, object]) -> None:
        for name in system_proxy.REG_VALUES:
            if name not in values or values[name] is None:
                written.pop(name, None)
            else:
                written[name] = values[name]

    monkeypatch.setattr(system_proxy, "supported", lambda: True)
    monkeypatch.setattr(system_proxy, "read_current", lambda: dict(written))
    monkeypatch.setattr(system_proxy, "_write_registry", fake_write)
    monkeypatch.setattr("agent.egress.engine.usable_exit", lambda url, **kw: (url, "test gate"))
    return written


@pytest.fixture
def served(tmp_path, monkeypatch):
    """A PAC server in this process, answering from a table we control."""
    table = _table()
    monkeypatch.setattr(pac_server, "_live_table", lambda: table)
    port = 17951
    import threading

    httpd = __import__("http.server", fromlist=["ThreadingHTTPServer"]).ThreadingHTTPServer(
        ("127.0.0.1", port), pac_server._Handler
    )
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_server_answers_the_health_marker_and_nothing_else(served):
    status, body = _get("http://127.0.0.1:%d%s" % (served, pac_server.HEALTH_PATH))
    assert status == 200 and body.strip() == pac_server.HEALTH_MARKER

    status, _ = _get("http://127.0.0.1:%d/other" % served)
    assert status == 404, "a stranger's path must not be answered"


def test_the_served_pac_is_the_tables_own_pac(served):
    status, body = _get("http://127.0.0.1:%d%s" % (served, pac_server.PAC_PATH))
    assert status == 200
    assert 'if (host.toLowerCase() === "store.steampowered.com") return "DIRECT";' in body
    assert 'return "PROXY 127.0.0.1:7895";' in body


def test_a_table_we_cannot_read_becomes_a_500_not_a_wrong_pac(served, monkeypatch):
    def boom():
        raise RuntimeError("table is broken")

    monkeypatch.setattr(pac_server, "_live_table", boom)
    status, _ = _get("http://127.0.0.1:%d%s" % (served, pac_server.PAC_PATH))
    assert status == 500


def test_health_tells_our_server_from_a_stranger_holding_the_port(served):
    ok, detail = pac_server.health(served)
    assert ok and "在" in detail, detail

    ok, detail = pac_server.health(served + 1)
    assert not ok, detail


def test_the_pac_port_sits_next_to_the_engine():
    table = _table()
    assert system_proxy.pac_port(table) == table.engine_mixed_port + 1
    assert system_proxy.pac_url(table).endswith("/" + system_proxy.PAC_BASENAME)


def test_the_registry_points_at_the_http_pac_not_the_file(machine, tmp_path, monkeypatch):
    """WinINET only executes what it can load — so the URL has to be the http one."""
    monkeypatch.setattr(pac_server, "start", lambda port, **kw: (True, "test: up"))
    machine.update({"ProxyEnable": 1, "ProxyServer": "127.0.0.1:7895"})

    system_proxy.enable(_table(), home=tmp_path)

    assert machine["AutoConfigURL"] == system_proxy.pac_url(_table()), machine
    assert machine["AutoConfigURL"].startswith("http://127.0.0.1:")


def test_a_pac_server_that_will_not_start_falls_back_to_the_file_and_says_so(
    machine, tmp_path, monkeypatch, caplog
):
    """The fallback is not the PAC path — it must be spoken, not silently taken."""
    monkeypatch.setattr(pac_server, "start", lambda port, **kw: (False, "端口被占"))
    machine.update({"ProxyEnable": 1, "ProxyServer": "127.0.0.1:7895"})

    with caplog.at_level("WARNING"):
        system_proxy.enable(_table(), home=tmp_path)

    assert machine["AutoConfigURL"].startswith("file:///"), machine
    assert "PAC 服务没起来" in caplog.text, caplog.text
    assert "静态代理那一路" in caplog.text, caplog.text


def test_status_recognises_our_pac_url_in_either_shape(machine, tmp_path):
    machine["AutoConfigURL"] = "http://127.0.0.1:17999/plobi-egress.pac"
    assert system_proxy.status(_table(), home=tmp_path)["ours"] is True

    machine["AutoConfigURL"] = "http://127.0.0.1:17999/somebody-else.pac"
    assert system_proxy.status(_table(), home=tmp_path)["ours"] is False

    machine["AutoConfigURL"] = ""
    assert system_proxy.status(_table(), home=tmp_path)["ours"] is False


def test_the_pac_server_record_says_which_pid_is_ours(tmp_path, monkeypatch):
    rec = {"pid": 4242, "created": 100.0, "port": 17951, "binary": "python.exe"}
    pac_server._write_record(rec, home=tmp_path)
    assert pac_server._read_record(home=tmp_path) == rec

    def not_ours(rec):
        return False, "pid 4242 现在是别的东西"

    monkeypatch.setattr(pac_server, "_pid_is_ours", not_ours)
    ok, why = pac_server.stop(home=tmp_path)
    assert not ok and "不是我们的" in why, why
    assert pac_server._read_record(home=tmp_path) is None, "a stranger's pid must not be left in our record"
