"""Full-stack end-to-end test of the OpenAI-compatible surface.

Drives the REAL FastAPI app (aigw.main.app) through httpx.ASGITransport with a
locally-injected STATE, so the entire path is exercised:

    HTTP route -> auth -> registry.resolve -> scheduler.dispatch
    -> provider.chat / chat_stream -> OpenAI translation -> SSE framing

No vendor accounts, no network, no ToS exposure: the mock provider returns
OpenAI-shaped payloads and can simulate rate-limit (fail) and dead-token (dead)
modes to prove the scheduler's circuit breaker / failover / reauth handling.

Run:  python -m pytest aigw/tests/test_gateway_e2e.py -v
"""
from __future__ import annotations

import asyncio
import json
import httpx
from contextlib import asynccontextmanager

import aigw.main as main
from aigw.registry import Registry
from aigw.scheduler import Scheduler
from aigw.providers.base import AccountState

KEY = "sk-test"


def _base_cfg() -> dict:
    # Deterministic, healthy-only setup for the happy-path tests. Failure modes
    # (fail/dead) are injected by their dedicated tests so selection is predictable.
    return {
        "server": {"api_key": KEY},
        "providers": {
            "mock": {
                "enabled": True,
                "mode": "static",
                "reply": "mock reply",
                "models": ["mock/echo", "mock/static", "mock/fail",
                           "mock/dead", "mock/embedding"],
                "accounts": [
                    {"id": "mock-ok", "mode": "static"},
                ],
            }
        },
        "routing": {"rules": []},
    }


@asynccontextmanager
async def _ctx(cfg: dict):
    dummy = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={})))
    reg = await Registry(cfg, dummy).build()
    sched = Scheduler(reg.providers, **cfg.get("scheduler", {}))
    # Inject state directly; ASGITransport does NOT run lifespan startup, so the
    # app keeps our injected STATE instead of re-reading a config file.
    main.STATE.update(cfg=cfg, http=dummy, reg=reg, sched=sched,
                      api_key=cfg["server"]["api_key"], tokenmgr=None)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app), base_url="http://test")
    try:
        yield client, reg
    finally:
        await client.aclose()
        await dummy.aclose()


# --------------------------------------------------------------------------
def test_auth_required():
    asyncio.run(_auth_required())


async def _auth_required():
    async with _ctx(_base_cfg()) as (c, _):
        r = await c.get("/v1/models")
        assert r.status_code == 401
        # wrong key also rejected
        r2 = await c.get("/v1/models", headers={"authorization": "Bearer nope"})
        assert r2.status_code == 401


def test_models_and_healthz():
    asyncio.run(_models_and_healthz())


