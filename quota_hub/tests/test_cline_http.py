"""Cline's OpenAI door: the four wire facts measured on the real account 2026-10-31.

Everything here is local - MockTransport, a temp credential file, no vendor call and no
real token. The point of these tests is that the oddities are *measured*, so a future
"cleanup" that normalizes them fails loudly:

  ids come back inside ``{"data": ...}`` · ``cline-free/*`` is refused · ``expiresAt`` is
  milliseconds · with no credential the leg must keep the ACP behaviour it had before.
"""
from __future__ import annotations

import asyncio
import json
import time

import httpx

from quota_hub.providers.acp import AcpProvider
from quota_hub.providers.apps.cline import ClineCliProvider
from quota_hub.providers.base import Account

BASE = "https://api.cline.bot/api/v1"
TOOL_CALL = {"id": "call_1", "type": "function", "function": {"name": "plobi_probe_read", "arguments": "{}"}}


def _provider(routes: dict[str, object], config: dict | None = None):
    seen: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        key = f"{request.method} {request.url.path}{':stream' if body.get('stream') else ''}"
        seen.append((request.method, key, body))
        if key not in routes:
            return httpx.Response(404, json={"error": {"message": f"unrouted {key}"}})
        payload = routes[key]
        if isinstance(payload, tuple):
            return httpx.Response(200, content=payload[0].encode(),
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=payload)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    cfg = {"base_url": BASE, "transport": "acp", "binary": "cline", **(config or {})}
    return ClineCliProvider(cfg, http), seen


def _account(token: str = "") -> Account:
    acc = Account(id="cline_cli-default", provider="cline_cli")
    acc.cred.access_token = token
    return acc


def _completion(model: str) -> dict:
    """The wrapped shape Cline actually answers with (measured, not guessed)."""
    return {"data": {"choices": [{"index": 0, "message": {"content": None, "tool_calls": [TOOL_CALL]},
                                  "finish_reason": "tool_calls"}],
                     "model": model, "usage": {"total_tokens": 7}}}


def test_the_free_pool_never_enters_our_catalog_and_the_key_is_a_live_credential():
    prov, _ = _provider(
        {f"GET /api/v1/models": {"data": [{"id": "moonshotai/kimi-k3.1-pro"},
                                          {"id": "cline-free/solar-mini4"},
                                          {"id": "deepseek/deepseek-v4.1-flash-1017"}]}},
        {"api_key": "sk-cpb-test"},
    )
    assert prov.credential_health() == (True, "ok")
    assert asyncio.run(prov.refresh_models(_account("sk-cpb-test"))) == [
        "moonshotai/kimi-k3.1-pro", "deepseek/deepseek-v4.1-flash-1017"]
    assert prov.served_models == ("cline_cli/moonshotai/kimi-k3.1-pro", "cline_cli/deepseek/deepseek-v4.1-flash-1017")


def test_a_turn_is_unwrapped_and_the_caller_gets_its_own_model_id_back():
    completion = _completion("moonshotai/kimi-k3.1-pro")
    routes = {"POST /api/v1/chat/completions": completion,
              "POST /api/v1/chat/completions:stream":
                  (f"data: {json.dumps(completion)}\n\ndata: [DONE]\n\n",)}
    prov, seen = _provider(routes, {"api_key": "sk-cpb-test"})
    req = {"model": "cline_cli/moonshotai/kimi-k3.1-pro", "messages": [{"role": "user", "content": "读一个文件"}],
           "tools": [{"type": "function", "function": {"name": "plobi_probe_read"}}]}

    out = asyncio.run(prov.chat(_account("sk-cpb-test"), req))
    assert out["choices"][0]["finish_reason"] == "tool_calls"
    assert out["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "plobi_probe_read"
    assert out["model"] == "cline_cli/moonshotai/kimi-k3.1-pro", "the vendor's id must not leak to the caller"
    # what we dial is Cline's own id, prefix stripped
    assert seen[-1][2]["model"] == "moonshotai/kimi-k3.1-pro"

    async def _stream():
        return [chunk async for chunk in prov.chat_stream(_account("sk-cpb-test"), req)]

    chunks = asyncio.run(_stream())
    assert len(chunks) == 1 and chunks[0]["model"] == "cline_cli/moonshotai/kimi-k3.1-pro"


def test_the_lease_is_milliseconds_and_the_two_verdicts_stay_separate(tmp_path):
    store = tmp_path / "providers.json"

    def write(expires_ms: int) -> None:
        store.write_text(json.dumps(
            {"lastUsedProvider": "cline",
             "providers": {"cline": {"settings": {"auth": {"accessToken": "ya29.live",
                                                          "expiresAt": expires_ms}}}}}), encoding="utf-8")

    ms = lambda secs: int((time.time() + secs) * 1000)  # noqa: E731

    write(ms(3600))
    prov, _ = _provider({}, {"providers_json": str(store)})
    assert prov.signed_in() == (True, "ok") and prov.credential_health() == (True, "ok")
    assert prov._live_credential() == "ya29.live"

    # 60 s out with a 120 s skew: the credential is still HIS, but the leg cannot serve now
    write(ms(60))
    prov, _ = _provider({}, {"providers_json": str(store)})
    assert prov.signed_in() == (True, "ok"), "he is signed in; the lease is a second question"
    assert prov.credential_health() == (False, "credential_expired")

    # read as seconds this lease would look good for 57,000 years - the silent-death bug
    assert prov._live_credential() == ""


def test_with_no_credential_the_leg_is_the_acp_session_it_used_to_be(monkeypatch):
    marker = {"id": "from-acp"}

    async def _fake_chat(self, acc, oai_req):
        return marker

    monkeypatch.setattr(AcpProvider, "chat", _fake_chat)
    monkeypatch.setenv("CLINE_PROVIDERS_JSON", "/nonexistent/cline-providers.json")
    for var in ("CLINE_API_KEY", "CLINEPASS_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    prov, seen = _provider({f"POST {BASE}/chat/completions": _completion("x")}, {})
    assert prov.signed_in() == (False, "no_desktop_credential")
    out = asyncio.run(prov.chat(_account(), {"model": "cline_cli/anything", "messages": []}))
    assert out is marker and seen == [], "no token means no HTTP call"
