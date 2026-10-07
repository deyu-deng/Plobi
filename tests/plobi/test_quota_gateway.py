"""B3 gateway derivation: the desktop-quota source list comes from the gateway.

裁定 46 says the list of quota apps has one source of truth — the gateway's own
``GET /v1/models`` — and forbids a second copy. These tests pin the two ways that
goes wrong in practice:

* grouping by the ``<app>/`` **id prefix** instead of the ``provider`` field. The
  live gateway's mock channel serves ``workbuddy/deepseek-chat``, so prefix-matching
  reports a fake channel as a real one.
* collapsing "could not ask" into "nothing there". An outage and an empty catalog
  must stay two different answers (R-048's deferred-vs-red discipline).

Every fetch here goes through the ``fetch`` seam — no test touches the network.
"""

from __future__ import annotations

import json
import time
import urllib.error

import pytest

from plobi.quota.gateway import (
    CatalogState,
    GatewayOutcome,
    catalog_url,
    fetch_sources,
    sources_from_catalog,
)
from plobi.quota.pool import GATEWAY_RETRY_SECONDS, QuotaPool
from plobi.quota.sources import AigwSource, CheapApiSource, HealthStatus

# Captured shape from the running gateway (uvicorn on 127.0.0.1:8000). The mock
# entry serving a workbuddy/ id is real, not invented — see aigw/config.yaml.
LIVE_CATALOG = {
    "object": "list",
    "data": [
        {
            "id": "mock/echo",
            "provider": "mock",
            "capabilities": {"stream": True, "tools": False, "compliance": "compliant"},
        },
        {
            "id": "workbuddy/deepseek-chat",
            "provider": "mock",
            "capabilities": {"stream": True, "tools": False, "compliance": "compliant"},
        },
    ],
}

APP_CATALOG = {
    "object": "list",
    "data": [
        {"id": "mock/echo", "provider": "mock", "capabilities": {"stream": True}},
        {
            "id": "antigravity/gemini-3-flash",
            "provider": "antigravity",
            "capabilities": {"stream": True, "tools": False},
        },
        {
            "id": "antigravity/gemini-3-pro-high",
            "provider": "antigravity",
            "capabilities": {"stream": True, "tools": True},
        },
        {
            "id": "cursor/auto",
            "provider": "cursor",
            "capabilities": {"stream": True, "sessionful": True},
        },
    ],
}


def _outcome(state, sources=None, models_seen=0, checked_at=None):
    return GatewayOutcome(
        state, sources or {}, state.value, checked_at or time.time(), models_seen
    )


def _pool(gateway, sources=None):
    return QuotaPool(
        sources if sources is not None else {"zhipu-air": CheapApiSource(
            "zhipu-air", model="glm-4-air", base_url="http://x/v1", api_key="k"
        )},
        order=["zhipu-air"],
        gateway=gateway,
    )


def _counting(calls: list, outcome: GatewayOutcome):
    """A fetch seam that records what it was asked for and returns ``outcome``."""

    def fetch(base_url, api_key, **_kw):
        calls.append((base_url, api_key))
        return outcome

    return fetch


# ── catalog parsing ──────────────────────────────────────────────────────────


def test_catalog_url_handles_both_base_shapes():
    assert catalog_url("http://127.0.0.1:8000/v1") == "http://127.0.0.1:8000/v1/models"
    assert catalog_url("http://127.0.0.1:8000") == "http://127.0.0.1:8000/v1/models"
    assert catalog_url("") == ""


def test_groups_by_provider_not_by_id_prefix():
    """The mock channel serving ``workbuddy/…`` must not become a workbuddy source."""
    sources, seen = sources_from_catalog(
        LIVE_CATALOG, base_url="http://h/v1", api_key="k"
    )
    assert sources == {}
    assert seen == 2  # both were seen — that's what distinguishes "only mock"


def test_one_source_per_app_with_all_its_models():
    sources, seen = sources_from_catalog(
        APP_CATALOG, base_url="http://h/v1", api_key="k"
    )
    assert seen == 4
    assert set(sources) == {"antigravity", "cursor"}
    ant = sources["antigravity"]
    assert ant["kind"] == "aigw"
    assert ant["derived"] is True
    assert ant["base_url"] == "http://h/v1"
    assert sorted(ant["models"]) == [
        "antigravity/gemini-3-flash",
        "antigravity/gemini-3-pro-high",
    ]


