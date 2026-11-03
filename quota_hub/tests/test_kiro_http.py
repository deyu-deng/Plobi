"""Kiro's streaming API leg: the parser against the bytes this machine actually saw.

Local only - a temp SQLite the test writes, MockTransport, and one embedded capture from
`runtime.us-east-1.kiro.dev` on 2026-10-31 for a turn that had to call our own tool. The
frame layout is therefore evidence, not a guess.
"""
from __future__ import annotations

import asyncio
import base64
import json
import sqlite3
import time

import httpx
import pytest

from quota_hub.providers.apps.kiro import http as kiro_http
from quota_hub.providers.apps.kiro.cli import KiroCliProvider
from quota_hub.providers.base import Account, UpstreamError

MEASURED_FRAMES_B64 = "AAAAhQAAAGBRloHTCzpldmVudC10eXBlBwAQaW5pdGlhbC1yZXNwb25zZQ06Y29udGVudC10eXBlBwAaYXBwbGljYXRpb24veC1hbXotanNvbi0xLjANOm1lc3NhZ2UtdHlwZQcABWV2ZW50eyJjb252ZXJzYXRpb25JZCI6IiJ9d61O6AAAAJMAAABckVmfdgs6ZXZlbnQtdHlwZQcAFmFzc2lzdGFudFJlc3BvbnNlRXZlbnQNOmNvbnRlbnQtdHlwZQcAEGFwcGxpY2F0aW9uL2pzb24NOm1lc3NhZ2UtdHlwZQcABWV2ZW50eyJjb250ZW50IjoiIiwibW9kZWxJZCI6ImRlZXBzZWVrLTMuMiJ9Iq2UZAAAALAAAABcFzjKogs6ZXZlbnQtdHlwZQcAFmFzc2lzdGFudFJlc3BvbnNlRXZlbnQNOmNvbnRlbnQtdHlwZQcAEGFwcGxpY2F0aW9uL2pzb24NOm1lc3NhZ2UtdHlwZQcABWV2ZW50eyJjb250ZW50IjoiXG5cbjzvvZxEU01M772cZnVuY3Rpb25fY2FsbHMiLCJtb2RlbElkIjoiZGVlcHNlZWstMy4yIn2uJSIGAAAAqgAAAFLa0GiGCzpldmVudC10eXBlBwAMdG9vbFVzZUV2ZW50DTpjb250ZW50LXR5cGUHABBhcHBsaWNhdGlvbi9qc29uDTptZXNzYWdlLXR5cGUHAAVldmVudHsibmFtZSI6InBsb2JpX3Byb2JlX3JlYWQiLCJ0b29sVXNlSWQiOiJ0b29sdXNlX0RSZmlqMFhUbmhEaG9ZNW1XYVRNTVkifddakDwAAADgAAAAUsiTKG4LOmV2ZW50LXR5cGUHAAx0b29sVXNlRXZlbnQNOmNvbnRlbnQtdHlwZQcAEGFwcGxpY2F0aW9uL2pzb24NOm1lc3NhZ2UtdHlwZQcABWV2ZW50eyJpbnB1dCI6IntcInBhdGhcIjogXCJEOi9Qcm9qZWN0cy9QbG9iaS9BR0VOVFMubWRcIn0iLCJuYW1lIjoicGxvYmlfcHJvYmVfcmVhZCIsInRvb2xVc2VJZCI6InRvb2x1c2VfRFJmaWowWFRuaERob1k1bVdhVE1NWSJ9N4pZZwAAALYAAABSf8ASBQs6ZXZlbnQtdHlwZQcADHRvb2xVc2VFdmVudA06Y29udGVudC10eXBlBwAQYXBwbGljYXRpb24vanNvbg06bWVzc2FnZS10eXBlBwAFZXZlbnR7Im5hbWUiOiJwbG9iaV9wcm9iZV9yZWFkIiwic3RvcCI6dHJ1ZSwidG9vbFVzZUlkIjoidG9vbHVzZV9EUmZpajBYVG5oRGhvWTVtV2FUTU1ZIn3VgPF9AAAAkwAAAFyRWZ92CzpldmVudC10eXBlBwAWYXNzaXN0YW50UmVzcG9uc2VFdmVudA06Y29udGVudC10eXBlBwAQYXBwbGljYXRpb24vanNvbg06bWVzc2FnZS10eXBlBwAFZXZlbnR7ImNvbnRlbnQiOiIiLCJtb2RlbElkIjoiZGVlcHNlZWstMy4yIn0irZRkAAAAfAAAAFOrYtLpCzpldmVudC10eXBlBwANbWV0YWRhdGFFdmVudA06Y29udGVudC10eXBlBwAQYXBwbGljYXRpb24vanNvbg06bWVzc2FnZS10eXBlBwAFZXZlbnR7InN0b3BSZWFzb24iOiJUT09MX1VTRSJ9XgX7uQAAAJQAAABXtKua7gs6ZXZlbnQtdHlwZQcAEWNvbnRleHRVc2FnZUV2ZW50DTpjb250ZW50LXR5cGUHABBhcHBsaWNhdGlvbi9qc29uDTptZXNzYWdlLXR5cGUHAAVldmVudHsiY29udGV4dFVzYWdlUGVyY2VudGFnZSI6Mi41MDE4MjkzODU3NTc0NDYzfZkcTz4AAACoAAAAU9cXC3ALOmV2ZW50LXR5cGUHAA1tZXRlcmluZ0V2ZW50DTpjb250ZW50LXR5cGUHABBhcHBsaWNhdGlvbi9qc29uDTptZXNzYWdlLXR5cGUHAAVldmVudHsidW5pdCI6ImNyZWRpdCIsInVuaXRQbHVyYWwiOiJjcmVkaXRzIiwidXNhZ2UiOjAuMDI1NjkwMDM2NDg0MjQ1NDQyfYsOG+4="