async def _models_and_healthz():
    async with _ctx(_base_cfg()) as (c, _):
        h = {"authorization": f"Bearer {KEY}"}
        r = await c.get("/v1/models", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["object"] == "list"
        ids = [m["id"] for m in body["data"]]
        assert "mock/echo" in ids and "mock/static" in ids
        # full OpenAI-shaped model objects (created/owned_by/root/parent)
        m0 = body["data"][0]
        assert m0["object"] == "model" and m0["owned_by"] == "aigw"
        assert m0["root"] == m0["id"] and m0["parent"] is None

        r2 = await c.get("/healthz")
        assert r2.status_code == 200
        assert "mock" in r2.json()


def test_embeddings():
    asyncio.run(_embeddings())


async def _embeddings():
    async with _ctx(_base_cfg()) as (c, _):
        r = await c.post(
            "/v1/embeddings",
            headers={"authorization": f"Bearer {KEY}"},
            json={"model": "mock/embedding", "input": "hello world"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["object"] == "list"
        assert isinstance(body["data"], list) and len(body["data"]) == 1
        emb = body["data"][0]
        assert emb["object"] == "embedding" and emb["index"] == 0
        assert isinstance(emb["embedding"], list) and len(emb["embedding"]) == 8
        assert all(isinstance(x, float) for x in emb["embedding"])
        assert body["model"] == "mock/embedding"
        assert body["usage"]["prompt_tokens"] >= 1

        # list input -> one embedding per item
        r2 = await c.post(
            "/v1/embeddings",
            headers={"authorization": f"Bearer {KEY}"},
            json={"model": "mock/embedding", "input": ["a", "b"]},
        )
        assert r2.status_code == 200
        assert len(r2.json()["data"]) == 2


def test_chat_static():
    asyncio.run(_chat_static())


async def _chat_static():
    async with _ctx(_base_cfg()) as (c, _):
        r = await c.post(
            "/v1/chat/completions",
            headers={"authorization": f"Bearer {KEY}"},
            json={"model": "mock/static",
                  "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["choices"][0]["message"]["role"] == "assistant"
        assert body["choices"][0]["message"]["content"] == "mock reply"
        assert body["object"] == "chat.completion"


def test_chat_echo():
    asyncio.run(_chat_echo())


async def _chat_echo():
    cfg = _base_cfg()
    cfg["providers"]["mock"]["accounts"] = [{"id": "m-echo", "mode": "echo"}]
    async with _ctx(cfg) as (c, _):
        r = await c.post(
            "/v1/chat/completions",
            headers={"authorization": f"Bearer {KEY}"},
            json={"model": "mock/echo",
                  "messages": [{"role": "user", "content": "ping pong"}]},
        )
        assert r.status_code == 200
        assert r.json()["choices"][0]["message"]["content"] == "ping pong"


def test_chat_stream():
    asyncio.run(_chat_stream())


async def _chat_stream():
    async with _ctx(_base_cfg()) as (c, _):
        r = await c.post(
            "/v1/chat/completions",
            headers={"authorization": f"Bearer {KEY}"},
            json={"model": "mock/static", "stream": True,
                  "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200
        content = ""
        done = False
        for line in r.text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if payload == "[DONE]":
                done = True
                continue
            obj = json.loads(payload)
            assert obj["object"] == "chat.completion.chunk"
            delta = obj["choices"][0]["delta"]
            if "content" in delta:
                content += delta["content"]
        assert done, "stream must end with [DONE]"
        assert content.startswith("mock reply")


def test_unknown_model_404():
    asyncio.run(_unknown_model_404())


async def _unknown_model_404():
    async with _ctx(_base_cfg()) as (c, _):
        r = await c.post(
            "/v1/chat/completions",
            headers={"authorization": f"Bearer {KEY}"},
            json={"model": "nope/does-not-exist",
                  "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 404


def test_failover_across_accounts():
    asyncio.run(_failover_across_accounts())


async def _failover_across_accounts():
    cfg = _base_cfg()
    cfg["providers"]["mock"]["accounts"] = [
        {"id": "mock-fail", "mode": "fail"},
        {"id": "mock-ok", "mode": "static"},
    ]
    async with _ctx(cfg) as (c, reg):
        # Force the failing account to be picked first: scheduler uses least_fail,
        # so raise the healthy account's fail_count so the failer (0) wins the min.
        ok_acc = reg.providers["mock"].accounts[1]
        ok_acc.fail_count = 10

        r = await c.post(
            "/v1/chat/completions",
            headers={"authorization": f"Bearer {KEY}"},
            json={"model": "mock/fail",
                  "messages": [{"role": "user", "content": "hi"}]},
        )
        # The 429 from mock-fail must be retried on mock-ok -> success.
        assert r.status_code == 200
        assert r.json()["choices"][0]["message"]["content"] == "mock reply"

        fail_acc = reg.providers["mock"].accounts[0]
        assert fail_acc.state == AccountState.COOLDOWN, \
            "rate-limited account should be tripped into cooldown"


def test_dead_token_reauth_then_503():
    asyncio.run(_dead_token_reauth_then_503())


async def _dead_token_reauth_then_503():
    cfg = _base_cfg()
    cfg["providers"]["mock"] = {
        "enabled": True,
        "mode": "static",
        "models": ["mock/dead"],
        "accounts": [{"id": "mock-dead", "mode": "dead"}],
    }
    async with _ctx(cfg) as (c, reg):
        r = await c.post(
            "/v1/chat/completions",
            headers={"authorization": f"Bearer {KEY}"},
            json={"model": "mock/dead",
                  "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 503  # no schedulable account left
        dead = reg.providers["mock"].accounts[0]
        assert dead.state == AccountState.REAUTH, \
            "dead token should flip the account to REAUTH (no blind retry)"
