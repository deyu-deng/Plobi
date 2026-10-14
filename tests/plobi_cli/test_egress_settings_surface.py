"""The egress table's settings surface (WP-EGRESS-ROUTING).

Pinned here because the bug these endpoints exist to prevent is a one-click
product break: the schema-driven settings form renders a list of mappings as a
comma-separated input, and a malformed table raises at *spawn* time.
"""

from __future__ import annotations

import pytest
import yaml

from agent.egress import rules as egress
from agent.egress.rules import EgressConfigError, parse_table
from plobi_cli.egress_config import replace_egress, validate_egress

_VALID = {
    "force_ipv4": False,
    "egress": {
        "proxies": {"home": {"url": "http://127.0.0.1:7895"}},
        "rules": [
            {"match": "127.0.0.1", "via": "direct"},
            {"match": "*", "via": "proxy:home"},
        ],
        "mirrors": {"npm": "https://registry.npmmirror.com"},
        "engine": {"enabled": True, "binary": "", "mixed_port": 7895, "include_files": []},
    },
}

#: Exactly what the generic form produces when a user touches the rules field.
_COMMA_CORRUPTED = {"egress": {"rules": ["[object Object]"]}}


@pytest.fixture(autouse=True)
def fresh_table(monkeypatch):
    monkeypatch.setattr(egress, "_TABLE_CACHE", None)
    yield
    monkeypatch.setattr(egress, "_TABLE_CACHE", None)


def test_the_generic_form_cannot_be_offered_a_table_it_would_corrupt():
    """The structured keys are excluded from CONFIG_SCHEMA on purpose."""
    from plobi_cli.web_server import CONFIG_SCHEMA, _SCHEMA_STRUCTURED_ONLY

    for key in _SCHEMA_STRUCTURED_ONLY:
        assert key not in CONFIG_SCHEMA, f"{key} must not be rendered by the generic form"
    # and the scalars a user legitimately wants to flip are still there
    assert CONFIG_SCHEMA["network.egress.engine.enabled"]["type"] == "boolean"
    assert CONFIG_SCHEMA["network.egress.engine.mixed_port"]["type"] == "number"


def test_network_is_its_own_category_now():
    from plobi_cli.web_server import CONFIG_SCHEMA, _CATEGORY_MERGE, _CATEGORY_ORDER

    assert "network" not in _CATEGORY_MERGE, "network is not a sub-pane of Agent anymore"
    assert CONFIG_SCHEMA["network.egress.engine.enabled"]["category"] == "network"
    assert "network" in _CATEGORY_ORDER


def test_the_comma_corrupted_table_is_refused_before_it_reaches_disk():
    ok, issues = validate_egress(_COMMA_CORRUPTED)
    assert not ok
    assert "must be a mapping" in issues[0]
    with pytest.raises(EgressConfigError):
        parse_table(_COMMA_CORRUPTED["egress"])  # same verdict from the one parser


def test_a_valid_block_passes():
    assert validate_egress(_VALID) == (True, [])
    assert validate_egress(None) == (True, [])
    assert validate_egress({"force_ipv4": True}) == (True, [])


def test_a_table_without_a_catch_all_is_refused():
    block = {"egress": {"proxies": {"home": {"url": "http://127.0.0.1:1"}},
                        "rules": [{"match": "a.test", "via": "direct"}]}}
    ok, issues = validate_egress(block)
    assert not ok and "catch-all" in issues[0]


def test_replace_writes_and_deletes_and_reload_is_visible(tmp_path, monkeypatch):
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text("model: test/model\n", encoding="utf-8")

    ok, issues = replace_egress(_VALID)
    assert ok, issues
    on_disk = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert on_disk["network"]["egress"]["proxies"] == {"home": {"url": "http://127.0.0.1:7895"}}
    # the running process sees it without a restart — this is what the cache test is about
    table = egress.load_table()
    assert table.active and table.engine_mixed_port == 7895

    # a removal has to actually disappear (deep-merge could not do this)
    ok, issues = replace_egress({"force_ipv4": True, "egress": {"rules": [{"match": "*", "via": "direct"}]}})
    assert ok, issues
    on_disk = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert "proxies" not in on_disk["network"]["egress"]

    ok, issues = replace_egress(None)
    assert ok, issues
    on_disk = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert "network" not in on_disk