def _frames() -> bytes:
    return base64.b64decode(MEASURED_FRAMES_B64)


@pytest.fixture
def store(tmp_path):
    """A kiro-cli-shaped database the test owns; the real one is never opened."""
    path = tmp_path / "data.sqlite3"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE auth_kv(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    con.execute("CREATE TABLE state(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    expires = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
    arn = "arn:aws:codewhisperer:eu-central-1:1:profile/AAA"
    con.execute("INSERT INTO auth_kv VALUES ('kirocli:social:token', ?)",
                (json.dumps({"access_token": "at-test", "refresh_token": "rt-test",
                             "expires_at": expires, "provider": "social", "profile_arn": arn}),))
    con.execute("INSERT INTO state VALUES ('api.codewhisperer.profile', ?)",
                (json.dumps({"arn": arn, "profile_name": "Social_Default_Profile"}),))
    con.commit()
    con.close()
    return str(path)


def _provider(config: dict, routes: dict, seen: list) -> KiroCliProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url), request.headers, request.content))
        payload = routes.get(request.url.path)
        if payload is None:
            return httpx.Response(404, text="unrouted")
        if isinstance(payload, tuple):
            status, body, ctype = payload
            return httpx.Response(status, content=body, headers={"content-type": ctype})
        return httpx.Response(200, json=payload)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    return KiroCliProvider({"binary": "kiro-cli", **config}, http)


def _account() -> Account:
    acc = Account(id="kiro_cli-default", provider="kiro_cli")
    acc.cred.access_token = "at-test"
    acc.cred.extra.update({"kiro_profile_arn": "arn:aws:codewhisperer:eu-central-1:1:profile/AAA",
                           "kiro_region": "eu-central-1"})
    return acc


def test_the_credential_itself_is_the_door_and_the_lease_is_a_separate_question(tmp_path, store):
    prov = _provider({"data_sqlite": store}, {}, [])
    assert prov._auth()["access_token"] == "at-test"
    assert prov._auth()["region"] == "eu-central-1", "region comes from the profile ARN, not a default"
    assert prov.credential_health() == (True, "ok")

    off = KiroCliProvider({"data_sqlite": store, "binary": "kiro-cli"}, None)
    assert off._auth()["access_token"] == "at-test", "the door is the credential itself, no config switch"
    assert off.credential_health() == (True, "ok"), "reading the lease needs no HTTP client"

    con = sqlite3.connect(store)
    con.execute("UPDATE auth_kv SET value = ? WHERE key = 'kirocli:social:token'",
                (json.dumps({"access_token": "at", "expires_at": "2000-01-01T00:00:00Z"}),))
    con.commit()
    con.close()
    assert prov.credential_health() == (False, "credential_expired")


