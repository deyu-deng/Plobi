"""Verification for the OpenCode Zen leg (`quota_hub/providers/apps/opencode/api.py`).

裁定 204: a `no_tools` leg is fixed by reading that product's own local credential and
talking to its own HTTP API through the OpenAI dialect — text-only is no longer an
acceptable verdict. This leg is the first end-to-end instance, so the tests pin the four
things that could make it lie again:

* model names come from the upstream's `/models`, never from a declared list;
* a failed/unreadable `/models` yields **no** models (the leg disappears, it does not
  fall back to a hand-written list);
* an expired local credential reports `credential_expired` (the card keeps its `+`);
* a tool-bearing turn passes `tool_calls` straight through, and `tools` reaches the body.

The real store is never touched: `QUOTA_HUB_OPENCODE_STORE` points at a temp SQLite with
the same two columns OpenCode writes (`integration_id`, `value`, `active`, `time_updated`).

Run:
    cd Code && scripts/run_tests.sh -j 1 quota_hub/tests/test_opencode_api.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time

import httpx
import pytest
from quota_hub.providers.apps.opencode.api import OpencodeApiProvider, read_credential
from quota_hub.providers.base import UpstreamError

BASE = "https://zen.test/v1"


def _store(tmp_path, *, expires_in: float | None = 3600.0, present: bool = True) -> str:
    path = tmp_path / "opencode.db"
    if not present:
        return str(path)
    con = sqlite3.connect(str(path))
    con.execute(
        "create table credential (id text, integration_id text, label text, value text, "
        "connector_id text, method_id text, active integer, time_created integer, time_updated integer)"
    )
    expires = int((time.time() + expires_in) * 1000) if expires_in is not None else 0
    value = json.dumps({"type": "oauth", "access": "test-access", "refresh": "test-refresh", "expires": expires, "methodID": "code"})
    con.execute(
        "insert into credential values ('c1','opencode','Personal',? ,null,null,1,0,1)", (value,)
    )
    con.commit()
    con.close()
    return str(path)


def _http(routes: dict[str, httpx.Response]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.path
        if key in routes:
            return routes[key]
        return httpx.Response(404, json={"error": "route"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _models(*names: str) -> httpx.Response:
    return httpx.Response(200, json={"object": "list", "data": [{"id": n, "object": "model"} for n in names]})


def test_the_leg_reports_the_names_the_upstream_lists(tmp_path, monkeypatch):
    monkeypatch.setenv("QUOTA_HUB_OPENCODE_STORE", _store(tmp_path))

    async def _go():
        async with _http({"/v1/models": _models("big-pickle", "beam-free")}) as http:
            prov = OpencodeApiProvider({"base_url": BASE, "api_key": "test-key"}, http)
            accounts = await prov.discover_accounts()
            assert accounts, "a present credential must yield one account"
            assert prov.served_models == ("opencode_api/big-pickle", "opencode_api/beam-free")
            assert prov.credential_health() == (True, "ok")

    asyncio.run(asyncio.wait_for(_go(), timeout=30))


def test_no_upstream_list_means_no_models_at_all(tmp_path, monkeypatch):
    monkeypatch.setenv("QUOTA_HUB_OPENCODE_STORE", _store(tmp_path))

    async def _go():
        async with _http({"/v1/models": httpx.Response(403, json={"error": "forbidden"})}) as http:
            prov = OpencodeApiProvider({"base_url": BASE, "api_key": "test-key", "models": ["opencode_api/declared-only"]}, http)
            await prov.discover_accounts()
            # Ruling 204.5: a declared list is not a measurement.
            assert prov.served_models == ()

    asyncio.run(asyncio.wait_for(_go(), timeout=30))


def test_no_store_means_no_card_and_no_pretending(tmp_path, monkeypatch):
    monkeypatch.setenv("QUOTA_HUB_OPENCODE_STORE", _store(tmp_path, present=False))

    async def _go():
        async with _http({}) as http:
            prov = OpencodeApiProvider({"base_url": BASE}, http)
            assert prov.present() is False
            assert prov.credential_health() == (False, "no_desktop_credential")
            assert await prov.discover_accounts() == []
            assert prov.served_models == ()

    asyncio.run(asyncio.wait_for(_go(), timeout=30))


def test_an_expired_local_credential_is_reported_as_expired(tmp_path, monkeypatch):
    monkeypatch.setenv("QUOTA_HUB_OPENCODE_STORE", _store(tmp_path, expires_in=-60))

    async def _go():
        async with _http({"/v1/models": _models("big-pickle")}) as http:
            prov = OpencodeApiProvider({"base_url": BASE}, http)
            assert prov.credential_health() == (False, "credential_expired")
            assert await prov.discover_accounts() == []

    asyncio.run(asyncio.wait_for(_go(), timeout=30))


def test_a_turn_passes_tools_through_and_returns_tool_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("QUOTA_HUB_OPENCODE_STORE", _store(tmp_path))
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return _models("big-pickle")
        if request.url.path == "/v1/chat/completions":
            seen.update(json.loads(request.content.decode()))
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-1",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "tool_calls",
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}
                                ],
                            },
                        }
                    ],
                },
            )
        return httpx.Response(404, json={"error": "route"})

    async def _go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            prov = OpencodeApiProvider({"base_url": BASE, "api_key": "test-key"}, http)
            acc = (await prov.discover_accounts())[0]
            tools = [{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}]
            out = await prov.chat(
                acc,
                {
                    "model": "opencode_api/big-pickle",
                    "messages": [{"role": "user", "content": "hi"}],
                    "tools": tools,
                    "stream": True,  # must not leak into the upstream body
                },
            )
            assert seen["model"] == "big-pickle", "the leg's prefix is stripped for the upstream"
            assert seen["tools"] == tools, "tools reach the upstream"
            assert "stream" not in seen
            choice = out["choices"][0]
            assert choice["finish_reason"] == "tool_calls"
            assert choice["message"]["tool_calls"][0]["function"]["name"] == "read_file"
            assert prov.capabilities().tools is True

    asyncio.run(asyncio.wait_for(_go(), timeout=30))


def test_reading_the_real_store_never_raises_on_a_bad_file(tmp_path, monkeypatch):
    bad = tmp_path / "not-a-db.db"
    bad.write_text("definitely not sqlite", encoding="utf-8")
    monkeypatch.setenv("QUOTA_HUB_OPENCODE_STORE", str(bad))

    assert read_credential() is None


@pytest.mark.parametrize("expires", [None])
def test_an_unknown_expiry_is_still_a_present_credential(tmp_path, monkeypatch, expires):
    monkeypatch.setenv("QUOTA_HUB_OPENCODE_STORE", _store(tmp_path, expires_in=expires))

    async def _go():
        async with _http({"/v1/models": _models("big-pickle")}) as http:
            prov = OpencodeApiProvider({"base_url": BASE, "api_key": "test-key"}, http)
            assert prov.credential_health() == (True, "ok")

    asyncio.run(asyncio.wait_for(_go(), timeout=30))


def test_a_store_credential_is_believed_until_the_upstream_says_otherwise(tmp_path, monkeypatch):
    """2026-10-28 read one answer and concluded "no subscription".

    Re-measured 2026-10-31 on the same account with the right lane and the right identity
    shapes: a region-restricted model answers `403 RegionError` - which proves the gate does
    read his identity - while the free pool answers `429` with `retry-after`. That is a
    bucket that refills, not a purchase he lacks, so the credential is believed and the
    upstream is allowed to overturn it (see the next test).
    """
    monkeypatch.setenv("QUOTA_HUB_OPENCODE_STORE", _store(tmp_path))

    async def _go():
        async with _http({"/v1/models": _models("big-pickle", "beam-free")}) as http:
            prov = OpencodeApiProvider({"base_url": BASE}, http)
            assert prov.present() is True
            assert prov.credential_health() == (True, "ok")
            accounts = await prov.discover_accounts()
            assert accounts, "a real credential yields an account"
            assert prov.served_models == ("opencode_api/big-pickle", "opencode_api/beam-free")

    asyncio.run(asyncio.wait_for(_go(), timeout=30))


def test_a_spent_free_bucket_cools_down_by_exactly_what_upstream_asked_for(tmp_path, monkeypatch):
    """The verdict flips to "额度用完了" on the answer, not on our guess, and lifts itself."""
    monkeypatch.setenv("QUOTA_HUB_OPENCODE_STORE", _store(tmp_path))
    refusal = httpx.Response(
        429,
        json={"type": "error", "error": {"type": "FreeUsageLimitError",
                                         "message": "Free usage limit reached"}},
        headers={"retry-after": "1234"})

    async def _go():
        async with _http({"/v1/models": _models("big-pickle"),
                          "/v1/chat/completions": refusal}) as http:
            prov = OpencodeApiProvider({"base_url": BASE}, http)
            accounts = await prov.discover_accounts()
            with pytest.raises(UpstreamError) as caught:
                await prov.chat(accounts[0], {"model": "opencode_api/big-pickle",
                                              "messages": [{"role": "user", "content": "hi"}]})
            assert caught.value.status == 429 and caught.value.retryable
            assert caught.value.cooldown == 1234.0, "cool down by the seconds upstream named"
            assert prov.credential_health() == (False, "quota_exhausted")
            prov._free_blocked_until = time.time() - 1
            assert prov.credential_health() == (False, "quota_exhausted"), (
                "the cooldown is on disk now: forgetting the in-memory field does not "
                "unlock the leg")
            prov.clear_cooldown()
            assert prov.credential_health() == (True, "ok"), "a window that passed is over"

    asyncio.run(asyncio.wait_for(_go(), timeout=30))


def test_a_spent_free_bucket_is_still_spent_after_a_restart(tmp_path, monkeypatch):
    """A restart must not forget that the bucket is empty (user, 2026-11-08 21:2x).

    The picker offers this leg's models whenever the leg looks live. If the
    "the bucket is empty for another ten hours" answer only lived in memory, the
    very next restart would put those models back into the box — and the user
    would find out by clicking one.
    """
    monkeypatch.setenv("QUOTA_HUB_OPENCODE_STORE", _store(tmp_path))
    monkeypatch.setenv("QUOTA_HUB_LEG_COOLDOWN", str(tmp_path / "cooldowns.json"))
    refusal = httpx.Response(
        429,
        json={"type": "error", "error": {"type": "FreeUsageLimitError",
                                         "message": "Free usage limit reached"}},
        headers={"retry-after": "3600"})

    async def _go():
        async with _http({"/v1/models": _models("big-pickle"),
                          "/v1/chat/completions": refusal}) as http:
            prov = OpencodeApiProvider({"base_url": BASE}, http)
            accounts = await prov.discover_accounts()
            with pytest.raises(UpstreamError):
                await prov.chat(accounts[0], {"model": "opencode_api/big-pickle",
                                              "messages": [{"role": "user", "content": "hi"}]})
            assert (tmp_path / "cooldowns.json").exists(), "the cooldown must be on disk"
            from quota_hub import leg_cooldown as _lc
            assert _lc.remaining("opencode_api") > 3000, (
                "remaining=%s path=%s env=%s" % (
                    _lc.remaining("opencode_api"), _lc.cooldown_path(),
                    os.environ.get("QUOTA_HUB_LEG_COOLDOWN")))

        # …a restart: a brand-new provider instance, nothing in memory.
        async with _http({"/v1/models": _models("big-pickle")}) as http:
            fresh = OpencodeApiProvider({"base_url": BASE}, http)
            assert fresh.credential_health() == (False, "quota_exhausted"), (
                "the leg is still spent after a restart")
            wait = fresh.quota_retry_after()
            assert wait is not None and 3000 < wait <= 3600, (
                "and it still knows how long that lasts")

    asyncio.run(asyncio.wait_for(_go(), timeout=30))


def test_a_configured_key_turns_the_leg_on_without_any_store(tmp_path, monkeypatch):
    monkeypatch.setenv("QUOTA_HUB_OPENCODE_STORE", _store(tmp_path, present=False))

    async def _go():
        async with _http({"/v1/models": _models("jev-1.13")}) as http:
            prov = OpencodeApiProvider({"base_url": BASE, "api_key": "zen-key"}, http)
            assert prov.credential_health() == (True, "ok")
            accounts = await prov.discover_accounts()
            assert accounts and accounts[0].cred.access_token == "zen-key"
            assert prov.served_models == ("opencode_api/jev-1.13",)

    asyncio.run(asyncio.wait_for(_go(), timeout=30))


def _recording_http(routes: dict[str, httpx.Response], seen: list) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, dict(request.headers), request.content))
        if request.url.path in routes:
            return routes[request.url.path]
        return httpx.Response(404, json={"error": "route"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_the_vendor_default_splits_chat_and_list_across_the_two_lanes():
    """Measured 2026-10-31: chat lives on `/inference/openai/v1`, the list on `/inference/v1`.

    `/zen/v1`, which this leg and the hub's own config used to dial, is a legacy base: the
    working community proxy rewrites all four legacy forms on load, and non-CLI traffic
    against them is answered as quota-exhausted - so a paid subscription would still have
    kept hitting a wall we built ourselves.
    """
    prov = OpencodeApiProvider({"api_key": "k"}, _recording_http({}, []))
    assert prov.base_url == "https://opencode.ai/inference/openai/v1"
    assert prov.models_url == "https://opencode.ai/inference/v1/models"


def test_the_paid_lane_identity_headers_are_ours_to_send(tmp_path, monkeypatch):
    monkeypatch.setenv("QUOTA_HUB_OPENCODE_STORE", _store(tmp_path))
    seen: list = []
    chat = httpx.Response(200, json={"id": "c1", "object": "chat.completion", "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "好"}, "finish_reason": "stop"}]})
    http = _recording_http({"/v1/models": _models("big-pickle"), "/v1/chat/completions": chat}, seen)

    async def _go():
        async with http:
            prov = OpencodeApiProvider({"base_url": BASE, "api_key": "zen-key"}, http)
            accounts = await prov.discover_accounts()
            await prov.chat(accounts[0], {"model": "opencode_api/big-pickle",
                                          "messages": [{"role": "user", "content": "hi"}]})

    asyncio.run(asyncio.wait_for(_go(), timeout=30))
    assert len(seen) == 2
    for _method, _path, headers, _body in seen:
        assert headers["user-agent"] == "plobi-quota-hub/1.0", "our own identity, not a fake CLI"
        session = headers["x-opencode-session"]
        assert session.startswith("ses_") and len(session) > 16
        assert int(session[4:16], 16) > 0, "the gate reads the leading 12 hex as a creation timestamp"
        assert len(headers["x-opencode-project"]) == 40, "sha1 hex of `directory:<abs path>`"
    assert "x-org-id" not in seen[0][2], "no org header when the credential carries no org"


def test_an_org_bearing_credential_sends_the_org_header(tmp_path, monkeypatch):
    path = tmp_path / "org.sqlite3"
    con = sqlite3.connect(path)
    con.execute("create table credential (id text, integration_id text, label text, value text, "
                "connector_id text, method_id text, active integer, time_created integer, time_updated integer)")
    value = json.dumps({"type": "oauth", "access": "at", "refresh": "rt", "expires": 0, "methodID": "code",
                        "metadata": {"orgID": "org-77", "server": "https://opencode.ai/console"}})
    con.execute("insert into credential values ('c1','opencode','Personal',?,null,null,1,0,1)", (value,))
    con.commit()
    con.close()
    monkeypatch.setenv("QUOTA_HUB_OPENCODE_STORE", str(path))

    seen: list = []
    http = _recording_http({"/v1/models": _models("big-pickle")}, seen)

    async def _go():
        async with http:
            prov = OpencodeApiProvider({"base_url": BASE, "api_key": "zen-key"}, http)
            await prov.discover_accounts()

    asyncio.run(asyncio.wait_for(_go(), timeout=30))
    assert seen[0][2]["x-org-id"] == "org-77"


def test_streaming_forwards_each_frame_instead_of_folding_the_answer(tmp_path, monkeypatch):
    monkeypatch.setenv("QUOTA_HUB_OPENCODE_STORE", _store(tmp_path))
    sse = ("data: " + json.dumps({"id": "s1", "object": "chat.completion.chunk", "choices": [
        {"index": 0, "delta": {"content": "你"}, "finish_reason": None}]})
           + "\n\ndata: " + json.dumps({"id": "s1", "object": "chat.completion.chunk", "choices": [
        {"index": 0, "delta": {"content": "好"}, "finish_reason": "stop"}]}) + "\n\ndata: [DONE]\n\n")
    seen: list = []
    routes = {"/v1/models": _models("big-pickle"),
              "/v1/chat/completions": httpx.Response(200, text=sse,
                                                    headers={"content-type": "text/event-stream"})}
    http = _recording_http(routes, seen)

    async def _go():
        async with http:
            prov = OpencodeApiProvider({"base_url": BASE, "api_key": "zen-key"}, http)
            accounts = await prov.discover_accounts()
            return [chunk async for chunk in prov.chat_stream(
                accounts[0], {"model": "opencode_api/big-pickle",
                              "messages": [{"role": "user", "content": "hi"}]})]

    chunks = asyncio.run(asyncio.wait_for(_go(), timeout=30))
    assert [c["choices"][0]["delta"]["content"] for c in chunks] == ["你", "好"], "incremental, not one blob"
    sent = json.loads(seen[-1][3])
    assert sent["stream"] is True and sent["stream_options"]["include_usage"] is True, "usage only comes if asked"


def test_an_insufficient_funds_answer_ends_the_leg_instead_of_per_model_strikes(tmp_path, monkeypatch):
    """Measured 2026-10-31: every paid id answers `402 Insufficient account funds`.

    That answer is only reachable after the door accepts who is asking, so the leg is not a
    stranger - the account has no balance. It is an account-level fact, so one of them must
    retire the whole leg rather than let 32 ids each burn two refusals to leave the box one
    by one, and the card must then state the true reason.
    """
    monkeypatch.setenv("QUOTA_HUB_OPENCODE_STORE", _store(tmp_path))
    unpaid = httpx.Response(
        402,
        json={"type": "error", "error": {"type": "server_error",
                                         "message": "Upstream request failed: Insufficient account funds"}})

    async def _go():
        async with _http({"/v1/models": _models("claude-opus-5-5"),
                          "/v1/chat/completions": unpaid}) as http:
            prov = OpencodeApiProvider({"base_url": BASE}, http)
            accounts = await prov.discover_accounts()
            with pytest.raises(UpstreamError) as caught:
                await prov.chat(accounts[0], {"model": "opencode_api/claude-opus-5-5",
                                              "messages": [{"role": "user", "content": "hi"}]})
            assert caught.value.status == 402 and caught.value.retryable is False
            assert prov.credential_health() == (False, "no_subscription")
            prov._no_wallet = False
            assert prov.credential_health() == (True, "ok"), "a top-up needs no restart"

    asyncio.run(asyncio.wait_for(_go(), timeout=30))


def _rows(*rows: dict) -> httpx.Response:
    """The upstream's own rows, verbatim — the price and the limits included."""
    return httpx.Response(200, json={"object": "list", "data": list(rows)})