def test_a_rejected_replace_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text("model: test/model\n", encoding="utf-8")
    ok, issues = replace_egress(_COMMA_CORRUPTED)
    assert not ok and issues
    assert yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8")) == {
        "model": "test/model"
    }


def _client():
    from fastapi.testclient import TestClient

    from plobi_cli.web_server import app

    return TestClient(app)


def _auth():
    from plobi_cli.web_server import _SESSION_HEADER_NAME, _SESSION_TOKEN

    return {_SESSION_HEADER_NAME: _SESSION_TOKEN}


def test_the_endpoints_exist_and_answer_in_the_runtime_shape():
    from plobi_cli.web_server import app

    client = _client()
    routes = {getattr(r, "path", None) for r in app.routes}
    assert {"/api/network/egress", "/api/network/status"} <= routes

    status = client.get("/api/network/status", headers=_auth())
    assert status.status_code == 200
    body = status.json()
    assert set(body) >= {"declared", "rules", "engine", "downgrades"}
    assert body["engine"]["state"] in {"off", "running-ours", "running-foreign", "stopped", "failed"}


def test_the_endpoint_refuses_the_corrupting_save_with_the_parsers_words(tmp_path, monkeypatch):
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text("model: test/model\n", encoding="utf-8")
    client = _client()

    refused = client.put("/api/network/egress", headers=_auth(), json={"network": _COMMA_CORRUPTED})
    assert refused.status_code == 400
    assert "must be a mapping" in refused.json()["detail"]
    assert (tmp_path / "config.yaml").read_text(encoding="utf-8") == "model: test/model\n"

    accepted = client.put("/api/network/egress", headers=_auth(), json={"network": _VALID})
    assert accepted.status_code == 200, accepted.json()
    fetched = client.get("/api/network/egress", headers=_auth()).json()
    assert fetched["network"]["egress"]["rules"][1]["via"] == "proxy:home"
    assert fetched["declared"] is True


def test_the_pool_import_is_read_only_and_keeps_secrets_out_of_the_snippet(tmp_path):
    from plobi_cli.egress_config import export_pool_membership

    pool = tmp_path / "config.yaml"
    original = yaml.safe_dump({
        "proxies": [
            {"name": "n01 hk", "type": "ss", "server": "a.test", "port": 8388,
             "cipher": "aes-256-gcm", "password": "cleartext-hk", "udp": True},
            {"name": "n02", "type": "vmess", "server": "b.test", "port": 443,
             "uuid": "uuid-cleartext", "tls": True, "alterId": 0},
            {"name": "n03", "type": "tuic", "server": "c.test", "port": 443, "token": "x"},
        ],
        "proxy-groups": [{"name": "POOL", "type": "url-test", "proxies": ["n01 hk", "n03"],
                          "interval": 600}],
        "rules": ["MATCH,POOL"],
    }, sort_keys=False)
    pool.write_text(original, encoding="utf-8")

    snippet, env_values, notes = export_pool_membership(str(pool))

    assert sorted(snippet["nodes"]) == ["n01 hk", "n02"], "the unsupported type is skipped, not guessed"
    assert any("tuic" in n for n in notes), notes
    # nothing cleartext survives into the table shape
    dumped = yaml.safe_dump(snippet)
    assert "cleartext-hk" not in dumped and "uuid-cleartext" not in dumped
    assert snippet["nodes"]["n01 hk"]["password_env"] == "PLOBI_EGRESS_N01_HK_PASSWORD"
    assert env_values["PLOBI_EGRESS_N01_HK_PASSWORD"] == "cleartext-hk"
    assert env_values["PLOBI_EGRESS_N02_UUID"] == "uuid-cleartext"
    # a group keeps only members that made it in, and says which did not
    assert snippet["groups"]["POOL"]["include"] == ["n01 hk"]
    assert any("n03" in n for n in notes), notes
    # and the file it read is untouched — migrating is the user's own edit
    assert pool.read_text(encoding="utf-8") == original