def test_picks_tool_capable_model_deterministically():
    sources, _ = sources_from_catalog(APP_CATALOG, base_url="http://h/v1", api_key="k")
    assert sources["antigravity"]["model"] == "antigravity/gemini-3-pro-high"
    # a sessionful adapter is worth as much as a tool-capable one
    assert sources["cursor"]["model"] == "cursor/auto"
    # stable across calls — a source that silently changes model would move the
    # L2 loop's context between two reads
    again, _ = sources_from_catalog(APP_CATALOG, base_url="http://h/v1", api_key="k")
    assert again["antigravity"]["model"] == sources["antigravity"]["model"]


def test_absent_capabilities_stay_unknown_not_defaulted():
    payload = {"data": [{"id": "wbx/deepseek-chat", "provider": "wbx"}]}
    sources, _ = sources_from_catalog(payload, base_url="http://h/v1", api_key="k")
    assert sources["wbx"]["capabilities"] == {}


def test_bare_list_payload_accepted():
    sources, seen = sources_from_catalog(
        [{"id": "antigravity/gemini-3-flash", "provider": "antigravity"}],
        base_url="http://h/v1",
        api_key="k",
    )
    assert seen == 1
    assert set(sources) == {"antigravity"}


# ── fetch outcome states ─────────────────────────────────────────────────────


class _Response:
    def __init__(self, payload, status=200):
        self._payload = payload.encode() if isinstance(payload, str) else payload
        self.status = status

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _open(payload):
    return lambda request, timeout: _Response(payload)


def test_fetch_ok_reports_sources():
    outcome = fetch_sources(
        "http://h/v1", "k", open_url=_open('{"data":[{"id":"antigravity/x","provider":"antigravity","capabilities":{}}]}')
    )
    assert outcome.state is CatalogState.OK
    assert set(outcome.sources) == {"antigravity"}


def test_fetch_only_mock_is_answered_with_no_apps():
    outcome = fetch_sources("http://h/v1", "k", open_url=_open('{"data":[{"id":"mock/echo","provider":"mock"}]}'))
    assert outcome.answered is True
    assert outcome.sources == {}
    assert "1 models" in outcome.detail


def test_fetch_unreachable_is_not_answered():
    def boom(request, timeout):
        raise urllib.error.URLError("connection refused")

    outcome = fetch_sources("http://h/v1", "k", open_url=boom)
    assert outcome.state is CatalogState.UNREACHABLE
    assert outcome.answered is False


def test_fetch_bad_key_is_rejected_not_unreachable():
    def deny(request, timeout):
        raise urllib.error.HTTPError("http://h/v1/models", 401, "Unauthorized", {}, None)

    outcome = fetch_sources("http://h/v1", "k", open_url=deny)
    assert outcome.state is CatalogState.REJECTED
    assert "rejected the key" in outcome.detail


def test_fetch_garbage_body_is_rejected():
    outcome = fetch_sources("http://h/v1", "k", open_url=_open("not json at all"))
    assert outcome.state is CatalogState.REJECTED


def test_no_key_sends_no_authorization_header():
    seen = {}

    def capture(request, timeout):
        seen["headers"] = dict(request.header_items())
        return _Response('{"data":[]}')

    fetch_sources("http://h/v1", "", open_url=capture)
    assert "Authorization" not in seen["headers"] and "authorization" not in seen["headers"]


# ── pool derivation ──────────────────────────────────────────────────────────


def test_registered_sources_survive_a_failed_read():
    calls: list = []
    pool = _pool({"base_url": "http://h/v1", "api_key": "k", "enabled": True})
    pool.refresh_gateway(fetch=lambda b, k, **kw: _outcome(CatalogState.OK, {
        "antigravity": {"kind": "aigw", "model": "antigravity/x", "base_url": b,
                        "api_key": k, "derived": True}
    }))
    assert "antigravity" in pool.sources

    pool.refresh_gateway(
        force=True,
        fetch=lambda b, k, **kw: _outcome(CatalogState.UNREACHABLE),
    )
    # An outage must not read as "the user has no quota apps".
    assert "antigravity" in pool.sources
    assert pool.catalog_status()["state"] == "unreachable"


def test_catalog_removal_drops_only_derived_sources():
    pool = _pool({"base_url": "http://h/v1", "api_key": "k", "enabled": True})
    pool.refresh_gateway(fetch=lambda b, k, **kw: _outcome(CatalogState.OK, {
        "antigravity": {"kind": "aigw", "model": "antigravity/x", "base_url": b,
                        "api_key": k, "derived": True},
        "cursor": {"kind": "aigw", "model": "cursor/auto", "base_url": b,
                   "api_key": k, "derived": True},
    }))
    assert {"antigravity", "cursor"} <= set(pool.sources)
    assert "zhipu-air" in pool.order

    pool.refresh_gateway(force=True, fetch=lambda b, k, **kw: _outcome(CatalogState.OK, {
        "cursor": {"kind": "aigw", "model": "cursor/auto", "base_url": b,
                   "api_key": k, "derived": True},
    }))
    assert set(pool.sources) == {"zhipu-air", "cursor"}
    assert pool.order[0] == "zhipu-air"  # cheap-first survives re-derivation
    assert pool.status("cursor") is None  # replaced source must re-probe