def _zen_row(model_id: str, *, price=None, context=None, output=None) -> dict:
    """One row in the shape Zen wrote on 2026-11-08."""
    row: dict = {"id": model_id, "object": "model"}
    if price is not None:
        row["cost"] = {"text": {"input": {"price": price}, "output": {"price": price}}}
    limit: dict = {}
    if context is not None:
        limit["context"] = context
    if output is not None:
        limit["output"] = output
    if limit:
        row["limit"] = limit
    return row


def test_the_membership_lane_serves_only_what_the_subscription_covers(tmp_path, monkeypatch):
    """A Zen membership is the free pool; the billed models are not part of it.

    Measured 2026-11-08 against the vendor's own list: of 42 rows, 11 are free and 31
    are billed per token. Serving all 42 promised a model the user cannot use — the
    user's own rule (11-08 20:1x): 「我配置不了就不应该出现在这个模型框里面」.
    """
    monkeypatch.setenv("QUOTA_HUB_OPENCODE_STORE", _store(tmp_path))

    async def _go():
        async with _http({"/v1/models": _rows(
            _zen_row("big-pickle", price=0),
            _zen_row("claude-opus-5-5", price=4),
        )}) as http:
            prov = OpencodeApiProvider({"base_url": BASE}, http)
            await prov.discover_accounts()
            assert prov.served_models == ("opencode_api/big-pickle",), prov.served_models

    asyncio.run(asyncio.wait_for(_go(), timeout=30))