# ── the save hands the new table to supervision, and says what happened ──────


class _Report:
    """Real `EngineReport`s: `reload_engine` does `dataclasses.replace` on what it gets."""

    @staticmethod
    def ours():
        from agent.egress.engine import EngineReport

        return EngineReport("running-ours", "restarted with the current table", probe="204 via x")

    @staticmethod
    def foreign():
        from agent.egress.engine import EngineReport

        return EngineReport("running-foreign", "Plobi did not start this engine and will not stop it")


def _patch_table(monkeypatch, table=None):
    """Keep the test off the real config: this slice is about the hand-off, not the read."""
    from agent.egress.rules import parse_table

    resolved = table if table is not None else parse_table(_VALID["egress"])
    monkeypatch.setattr("agent.egress.rules.load_table", lambda **kw: resolved)
    return resolved


def test_a_saved_table_is_handed_to_supervision(monkeypatch):
    from plobi_cli import web_server

    seen = []
    _patch_table(monkeypatch)
    monkeypatch.setattr(
        "agent.egress.engine.reload_engine",
        lambda table, **kw: seen.append(table) or _Report.ours(),
    )

    assert web_server._reload_egress_engine() == {
        "state": "running-ours",
        "detail": "restarted with the current table",
        "probe": "204 via x",
    }
    assert len(seen) == 1 and seen[0].active, "supervision must be handed the table, not a re-read guess"


def test_supervision_saying_no_is_reported_not_swallowed(monkeypatch):
    from plobi_cli import web_server

    _patch_table(monkeypatch)

    def boom(table, **kw):
        raise RuntimeError("port 7890 is taken")

    monkeypatch.setattr("agent.egress.engine.reload_engine", boom)
    out = web_server._reload_egress_engine()

    assert out["state"] == "failed", out
    assert "port 7890 is taken" in out["detail"], "the save landed; the sentence must say what did not"


def test_a_foreign_engine_is_refused_rather_than_stolen(monkeypatch):
    """The real `reload_engine` on a foreign listener — the answer must be a state, not an exception."""
    from plobi_cli import web_server

    table = _patch_table(monkeypatch)
    monkeypatch.setattr(
        "agent.egress.engine.status",
        lambda t, **kw: _Report.foreign(),
    )
    monkeypatch.setattr("agent.egress.engine.ensure", lambda t, **kw: (_ for _ in ()).throw(AssertionError("must not restart")))

    out = web_server._reload_egress_engine()

    assert out["state"] == "running-foreign", out
    assert table.engine_enabled is True


# ── the machine-wide switch: its own endpoint, and its refusal words ─────────


def _plan(ok: bool = True, reason: str = "ready"):
    from agent.egress.system_proxy import ProxyPlan

    return ProxyPlan(ok, reason)


def _seed(tmp_path, monkeypatch, block):
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"model": "test/model", "network": block}, sort_keys=False), encoding="utf-8"
    )
    egress.load_table(force_reload=True)


def test_the_status_snapshot_carries_the_machine_half(tmp_path, monkeypatch):
    """The pane must not ask a second question of the registry to learn the state."""
    from agent.egress import system_proxy

    monkeypatch.setattr(system_proxy, "read_current", lambda: {"ProxyEnable": 0})
    _seed(tmp_path, monkeypatch, _VALID)

    body = _client().get("/api/network/status", headers=_auth()).json()
    assert set(body["system_proxy"]) >= {"supported", "declared", "ours", "pac_path", "backup_exists"}
    assert body["system_proxy"]["declared"] is False, "nothing has flipped the switch yet"


