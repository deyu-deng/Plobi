"""Verification for the OpenCode **Free** leg (`providers/apps/opencode/free.py`).

The Free pool is the one lane OpenCode only serves inside its own client (官方原文
「Free-tier chat models require OpenCode」；裸 http 直连恒 429)。The client ships an
http service of its own (`opencode service` on 127.0.0.1:<port>), and that is the lane
this leg drives. The measurements that shaped it (2026-11-09) are pinned here so it
cannot start lying again:

* the model list is the upstream's own `/api/model`, cut down to what it prices at
  zero — a declared list would rot;
* one turn = create a session (model pinned, read-only agent, every permission denied)
  → prompt with `text` → read the answer out of the session's messages (the shape is
  a **top-level `content`**, not `parts`);
* a spent bucket answers `200` on the POST and then reports the failure **inside the
  message list** ⇒ the verdict is read from there, not from the POST's status;
* `capabilities.tools` stays **false**: the upstream silently ignores a `tools` field,
  so our tools cannot ride along (裁定 204's test: tools closed upstream ⇒ not a
  provider) — the leg must not pretend otherwise.

Nothing here touches the real OpenCode service: the base URL is pinned in the config
and the password is stubbed.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from quota_hub.providers.apps.opencode import free as free_mod
from quota_hub.providers.apps.opencode.free import OpenCodeFreeProvider
from quota_hub.providers.base import UpstreamError

BASE = "http://127.0.0.1:45999"


def _http(routes: dict[str, object]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.path
        reply = routes.get(key)
        if isinstance(reply, httpx.Response):
            return reply
        if callable(reply):
            return reply(request)
        return httpx.Response(404, json={"error": "route"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _model_rows(*names: str, free: tuple[str, ...] = ()) -> httpx.Response:
    rows = []
    for name in names:
        cost = (
            [{"input": 0, "output": 0, "cache": {"read": 0, "write": 0}}]
            if name in free
            else [{"input": 3, "output": 15, "cache": {"read": 0, "write": 0}}]
        )
        rows.append({"id": name, "providerID": "opencode", "cost": cost})
    return httpx.Response(200, json={"data": rows})


def _assistant(text: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "data": [
                {
                    "id": "msg_1",
                    "type": "assistant",
                    "agent": "plan",
                    "model": {"id": "iceman-free", "providerID": "opencode"},
                    "content": [{"type": "text", "text": text}],
                    "finish": "stop",
                    "cost": 0,
                    "tokens": {"input": 12, "output": 3, "reasoning": 0},
                }
            ]
        },
    )


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Never touch the real ledger, the real password or the real client."""
    monkeypatch.setenv("QUOTA_HUB_LEG_COOLDOWN", str(tmp_path / "leg-cooldowns.json"))
    monkeypatch.setattr(free_mod, "read_service_password", lambda: "test-password")
    monkeypatch.setattr(free_mod, "opencode_binary", lambda: "opencode")


def test_the_leg_publishes_only_what_the_upstream_prices_at_zero():
    async def _go():
        routes = {
            "/api/model": _model_rows("iceman-free", "big-pickle", "glm-5.3-flash-free", free=("iceman-free", "glm-5.3-flash-free"))
        }
        async with _http(routes) as http:
            prov = OpenCodeFreeProvider({"base_url": BASE}, http)
            accounts = await prov.discover_accounts()
            assert accounts, "a reachable service must yield one seat"
            assert prov.served_models == ("opencode_free/iceman-free", "opencode_free/glm-5.3-flash-free")
            assert prov.credential_health() == (True, "ok")

    asyncio.run(_go())


def test_one_turn_goes_through_the_session_lane():
    seen: list[httpx.Request] = []

    def session(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": {"id": "ses_1"}})

    def prompt(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": {"id": "msg_1"}})

    async def _go():
        routes = {
            "/api/model": _model_rows("iceman-free", free=("iceman-free",)),
            "/api/session": session,
            "/api/session/ses_1/prompt": prompt,
            "/api/session/ses_1/message": _assistant("PLBI-FREE-OK"),
        }
        async with _http(routes) as http:
            prov = OpenCodeFreeProvider({"base_url": BASE}, http)
            accounts = await prov.discover_accounts()
            result = await prov.chat(
                accounts[0],
                {"model": "opencode_free/iceman-free", "messages": [{"role": "user", "content": "hi"}]},
            )
            assert result["choices"][0]["message"]["content"] == "PLBI-FREE-OK"
            assert result["choices"][0]["finish_reason"] == "stop"
            assert result["usage"]["completion_tokens"] == 3

    asyncio.run(_go())

    bodies = {request.url.path: request for request in seen}
    created = bodies["/api/session"]
    payload = created.content.decode()
    # The model is pinned on the session (not on the prompt), the agent is read-only and
    # every permission is denied — this leg must not touch the machine.
    assert '"providerID"' in payload or "providerID" in payload
    assert "plan" in payload
    assert "deny" in payload
    assert "Authorization" in created.headers


def test_a_spent_free_bucket_is_reported_as_quota_exhausted():
    spent = httpx.Response(
        200,
        json={
            "data": [
                {
                    "id": "msg_1",
                    "type": "assistant",
                    "content": [],
                    "finish": "error",
                    "error": {
                        "type": "provider.quota",
                        "status": 429,
                        "message": "Free usage limit reached.",
                    },
                }
            ]
        },
    )

    async def _go():
        routes = {
            "/api/model": _model_rows("iceman-free", free=("iceman-free",)),
            "/api/session": httpx.Response(200, json={"data": {"id": "ses_1"}}),
            "/api/session/ses_1/prompt": httpx.Response(200, json={"data": {"id": "msg_1"}}),
            "/api/session/ses_1/message": spent,
        }
        async with _http(routes) as http:
            prov = OpenCodeFreeProvider({"base_url": BASE}, http)
            accounts = await prov.discover_accounts()
            with pytest.raises(UpstreamError) as excinfo:
                await prov.chat(
                    accounts[0],
                    {"model": "opencode_free/iceman-free", "messages": [{"role": "user", "content": "hi"}]},
                )
            assert excinfo.value.status == 429
            assert excinfo.value.cooldown and excinfo.value.cooldown > 0
            # The card must say "the quota is spent", not "the credential is broken".
            assert prov.credential_health() == (False, "quota_exhausted")
            assert prov.quota_retry_after()

    asyncio.run(_go())


def test_the_leg_does_not_claim_tools():
    """Our tools cannot ride along: the upstream ignores a `tools` field (measured)."""

    async def _go():
        async with _http({}) as http:
            prov = OpenCodeFreeProvider({"base_url": BASE}, http)
            return prov.capabilities().as_dict()

    caps = asyncio.run(_go())
    assert caps["tools"] is False


def test_an_unreachable_service_yields_no_seat_and_says_so():
    async def _go():
        async with _http({}) as http:
            prov = OpenCodeFreeProvider({"base_url": BASE}, http)
            accounts = await prov.discover_accounts()
            assert accounts == []
            assert prov.served_models == ()
            live, reason = prov.credential_health()
            assert live is False
            assert "unreachable" in reason

    asyncio.run(_go())
