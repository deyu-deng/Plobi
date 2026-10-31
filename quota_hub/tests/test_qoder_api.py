"""The Qoder leg's contracts, against the frame shape measured on the real door.

MockTransport + a temp PLOBI_HOME: no vendor call, no credential. The envelope is asserted
exactly as observed 2026-10-31 (a `data:` line whose `body` is a *stringified* OpenAI chunk,
`statusCodeValue` beside it, `event:finish` as the end, and a duration-only final frame), so
a future normalization of it fails here instead of in a turn.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from quota_hub.providers.apps.qoder import oauth
from quota_hub.providers.apps.qoder.api import QoderProvider, frame_inner
from quota_hub.providers.base import UpstreamError

CHAT_URL_PART = "/algo/api/v2/service/pro/sse/agent_chat_generation"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path / "home"))
    oauth.write_credential({"token": "dt-test", "refresh_token": "rt-test",
                            "user_id": "01a0cd78-93a3-7964-b17f-8ca5992648c0",
                            "expires_at": "2099-01-01T00:00:00Z"})
    return tmp_path


def _provider(routes: dict[str, object]):
    seen: list[tuple[str, str, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.content))
        for key, payload in routes.items():
            if request.url.path.endswith(key):
                if isinstance(payload, tuple):
                    return httpx.Response(payload[0], text=payload[1])
                if isinstance(payload, list):  # SSE lines
                    return httpx.Response(200, text="\n".join(payload) + "\n",
                                         headers={"content-type": "text/event-stream"})
                return httpx.Response(200, json=payload)
        return httpx.Response(404, text="unrouted")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    return QoderProvider({"model_prefix": "qoder", "display_name": "Qoder"}, http), seen


def _frame(inner: dict, status: int = 200) -> str:
    return ("data:" + json.dumps({"headers": {"Content-Type": ["application/json"]},
                                  "body": json.dumps(inner, ensure_ascii=False),
                                  "statusCodeValue": status, "statusCode": "OK"},
                                 ensure_ascii=False))


def _chunk(**fields) -> dict:
    delta = {}
    for name in ("content", "reasoning_content"):
        if name in fields:
            delta[name] = fields.pop(name)
    if "tool_calls" in fields:
        delta["tool_calls"] = fields.pop("tool_calls")
    chunk = {"choices": [{"index": 0, "delta": delta, "finish_reason": fields.pop("finish_reason", None)}],
             "model": "auto", "object": "chat.completion.chunk", "id": "gen_upstream"}
    if fields.get("usage"):
        chunk["usage"] = fields.pop("usage")
    return chunk


def test_the_catalogue_takes_the_chat_role_and_drops_what_upstream_disabled():
    prov, _ = _provider({"/algo/api/v2/model/list": {
        "chat": [{"key": "auto", "is_reasoning": True, "enable": True, "max_input_tokens": 200000},
                 {"key": "kmodel", "is_reasoning": True, "enable": True},
                 {"key": "hidden_one", "display_name": "Hidden One", "enable": False}]}})
    acc = asyncio.run(prov.discover_accounts())[0]
    keys = asyncio.run(prov.refresh_models(acc))
    assert keys == ["auto", "kmodel"], keys
    assert "hidden-one" not in keys, "a role upstream disabled must not be offered"
    assert prov.served_models == ("qoder/auto", "qoder/kmodel")


def test_the_id_we_publish_is_the_name_upstream_reports_its_key_stays_internal():
    """Measured 2026-10-31: the door calls its models `dfmodel` / `kmodel_latest` and calls
    what a human sees `DeepSeek-Flash` / `Kimi-K3.1-Pro`. The desktop has no name field to
    read - it prettifies the id - so the name has to live in the id.
    """
    prov, _ = _provider({"/algo/api/v2/model/list": {"chat": [
        {"key": "dmodel", "display_name": "DeepSeek-V4-Pro", "is_reasoning": True},
        {"key": "dfmodel", "display_name": "DeepSeek-Flash", "is_reasoning": False},
        {"key": "kmodel_latest", "display_name": "Kimi-K3.1-Pro"},
        {"key": "same_name_one", "display_name": "Twice"},
        {"key": "same_name_two", "display_name": "Twice"}]}})
    acc = asyncio.run(prov.discover_accounts())[0]
    asyncio.run(prov.refresh_models(acc))
    assert prov.served_models == ("qoder/deepseek-v4-pro", "qoder/deepseek-flash",
                                  "qoder/kimi-k3.1-pro", "qoder/twice", "qoder/twice-same-name-two")

    key, body = prov._body({"model": "qoder/kimi-k3.1-pro",
                            "messages": [{"role": "user", "content": "hi"}]}, "rec")
    assert key == "kmodel_latest" and body["model_config"]["key"] == "kmodel_latest"
    # a caller still holding the raw key is not stranded
    assert prov._body({"model": "qoder/dfmodel", "messages": []}, "rec")[0] == "dfmodel"


def test_a_turn_is_assembled_from_frames_and_the_tool_call_survives_the_split():
    usage = {"completion_tokens": 83, "credits": 0.1224, "completion_tokens_details": {"reasoning_tokens": 55}}
    frames = [
        _frame(_chunk(reasoning_content="我们需要")),
        _frame(_chunk(content="好")),
        _frame(_chunk(tool_calls=[{"index": 0, "id": "call_1", "function": {"name": "plobi_probe_read",
                                                                           "arguments": '{"path": "D:/Proj'}}])),
        _frame(_chunk(tool_calls=[{"index": 0, "function": {"arguments": 'ects/Plobi/AGENTS.md"}'}}])),
        _frame(_chunk(finish_reason="tool_calls", usage=usage)),
        "data:{" + json.dumps({"firstTokenDuration": 0.4, "serverDuration": 1.0, "totalDuration": 1.1}) + "}",
        "event:finish",
    ]
    prov, seen = _provider({CHAT_URL_PART: frames})
    acc = asyncio.run(prov.discover_accounts())[0]
    out = asyncio.run(prov.chat(acc, {
        "model": "qoder/kmodel", "messages": [{"role": "user", "content": "读那个文件"}],
        "tools": [{"type": "function", "function": {"name": "plobi_probe_read"}}],
        "tool_choice": "auto", "max_tokens": 300}))

    message = out["choices"][0]["message"]
    assert out["choices"][0]["finish_reason"] == "tool_calls"
    assert message["content"] is None, "a tool call answers with no text"
    assert message["tool_calls"] == [{"id": "call_1", "type": "function", "function": {
        "name": "plobi_probe_read", "arguments": '{"path": "D:/Projects/Plobi/AGENTS.md"}'}}]
    assert message["reasoning"] == "我们需要"
    assert out["usage"] == usage
    assert out["model"] == "qoder/kmodel", "the caller's namespace, not the routed 'auto'"

    # the body the door demanded: the model key inside BOTH chat_context and model_config
    sent = json.loads(seen[-1][2].decode()) if seen[-1][2][:1] == b"{" else None
    if sent is None:  # the body is Encode=1, so the caller's bytes are the codec's output
        from quota_hub.providers.apps.qoder import cosy
        sent = json.loads(cosy.decode_body(seen[-1][2].decode()))
    assert sent["chat_context"]["extra"]["modelConfig"]["key"] == "kmodel"
    assert sent["model_config"]["key"] == "kmodel"
    assert sent["chat_record_id"] == sent["session_id"]
    assert sent["tools"][0]["function"]["name"] == "plobi_probe_read"
    assert sent["parameters"]["tool_choice"] == "auto"


def test_streaming_stops_at_event_finish_and_renames_the_model_per_chunk():
    frames = [_frame(_chunk(content="你")), _frame(_chunk(content="好")),
              "data:" + json.dumps({"firstTokenDuration": 0.1}), "event:finish",
              _frame(_chunk(content="不该出现"))]
    prov, _ = _provider({CHAT_URL_PART: frames})
    acc = asyncio.run(prov.discover_accounts())[0]

    async def _go():
        return [c async for c in prov.chat_stream(acc, {"model": "qoder/fast",
                                                       "messages": [{"role": "user", "content": "hi"}]})]

    chunks = asyncio.run(_go())
    assert [c["choices"][0]["delta"]["content"] for c in chunks] == ["你", "好"]
    assert {c["model"] for c in chunks} == {"qoder/fast"}


def test_the_door_lies_about_status_and_the_business_code_is_the_truth():
    acc_holder = {}

    def _turn(status_body: tuple[int, str]):
        prov, _ = _provider({CHAT_URL_PART: status_body})
        acc = acc_holder.setdefault("acc", asyncio.run(prov.discover_accounts())[0])
        with pytest.raises(UpstreamError) as caught:
            asyncio.run(prov.chat(acc, {"model": "qoder/fast", "messages": []}))
        return caught.value

    expired = _turn((403, json.dumps({"code": "105", "message": "Login expired"})))
    assert expired.status == 401 and expired.reauth is True, "105 is a click, not a retry"

    queued = _turn((403, json.dumps({"code": "10605", "data": {"retryAfterSeconds": 7}})))
    assert queued.status == 429 and queued.retryable and queued.cooldown == 7.0

    stale_client = _turn((430, json.dumps({"code": "430", "message": "client version too low"})))
    assert stale_client.status == 430 and stale_client.retryable is False


def test_an_expired_lease_raises_reauth_before_the_door_is_dialed():
    from quota_hub.providers.base import Account

    oauth.write_credential({"token": "dt-test", "user_id": "u", "expires_at": "2000-01-01T00:00:00Z"})
    prov, seen = _provider({CHAT_URL_PART: []})
    with pytest.raises(UpstreamError) as caught:
        asyncio.run(prov.chat(Account(id="qoder-device", provider="qoder"),
                              {"model": "qoder/fast", "messages": []}))
    assert caught.value.reauth is True
    assert seen == [], "a dead lease must not reach the network"


def test_the_frame_envelope_is_asserted_not_tolerated():
    assert frame_inner(json.loads(json.dumps(
        {"headers": {}, "body": json.dumps({"choices": [{"index": 0, "delta": {"content": "x"}}]})})))["choices"][0][
        "delta"]["content"] == "x"
    assert frame_inner({"body": "[DONE]"}) is None
    assert frame_inner({"body": "not json"}) is None
    assert frame_inner({"firstTokenDuration": 0.4}) is None, "a duration frame carries no chunk"