def test_probe_and_resolve_never_fetch():
    """The offline contract: /api/quota/summary must not become a network call."""
    calls: list = []
    pool = _pool({"base_url": "http://h/v1", "api_key": "k", "enabled": True})
    pool.refresh_gateway(fetch=_counting(calls, _outcome(CatalogState.OK, {
        "antigravity": {"kind": "aigw", "model": "antigravity/x", "base_url": "http://h/v1",
                        "api_key": "k", "derived": True}
    })))
    calls.clear()
    pool.probe_all()
    pool.resolve()
    pool.mark_failed("antigravity")
    pool.resolve()
    assert calls == []


def test_catalog_status_is_none_until_first_ask():
    pool = _pool({"base_url": "http://h/v1", "api_key": "k"})
    assert pool.catalog_status() is None  # unchecked, not empty


def test_ok_result_is_cached_for_the_ttl():
    calls: list = []
    pool = _pool({"base_url": "http://h/v1", "api_key": "k"})
    fetch = _counting(calls, _outcome(CatalogState.OK))
    pool.refresh_gateway(fetch=fetch)
    pool.refresh_gateway(fetch=fetch)
    assert len(calls) == 1


def test_failed_read_retries_before_the_ttl_expires():
    """The desktop app brings the gateway up after boot; caching that for 300s
    would keep real channels invisible."""
    calls: list = []
    pool = _pool({"base_url": "http://h/v1", "api_key": "k"})
    fetch = _counting(calls, _outcome(CatalogState.OK))
    pool._catalog = _outcome(
        CatalogState.UNREACHABLE, checked_at=time.time() - GATEWAY_RETRY_SECONDS - 1
    )
    pool.refresh_gateway(fetch=fetch)
    assert len(calls) == 1

    pool._catalog = _outcome(CatalogState.UNREACHABLE, checked_at=time.time())
    pool.refresh_gateway(fetch=fetch)
    assert len(calls) == 1  # still bounded — just by the shorter retry window


def test_disabled_gateway_clears_derived_and_says_why():
    pool = _pool({"base_url": "http://h/v1", "api_key": "k", "enabled": True})
    pool.refresh_gateway(fetch=lambda b, k, **kw: _outcome(CatalogState.OK, {
        "antigravity": {"kind": "aigw", "model": "antigravity/x", "base_url": b,
                        "api_key": k, "derived": True}
    }))
    pool.gateway = {"base_url": "http://h/v1", "enabled": False}
    outcome = pool.refresh_gateway(force=True, fetch=lambda b, k, **kw: pytest.fail("must not fetch"))
    assert outcome.state is CatalogState.DISABLED
    assert "antigravity" not in pool.sources


def test_exclude_keeps_the_app_out_of_the_pool():
    pool = _pool({
        "base_url": "http://h/v1", "api_key": "k", "enabled": True, "exclude": ["cursor"],
    })
    calls: list = []

    def fetch(b, k, **kw):
        calls.append(b)
        _sources, _ = sources_from_catalog(APP_CATALOG, base_url=b, api_key=k)
        return _outcome(CatalogState.OK, _sources, models_seen=4)

    pool.refresh_gateway(fetch=fetch)
    assert set(pool.sources) == {"zhipu-air", "antigravity"}
    assert pool.catalog_status()["source_names"] == ["antigravity", "cursor"]


def test_endpoint_reaches_the_fetcher_from_the_agents_layer():
    calls: list = []
    pool = _pool({"base_url": "http://127.0.0.1:8123/v1", "api_key": "kk", "enabled": True})
    pool.refresh_gateway(fetch=_counting(calls, _outcome(CatalogState.OK, {})))
    assert calls == [("http://127.0.0.1:8123/v1", "kk")]


def test_missing_endpoint_is_reported_not_raised():
    pool = _pool({"base_url": "", "enabled": True})
    outcome = pool.refresh_gateway(fetch=lambda b, k, **kw: pytest.fail("must not fetch"))
    assert outcome.state is CatalogState.UNREACHABLE
    assert "no gateway endpoint" in outcome.detail


# ── honesty of a derived source ──────────────────────────────────────────────