def test_a_key_lane_serves_what_that_key_account_lists(tmp_path, monkeypatch):
    """A key is billable, so the upstream's own list IS that account's entitlement."""
    monkeypatch.setenv("QUOTA_HUB_OPENCODE_STORE", _store(tmp_path))

    async def _go():
        async with _http({"/v1/models": _rows(
            _zen_row("big-pickle", price=0),
            _zen_row("claude-opus-5-5", price=4),
        )}) as http:
            prov = OpencodeApiProvider({"base_url": BASE, "api_key": "zen-key"}, http)
            await prov.discover_accounts()
            assert prov.served_models == (
                "opencode_api/big-pickle",
                "opencode_api/claude-opus-5-5",
            ), prov.served_models

    asyncio.run(asyncio.wait_for(_go(), timeout=30))


def test_a_row_without_a_price_is_kept_because_the_leg_did_not_say(tmp_path, monkeypatch):
    """Silence is not a refusal — the same rule the tuning hook follows."""
    monkeypatch.setenv("QUOTA_HUB_OPENCODE_STORE", _store(tmp_path))

    async def _go():
        async with _http({"/v1/models": _rows(_zen_row("mystery-model"))}) as http:
            prov = OpencodeApiProvider({"base_url": BASE}, http)
            await prov.discover_accounts()
            assert prov.served_models == ("opencode_api/mystery-model",), prov.served_models

    asyncio.run(asyncio.wait_for(_go(), timeout=30))


def test_the_window_and_the_output_cap_come_from_the_models_own_row(tmp_path, monkeypatch):
    """Zen writes each model's window on its row; the picker reads it from there."""
    monkeypatch.setenv("QUOTA_HUB_OPENCODE_STORE", _store(tmp_path))

    async def _go():
        async with _http({"/v1/models": _rows(
            _zen_row("gpt-5.5", price=5, context=1050000, output=128000),
        )}) as http:
            prov = OpencodeApiProvider({"base_url": BASE, "api_key": "zen-key"}, http)
            await prov.discover_accounts()
            tuning = prov.tuning("opencode_api/gpt-5.5")
            assert tuning["context"] == {
                "adjustable": False,
                "choices": [1050000],
                "default": 1050000,
                "max": 1050000,
            }, tuning
            assert tuning["max_output"] == 128000, tuning
            assert prov.tuning("opencode_api/never-read") == {}, "a model we never read says nothing"

    asyncio.run(asyncio.wait_for(_go(), timeout=30))