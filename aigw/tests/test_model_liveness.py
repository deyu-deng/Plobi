"""Offline contracts for gateway-side model liveness (step ① of the picker rule).

The owner's rule, 2026-10-12: a model may only appear where a human can pick it if
picking it actually works. Two independent things make a model un-pickable —
the transport cannot carry tools, or its credential is dead right now — and both
must be decided from live state, never from a hardcoded name list.

Everything here is local: fake providers, monkeypatched credential probes, no
network and no vendor calls.
"""

from __future__ import annotations

import asyncio

import pytest

import aigw.main as main
from aigw.providers.base import Capabilities
from aigw.providers.workbuddy import WorkbuddyApiProvider
from aigw.registry import Registry
from aigw.tokens import desktop_stores as ds


class FakeProv:
    def __init__(self, name, models, tools=True, health=(True, "ok"), boom=False):
        self.name = name
        self.served_models = tuple(models)
        self._caps = Capabilities(stream=True, tools=tools)
        self._health = health
        self._boom = boom

    def capabilities(self) -> Capabilities:
        return self._caps

    def credential_health(self):
        if self._boom:
            raise RuntimeError("probe exploded")
        return self._health


def _registry(*provs) -> Registry:
    reg = Registry({"aliases": {}, "routing": {"rules": []}}, None)
    for p in provs:
        reg.providers[p.name] = p
        for m in p.served_models:
            reg.model_to_provider[m] = p.name
    return reg


def ids(rows):
    return sorted(r["id"] for r in rows)


def row_of(rows, model_id):
    return next(r for r in rows if r["id"] == model_id)


def test_selectable_view_drops_a_text_only_transport():
    reg = _registry(
        FakeProv("workbuddy", ["workbuddy/glm-5.2", "workbuddy/hy3"], tools=False),
        FakeProv("workbuddy_api", ["workbuddy_api/hy3"], tools=True),
    )
    assert ids(reg.list_models(selectable_only=True)) == ["workbuddy_api/hy3"], (
        "no surface needs a tools=False leg"
    )
    # routing is untouched — only the pickable view changed, so an existing pinned
    # session keeps working while nobody can newly pick the dead-end model
    assert reg.model_to_provider["workbuddy/glm-5.2"] == "workbuddy"


def test_default_catalog_stays_complete_and_explains_itself():
    """`/v1/models` is the OpenAI-compat catalog; the quota apps are derived from it.

    Filtering it by default would make other surfaces report "no such model" for a
    leg that is merely expired, so the verdict travels on the row instead.
    """
    reg = _registry(
        FakeProv("workbuddy", ["workbuddy/glm-5.2"], tools=False),
        FakeProv("workbuddy_api", ["workbuddy_api/hy3"], tools=True),
    )
    rows = reg.list_models()
    assert ids(rows) == ["workbuddy/glm-5.2", "workbuddy_api/hy3"]
    assert row_of(rows, "workbuddy/glm-5.2")["unselectable_reason"] == "no_tools"
    assert row_of(rows, "workbuddy_api/hy3")["selectable"] is True


def test_dead_credential_leaves_the_pickable_view_and_renewal_brings_it_back():
    prov = FakeProv(
        "antigravity", ["antigravity/gemini-3.8"], tools=True, health=(False, "credential_expired")
    )
    reg = _registry(prov)
    assert reg.list_models(selectable_only=True) == [], "an expired credential must not be pickable"
    row = row_of(reg.list_models(), "antigravity/gemini-3.8")
    assert row["live"] is False and row["unselectable_reason"] == "credential_expired"

    # the desktop app renews its own token: no code change, no name list edit
    prov._health = (True, "ok")
    assert ids(reg.list_models(selectable_only=True)) == ["antigravity/gemini-3.8"]


def test_a_broken_probe_is_reported_as_not_live_not_guessed_as_working():
    reg = _registry(FakeProv("x", ["x/m"], tools=True, boom=True))
    assert reg.list_models(selectable_only=True) == []
    row = row_of(reg.list_models(), "x/m")
    assert row["live"] is False and row["live_reason"].startswith("probe_error"), row


def test_provider_without_a_probe_is_assumed_live():
    class NoProbe(FakeProv):
        credential_health = None  # type: ignore[assignment]

    reg = _registry(NoProbe("y", ["y/m"], tools=True))
    assert ids(reg.list_models(selectable_only=True)) == ["y/m"], (
        "absence of a probe is not evidence of death"
    )


class Recorder:
    """Stands in for the registry so the HTTP route's own plumbing is covered."""

    def __init__(self):
        self.calls = []

    async def ensure_models_refreshed(self):
        pass

    def list_models(self, selectable_only: bool = False):
        self.calls.append(selectable_only)
        return [{"id": "only/when-filtered" if selectable_only else "everything"}]


@pytest.fixture
def state_with(monkeypatch):
    def _install(reg):
        monkeypatch.setitem(main.STATE, "reg", reg)
        monkeypatch.setitem(main.STATE, "api_key", "sk-test")

    return _install


@pytest.mark.parametrize(
    "query,expected",
    [({"selectable": True}, True), ({"selectable": False}, False)],
)
def test_route_passes_the_selectable_flag_through(monkeypatch, state_with, query, expected):
    # Calling the handler directly skips FastAPI's query parsing, so this covers the
    # route -> registry kwarg plumbing only; the omitted-parameter default is proven
    # over HTTP below, where the DI machinery actually runs.
    rec = Recorder()
    state_with(rec)
    body = asyncio.run(main.models(authorization="Bearer sk-test", **query))
    assert rec.calls == [expected]
    assert body["data"][0]["id"] == ("only/when-filtered" if expected else "everything")


def test_http_query_string_reaches_the_filter(monkeypatch, state_with):
    """Same path the desktop will use in step ② — proves `?selectable=1` parses."""
    import httpx

    rec = Recorder()
    state_with(rec)

    async def _go():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://gateway"
        ) as c:
            h = {"authorization": "Bearer sk-test"}
            full = await c.get("/v1/models", headers=h)
            sel = await c.get("/v1/models?selectable=1", headers=h)
            return full.json()["data"], sel.json()["data"]

    full, sel = asyncio.run(_go())
    assert full[0]["id"] == "everything" and sel[0]["id"] == "only/when-filtered"
    assert rec.calls == [False, True]


def test_workbuddy_api_counts_a_renewable_token_as_live(monkeypatch):
    prov = WorkbuddyApiProvider(
        {"base_url": "https://www.workbuddy.cn", "dialect": "openai"}, None
    )
    monkeypatch.setattr(
        ds,
        "codebuddy_token_status",
        lambda: {"present": True, "expires_in_seconds": -60, "can_refresh": True},
    )
    assert prov.credential_health() == (True, "renewable")

    monkeypatch.setattr(
        ds,
        "codebuddy_token_status",
        lambda: {"present": True, "expires_in_seconds": -60, "can_refresh": False},
    )
    assert prov.credential_health() == (False, "credential_expired")

    monkeypatch.setattr(
        ds,
        "codebuddy_token_status",
        lambda: {"present": False, "expires_in_seconds": None, "can_refresh": False},
    )
    assert prov.credential_health() == (False, "no_desktop_credential")

    monkeypatch.setattr(
        ds,
        "codebuddy_token_status",
        lambda: {"present": True, "expires_in_seconds": 3600, "can_refresh": True},
    )
    assert prov.credential_health() == (True, "ok")
