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
import sqlite3
import time

import httpx
import pytest
from quota_hub.providers.apps.opencode.api import OpencodeApiProvider, read_credential

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
            prov = OpencodeApiProvider({"base_url": BASE}, http)
            accounts = await prov.discover_accounts()
            assert accounts, "a present credential must yield one account"
            assert prov.served_models == ("opencode_api/big-pickle", "opencode_api/beam-free")
            assert prov.credential_health() == (True, "ok")

    asyncio.run(asyncio.wait_for(_go(), timeout=30))


def test_no_upstream_list_means_no_models_at_all(tmp_path, monkeypatch):
    monkeypatch.setenv("QUOTA_HUB_OPENCODE_STORE", _store(tmp_path))

    async def _go():
        async with _http({"/v1/models": httpx.Response(403, json={"error": "forbidden"})}) as http:
            prov = OpencodeApiProvider({"base_url": BASE, "models": ["opencode_api/declared-only"]}, http)
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
            prov = OpencodeApiProvider({"base_url": BASE}, http)
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
            prov = OpencodeApiProvider({"base_url": BASE}, http)
            assert prov.credential_health() == (True, "ok")

    asyncio.run(asyncio.wait_for(_go(), timeout=30))