def test_the_catalogue_keeps_our_namespace_and_follows_the_accounts_region(store):
    routes = {"/": {"defaultModel": "deepseek-3.2", "models": [
        {"modelId": "deepseek-3.2", "tokenLimits": {"maxInputTokens": 164000}},
        {"modelId": "glm-5"}]}}
    seen: list = []
    prov = _provider({"data_sqlite": store}, routes, seen)
    keys = asyncio.run(prov.refresh_models(_account()))
    assert keys == ["deepseek-3.2", "glm-5"]
    assert prov.served_models == ("kiro_cli/deepseek-3.2", "kiro_cli/glm-5")

    method, url, headers, body = seen[-1]
    assert (method, url) == ("POST", "https://management.eu-central-1.kiro.dev/"), url
    assert headers["X-Amz-Target"] == "AmazonCodeWhispererService.ListAvailableModels"
    assert headers["Authorization"] == "Bearer at-test"
    assert json.loads(body)["profileArn"].endswith("profile/AAA")
    assert "us-east-1" not in url, "the region must come from the ARN, not the reference default"


def test_the_measured_frames_decode_to_a_tool_call_closed_by_stop_and_a_stop_reason():
    events = [(event, payload) for event, _headers, payload in kiro_http.decode_frames(_frames())]
    names = [event for event, _payload in events]
    assert "assistantResponseEvent" in names and "toolUseEvent" in names and "metadataEvent" in names
    tool = [payload for event, payload in events if event == "toolUseEvent"]
    assert any(part.get("name") == "plobi_probe_read" for part in tool)
    assert any(part.get("stop") is True for part in tool), "the call closes with stop, not a frame type"
    assert any(part.get("stopReason") == "TOOL_USE" for event, part in events if event == "metadataEvent")


def test_a_corrupt_frame_fails_closed():
    broken = bytearray(_frames())
    broken[10] ^= 0xFF  # the prelude CRC, not the payload
    assert list(kiro_http.decode_frames(bytes(broken))) == []


def test_a_turn_is_translated_and_the_tool_call_survives_the_three_frame_shape(store):
    seen: list = []
    routes = {"/": (200, _frames(), "application/vnd.amazon.eventstream")}
    prov = _provider({"data_sqlite": store}, routes, seen)
    req = {"model": "kiro_cli/deepseek-3.2",
           "messages": [{"role": "user", "content": "读那个文件"}],
           "tools": [{"type": "function", "function": {"name": "plobi_probe_read",
                                                      "parameters": {"type": "object", "properties": {}}}}]}
    out = asyncio.run(prov.chat(_account(), req))
    message = out["choices"][0]["message"]
    assert out["choices"][0]["finish_reason"] == "tool_calls"
    assert message["content"] is None
    assert message["tool_calls"][0]["function"]["name"] == "plobi_probe_read"
    assert json.loads(message["tool_calls"][0]["function"]["arguments"]) == {"path": "D:/Projects/Plobi/AGENTS.md"}
    assert out["model"] == "kiro_cli/deepseek-3.2"

    _method, _path, headers, body = seen[-1]
    sent = json.loads(body)
    assert headers["X-Amz-Target"].endswith("GenerateAssistantResponse")
    state = sent["conversationState"]
    assert state["agentTaskType"] == "vibe", "required: without it the API answers REQUEST_BODY_INVALID"
    tool = state["currentMessage"]["userInputMessage"]["userInputMessageContext"]["tools"][0]
    assert isinstance(tool["toolSpecification"]["inputSchema"]["json"], dict), "a stringified schema is refused"
    assert state["currentMessage"]["userInputMessage"]["modelId"] == "deepseek-3.2"
    assert "history" not in state, "an empty history is omitted, not sent as an empty list"
    assert sent["profileArn"].endswith("profile/AAA")


def test_a_rate_limit_wearing_http_200_is_not_parsed_as_frames(store):
    envelope = json.dumps({"__type": "ThrottlingException", "message": "Too many tokens"}).encode()
    prov = _provider({"data_sqlite": store}, {"/": (200, envelope, "application/json")}, [])
    with pytest.raises(UpstreamError) as caught:
        asyncio.run(prov.chat(_account(), {"model": "kiro_cli/glm-5", "messages": [{"role": "user", "content": "hi"}]}))
    assert caught.value.status == 429 and caught.value.retryable


