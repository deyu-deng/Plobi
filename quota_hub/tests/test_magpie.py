"""Offline contracts for the Magpie leg.

magpie is somebody else's project (MIT, github.com/yetone/magpie): a local
multi-model gateway that keeps the vendor subscriptions behind one
OpenAI-compatible endpoint and serves most of them through community provider
plugins. This leg only has to do three things, and each is pinned here:

1. find the gateway and know it is there (`GET /api/hello` says `magpie`);
2. publish **their** roster with **their** per-model numbers — the window, the
   thinking levels, the output cap — because that is the whole point of reading
   a live catalogue instead of writing one down ourselves;
3. send the turn under our own key name, so the calls are counted as ours.

No network, no magpie installed: the transport is an httpx mock and the hello
probe is answered by the leg's own seam.
"""

from __future__ import annotations

import json

import httpx
import pytest

from quota_hub.providers.apps.magpie import MagpieProvider


def _provider(http, **extra):
    config = {
        "timeout": 30,
        **extra,
    }
    return MagpieProvider(config, http)


@pytest.fixture(autouse=True)
def no_local_settings(tmp_path, monkeypatch):
    """Nobody's magpie settings on this machine — the port is ours to set."""
    settings = tmp_path / "magpie" / "settings.json"
    monkeypatch.setattr(MagpieProvider, "_settings_path", lambda self: settings)
    return settings


def _models_payload() -> dict:
    """A slice of magpie's own catalogue, in the shape their docs describe."""
    return {
        "object": "list",
        "data": [
            {
                "id": "opencode-zen-free/big-pickle",
                "display_name": "Big Pickle",
                "owned_by": "opencode-zen-free",
                "context_window": 200000,
                "max_output_tokens": 32000,
                "reasoning": {"effort": "high"},
                "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}],
            },
            {
                "id": "workbuddy/hy4free",
                "display_name": "Hy4",
                "owned_by": "workbuddy",
                "context_window": 960000,
                "max_output_tokens": 64000,
            },
            {"display_name": "a row with no id cannot be routed"},
        ],
    }


def _http(routes: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in routes:
            return routes[path]
        return httpx.Response(404, json={"error": "no route"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _with_gateway(prov, *, live=True):
    """Answer the hello probe the way the gateway would."""
    prov._hello_body = lambda: ({"name": "magpie", "version": "0.1.0"} if live else None)
    return prov


# --- is it there -----------------------------------------------------------


def test_magpie_is_not_a_card_the_user_configures():
    """The Providers screen lists what *he* signs in to; magpie is behind it.

    User ruling (11-09 20:0x): 「不要这样弄。我说了前端还是用我们自己的操作逻辑。
    要配置 workbuddy 就要在相应的卡片点击加号配置的」 — so WorkBuddy keeps its own
    card and its own "+", and magpie (which ships with us) must not appear as
    one more thing to configure.
    """
    from quota_hub.registry import ADAPTED_APPS

    prov = _with_gateway(_provider(_http({})))
    assert prov.present() is False, "a leg that is not a product must not become a card"
    assert "magpie" not in ADAPTED_APPS, "the card list is the table of products"


def test_a_gateway_that_answers_is_live():
    prov = _with_gateway(_provider(_http({})))
    assert prov.credential_health() == (True, "ok")


def test_a_gateway_that_is_not_there_says_so_with_a_reason_the_screen_owns():
    """The reason must be one the four locales already know how to say.

    A machine word we invent would reach the card's tooltip verbatim.
    """
    prov = _with_gateway(_provider(_http({})), live=False)
    live, reason = prov.credential_health()
    assert live is False
    assert reason == "no_desktop_credential"


def test_something_else_on_that_port_is_not_magpie():
    prov = _provider(_http({}))
    prov._hello_body = lambda: {"name": "something-else"}
    assert prov.credential_health() == (False, "no_desktop_credential")


# --- where it is -----------------------------------------------------------


def test_the_port_comes_from_magpies_own_settings(no_local_settings):
    no_local_settings.parent.mkdir(parents=True, exist_ok=True)
    no_local_settings.write_text(json.dumps({"port": 4321}), encoding="utf-8")
    prov = _provider(_http({}))
    assert prov._base_url() == "http://127.0.0.1:4321"


def test_a_missing_settings_file_falls_back_to_their_default_port(no_local_settings):
    prov = _provider(_http({}))
    assert prov._base_url() == "http://127.0.0.1:3425"


# --- the roster ------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_roster_is_theirs_and_it_carries_their_own_numbers():
    http = _http({"/v1/models": httpx.Response(200, json=_models_payload())})
    prov = _with_gateway(_provider(http))

    accounts = await prov.discover_accounts()
    assert len(accounts) == 1, "the gateway needs no credential of ours"

    got = await prov.refresh_models(accounts[0])
    assert got == ["magpie/opencode-zen-free/big-pickle", "magpie/workbuddy/hy4free"], got
    assert prov.served_models == tuple(got)

    big_pickle = prov.tuning("magpie/opencode-zen-free/big-pickle")
    assert big_pickle["context"]["choices"] == [200000], big_pickle
    assert big_pickle["context"]["max"] == 200000
    assert big_pickle["max_output"] == 32000
    assert big_pickle["efforts"] == ["low", "high"], big_pickle
    assert big_pickle["default_effort"] in big_pickle["efforts"]

    hy4 = prov.tuning("magpie/workbuddy/hy4free")
    assert hy4["context"]["choices"] == [960000], hy4
    assert "efforts" not in hy4, "a model that says nothing keeps the generic controls"


@pytest.mark.asyncio
async def test_a_gateway_that_cannot_be_read_keeps_what_we_had():
    http = _http({"/v1/models": httpx.Response(500, json={"error": "boom"})})
    prov = _with_gateway(_provider(http))
    accounts = await prov.discover_accounts()

    assert await prov.refresh_models(accounts[0]) is None
    assert prov.served_models == (), "a failed read must not invent a roster"


# --- the turn --------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_turn_goes_out_under_our_own_key_name():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={
            "id": "chatcmpl-x",
            "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"},
                         "finish_reason": "stop"}],
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        prov = _with_gateway(_provider(http))
        accounts = await prov.discover_accounts()
        out = await prov.chat(accounts[0], {
            "model": "magpie/opencode-zen-free/big-pickle",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "function", "function": {"name": "read_file"}}],
        })

    assert seen["url"].endswith("/v1/chat/completions")
    assert seen["auth"] == "Bearer magpie-plobi", "our calls are counted under our own name"
    assert seen["body"]["model"] == "opencode-zen-free/big-pickle", "their id, not ours"
    assert seen["body"]["stream"] is False
    assert seen["body"]["tools"], "the tool list has to reach the gateway"
    assert out["model"] == "magpie/opencode-zen-free/big-pickle"
    assert out["choices"][0]["message"]["content"] == "hi"