def test_derived_source_probes_degraded_never_healthy():
    """存在 ≠ 能用：catalog 只证明适配器注册过，不证明一次对话能跑通。"""
    src = AigwSource(
        "antigravity", model="antigravity/x", base_url="http://h/v1", api_key="k",
        derived=True,
    )
    status = src.probe()
    assert status.health is HealthStatus.DEGRADED
    assert "chat not proven" in status.detail
    assert src.remaining_usd() is None  # never a dollar balance


def test_derived_source_without_credential_is_unavailable():
    src = AigwSource("antigravity", model="antigravity/x", derived=True)
    assert src.probe().health is HealthStatus.UNAVAILABLE


def test_build_sources_marks_only_gateway_entries_derived():
    from plobi.quota.sources import build_sources

    built = build_sources({"sources": {
        "antigravity": {"kind": "aigw", "model": "a/x", "base_url": "http://h/v1",
                        "api_key": "k", "derived": True, "capabilities": {"tools": True}},
        "workbuddy": {"kind": "aigw", "model": "w/y", "base_url": "http://h/v1", "api_key": "k"},
    }})
    assert built["antigravity"].derived is True
    assert built["antigravity"].capabilities == {"tools": True}
    assert built["workbuddy"].derived is False
    assert built["workbuddy"].models == ["w/y"]


# ── endpoint resolution stays in the agents layer (no second env parser) ─────


def test_gateway_settings_honors_the_documented_env(monkeypatch):
    """``PLOBI_AIGW_URL`` 是既有真源；额度池必须跟着它，而不是另读一套。"""
    from plobi.quota.config import gateway_settings

    monkeypatch.setenv("PLOBI_AIGW_URL", "http://127.0.0.1:9111/v1")
    monkeypatch.setenv("AIGW_API_KEY", "sk-from-env")
    settings = gateway_settings({})
    assert settings["base_url"] == "http://127.0.0.1:9111/v1"
    assert settings["api_key"] == "sk-from-env"
    assert settings["enabled"] is True and settings["exclude"] == []


def test_gateway_settings_yaml_knobs():
    from plobi.quota.config import gateway_settings

    settings = gateway_settings({"gateway": {"enabled": False, "exclude": ["Cursor", ""]}})
    assert settings["enabled"] is False
    assert settings["exclude"] == ["cursor"]  # normalized, blanks dropped


def test_gateway_settings_quoted_bool_does_not_reverse():
    """照 ``strategy.fail_open`` 的惯例：非 bool 不硬转，回落默认。"""
    from plobi.quota.config import gateway_settings

    assert gateway_settings({"gateway": {"enabled": "false"}})["enabled"] is True


def test_load_routes_legacy_desktop_entries_to_the_gateway(monkeypatch, tmp_path, caplog):
    """旧 quota.yaml 里硬编的桌面源不许被静默丢弃——它现在是网关派生的。"""
    import logging

    from plobi.quota.config import load

    cfg = tmp_path / "quota.yaml"
    cfg.write_text(
        "sources:\n"
        "  zhipu-air: {kind: cheap_api, model: glm-4-air}\n"
        "  antigravity: {kind: aigw, model: antigravity/gemini-3-pro}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PLOBI_QUOTA_CONFIG", str(cfg))
    with caplog.at_level(logging.WARNING, logger="plobi.quota.config"):
        loaded = load()

    assert set(loaded["sources"]) == {"zhipu-air"}
    assert "gateway" in loaded
    assert "antigravity" in caplog.text
    assert "gateway.exclude" in caplog.text


# ── the spend path must aim at the completions endpoint, not the base ────────


class _FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _send_probe(monkeypatch, base_url):
    from plobi.quota.route import _default_send
    from plobi.quota.sources import AigwSource

    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        return _FakeResponse({"choices": [{"message": {"role": "assistant", "content": "pong"}}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    src = AigwSource("workbuddy", model="workbuddy/glm-5.3", base_url=base_url, api_key="k")
    out = _default_send("hi", src)
    return out, seen["url"]


def test_default_send_appends_the_completions_path(monkeypatch):
    """base_url 是 OpenAI base（…/v1），直接 POST 它会 404 —— 派生出来的源全是这个形状。"""
    out, url = _send_probe(monkeypatch, "http://127.0.0.1:8022/v1")
    assert url == "http://127.0.0.1:8022/v1/chat/completions"
    assert out == "pong"


def test_default_send_does_not_double_an_explicit_endpoint(monkeypatch):
    _out, url = _send_probe(monkeypatch, "http://127.0.0.1:8022/v1/chat/completions/")
    assert url == "http://127.0.0.1:8022/v1/chat/completions"