def test_the_switch_writes_the_key_and_applies_it_now(tmp_path, monkeypatch):
    from plobi_cli import web_server

    _seed(tmp_path, monkeypatch, _VALID)
    seen = []

    def fake_ensure(table=None):
        seen.append(table)
        return _plan(True, "已接管：走系统代理的程序现在按这张表选路")

    monkeypatch.setattr("agent.egress.system_proxy.ensure", fake_ensure)

    out = _client().post("/api/network/system-proxy", headers=_auth(), json={"enabled": True})

    assert out.status_code == 200, out.json()
    assert out.json()["proxy"]["state"] == "applied"
    assert "已接管" in out.json()["proxy"]["detail"]
    assert len(seen) == 1 and seen[0].system_proxy_enabled is True, "apply must see the table just written"
    on_disk = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert on_disk["network"]["egress"]["system_proxy"] == {"enabled": True}


def test_a_refused_takeover_comes_back_as_words_not_a_log_line(tmp_path, monkeypatch):
    """The switch landed in the table; the machine was not touched — the pane has to say why."""
    _seed(tmp_path, monkeypatch, _VALID)
    monkeypatch.setattr(
        "agent.egress.system_proxy.ensure",
        lambda table=None: _plan(False, "出口 http://127.0.0.1:7895 此刻带不动流量——接管整机会让这台没有出口"),
    )

    out = _client().post("/api/network/system-proxy", headers=_auth(), json={"enabled": True})

    assert out.status_code == 200, out.json()
    assert out.json()["proxy"]["state"] == "refused"
    assert "没有出口" in out.json()["proxy"]["detail"]


def test_an_exploding_apply_is_reported_and_the_switch_stays_written(tmp_path, monkeypatch):
    from plobi_cli import web_server

    _seed(tmp_path, monkeypatch, _VALID)

    def boom(table=None):
        raise OSError("registry key is locked")

    monkeypatch.setattr("agent.egress.system_proxy.ensure", boom)
    out = web_server._apply_system_proxy()

    assert out["state"] == "failed"
    assert "registry key is locked" in out["detail"]


def test_a_table_that_asked_for_no_takeover_leaves_the_registry_alone(tmp_path, monkeypatch):
    from plobi_cli import web_server

    block = {**_VALID, "egress": {**_VALID["egress"], "system_proxy": {"enabled": False}}}
    _seed(tmp_path, monkeypatch, block)
    monkeypatch.setattr("agent.egress.system_proxy.ensure", lambda table=None: None)

    assert web_server._apply_system_proxy()["state"] == "idle"


def test_the_switch_leaves_everything_else_in_the_block_alone(tmp_path, monkeypatch):
    """Same wipe class as the panel's Save, on the Python half: the switch is one key."""
    from plobi_cli.egress_config import set_system_proxy

    block = {
        "egress": {
            **_VALID["egress"],
            "nodes": {"n01 hk": {"type": "ss", "server": "a.test", "port": 8388, "cipher": "aes-256-gcm",
                                  "password_env": "PLOBI_EGRESS_N01_HK_PASSWORD"}},
            "groups": {"POOL": {"type": "select", "include": ["n01 hk"]}},
            "engine": {**_VALID["egress"]["engine"], "dns": {"enable": True, "nameserver": ["223.5.5.5"]}},
        }
    }
    _seed(tmp_path, monkeypatch, block)

    ok, issues = set_system_proxy(True)
    assert ok, issues

    on_disk = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))["network"]["egress"]
    assert on_disk["system_proxy"] == {"enabled": True}
    assert on_disk["nodes"] == block["egress"]["nodes"], "the node pool is not this switch's to rewrite"
    assert on_disk["groups"] == block["egress"]["groups"]
    assert on_disk["engine"]["dns"]["nameserver"] == ["223.5.5.5"]