def test_a_tool_result_turn_replays_history_and_carries_tool_results(store):
    seen: list = []
    routes = {"/": (200, _frames(), "application/vnd.amazon.eventstream")}
    prov = _provider({"data_sqlite": store}, routes, seen)
    asyncio.run(prov.chat(_account(), {
        "model": "kiro_cli/deepseek-3.2",
        "messages": [{"role": "system", "content": "你是 Plobi"},
                    {"role": "user", "content": "读文件"},
                    {"role": "assistant", "tool_calls": [{"id": "t1", "type": "function",
                                                         "function": {"name": "plobi_probe_read",
                                                                      "arguments": "{\"path\": \"a\"}"}}]},
                    {"role": "tool", "tool_call_id": "t1", "content": "第一行"}]}))
    sent = json.loads(seen[-1][3])
    state = sent["conversationState"]
    context = state["currentMessage"]["userInputMessage"]["userInputMessageContext"]
    assert context["toolResults"][0]["toolUseId"] == "t1"
    assert state["history"][1]["assistantResponseMessage"]["toolUses"][0]["input"] == {"path": "a"}


def _encoded_frame(event_type: str, payload: dict) -> bytes:
    """Build one frame the way the parser reads it, so the round trip is checked both ways."""
    import struct
    import zlib

    block = b""
    for name, value in {":message-type": "event", ":event-type": event_type,
                        ":content-type": "application/json"}.items():
        raw = value.encode("utf-8")
        block += bytes([len(name)]) + name.encode("utf-8") + bytes([7]) + struct.pack(">H", len(raw)) + raw
    body = json.dumps(payload).encode("utf-8")
    total = 12 + len(block) + len(body) + 4
    prelude = struct.pack(">II", total, len(block))
    prelude += struct.pack(">I", zlib.crc32(prelude) & 0xFFFFFFFF)
    message = prelude + block + body
    message += struct.pack(">I", zlib.crc32(message) & 0xFFFFFFFF)
    return message


def test_kiros_token_counts_arrive_on_their_own_frame(store):
    """metadataEvent only carries stopReason; `meteringEvent` is where the usage lives."""
    stream = _frames() + _encoded_frame("meteringEvent", {
        "usage": {"inputTokens": 1234, "outputTokens": 56}, "stop": True})
    prov = _provider({"data_sqlite": store}, {"/": (200, stream, "application/vnd.amazon.eventstream")}, [])
    out = asyncio.run(prov.chat(_account(), {"model": "kiro_cli/glm-5",
                                             "messages": [{"role": "user", "content": "hi"}]}))
    assert out["usage"] == {"prompt_tokens": 1234, "completion_tokens": 56, "total_tokens": 1290}, out["usage"]


def test_a_locked_read_does_not_lose_the_cached_lease(tmp_path, store):
    """A refused read is not evidence the session ended (measured: kiro-cli holds
    this database open while it runs); the last readable lease keeps serving."""
    prov = _provider({"data_sqlite": store}, {}, [])
    assert prov._auth()["access_token"] == "at-test"
    missing = tmp_path / "gone.sqlite3"          # what a refused read looks like
    prov.config["data_sqlite"] = str(missing)
    assert prov._auth()["access_token"] == "at-test", "a failed read is not evidence the session ended"
    assert prov.credential_health() == (True, "ok")


def test_without_a_credential_every_door_degrades_explicitly(tmp_path):
    prov = KiroCliProvider({"data_sqlite": str(tmp_path / "missing.sqlite3"), "binary": "kiro-cli"}, None)
    assert prov.signed_in() == (False, "no_credential")
    assert prov.credential_health() == (False, "no_credential")
    assert asyncio.run(prov.discover_accounts()) == []
    assert asyncio.run(prov.refresh_models(_account())) is None
    with pytest.raises(UpstreamError) as caught:
        asyncio.run(prov.chat(_account(), {"model": "kiro_cli/default",
                                           "messages": [{"role": "user", "content": "hi"}]}))
    assert caught.value.status == 401 and caught.value.reauth
