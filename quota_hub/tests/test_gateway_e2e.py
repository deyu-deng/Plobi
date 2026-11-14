"""Full-stack end-to-end test of the OpenAI-compatible surface.

Drives the REAL FastAPI app (quota_hub.main.app) through httpx.ASGITransport with a
locally-injected STATE, so the entire path is exercised:

    HTTP route -> auth -> registry.resolve -> scheduler.dispatch
    -> provider.chat / chat_stream -> OpenAI translation -> SSE framing

No vendor accounts, no network, no ToS exposure: the mock provider returns
OpenAI-shaped payloads and can simulate rate-limit (fail) and dead-token (dead)
modes to prove the scheduler's circuit breaker / failover / reauth handling.

Run:  python -m pytest quota_hub/tests/test_gateway_e2e.py -v
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import httpx

import quota_hub.main as main
from quota_hub.providers.base import AccountState, UpstreamError
from quota_hub.registry import Registry
from quota_hub.scheduler import Scheduler

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
                "models": ["mock/echo", "mock/static", "mock/fail", "mock/dead", "mock/embedding"],
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
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={}))
    )
    reg = await Registry(cfg, dummy).build()
    sched = Scheduler(reg.providers, **cfg.get("scheduler", {}))
    # Inject state directly; ASGITransport does NOT run lifespan startup, so the
    # app keeps our injected STATE instead of re-reading a config file.
    main.STATE.update(
        cfg=cfg, http=dummy, reg=reg, sched=sched, api_key=cfg["server"]["api_key"], tokenmgr=None
    )
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test")
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
        assert m0["object"] == "model" and m0["owned_by"] == "quota_hub"
        assert m0["root"] == m0["id"] and m0["parent"] is None
        # Plobi extension: capability flags for model picker filtering
        assert m0.get("provider") == "mock"
        caps = m0.get("capabilities") or {}
        assert caps.get("stream") is True
        assert caps.get("embeddings") is True
        assert caps.get("tools") is False
        assert caps.get("compliance") == "compliant"

        r2 = await c.get("/healthz")
        assert r2.status_code == 200
        hz = r2.json()
        assert "mock" in hz
        assert hz.get("version")
        assert (
            hz.get("providers", {}).get("mock", {}).get("capabilities", {}).get("embeddings")
            is True
        )


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
            json={"model": "mock/static", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["choices"][0]["message"]["role"] == "assistant"
        assert body["choices"][0]["message"]["content"] == "mock reply"
        assert body["object"] == "chat.completion"


def test_tools_rejected_when_unsupported():
    asyncio.run(_tools_rejected())


async def _tools_rejected():
    """mock.capabilities.tools=false -> 400 when client sends tools."""
    async with _ctx(_base_cfg()) as (c, _):
        r = await c.post(
            "/v1/chat/completions",
            headers={"authorization": f"Bearer {KEY}"},
            json={
                "model": "mock/static",
                "messages": [{"role": "user", "content": "hi"}],
                "tools": [
                    {
                        "type": "function",
                        "function": {"name": "noop", "parameters": {"type": "object"}},
                    }
                ],
            },
        )
        assert r.status_code == 400
        assert "tools" in r.text.lower()


def test_chat_echo():
    asyncio.run(_chat_echo())


async def _chat_echo():
    cfg = _base_cfg()
    cfg["providers"]["mock"]["accounts"] = [{"id": "m-echo", "mode": "echo"}]
    async with _ctx(cfg) as (c, _):
        r = await c.post(
            "/v1/chat/completions",
            headers={"authorization": f"Bearer {KEY}"},
            json={"model": "mock/echo", "messages": [{"role": "user", "content": "ping pong"}]},
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
            json={
                "model": "mock/static",
                "stream": True,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert r.status_code == 200
        content = ""
        done = False
        for line in r.text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[len("data:") :].strip()
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
            json={"model": "nope/does-not-exist", "messages": [{"role": "user", "content": "hi"}]},
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
            json={"model": "mock/fail", "messages": [{"role": "user", "content": "hi"}]},
        )
        # The 429 from mock-fail must be retried on mock-ok -> success.
        assert r.status_code == 200
        assert r.json()["choices"][0]["message"]["content"] == "mock reply"

        fail_acc = reg.providers["mock"].accounts[0]
        assert fail_acc.state == AccountState.COOLDOWN, (
            "rate-limited account should be tripped into cooldown"
        )


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
            json={"model": "mock/dead", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 503  # no schedulable account left
        dead = reg.providers["mock"].accounts[0]
        assert dead.state == AccountState.REAUTH, (
            "dead token should flip the account to REAUTH (no blind retry)"
        )


# --------------------------------------------------------------------------
# 裁定 152 那一族：200 而 content 为空 = 没货，不许算成功
# --------------------------------------------------------------------------
async def _empty_completion_is_no_goods():
    cfg = _base_cfg()
    cfg["providers"]["mock"]["reply"] = ""          # 上游"成功"地回了个空
    async with _ctx(cfg) as (c, _):
        r = await c.post(
            "/v1/chat/completions",
            headers={"authorization": f"Bearer {KEY}"},
            json={"model": "mock/static", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert r.status_code == 502, (r.status_code, r.text[:200])
    assert "没货" in r.text or "empty completion" in r.text, r.text[:200]


def test_empty_completion_is_no_goods():
    asyncio.run(_empty_completion_is_no_goods())


async def _a_real_reply_is_still_success():
    """反向那一半：判据不许把有货的也打死（否则这条腿从"假成功"翻成"全红"，更没法用）。"""
    async with _ctx(_base_cfg()) as (c, _):
        r = await c.post(
            "/v1/chat/completions",
            headers={"authorization": f"Bearer {KEY}"},
            json={"model": "mock/static", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert r.status_code == 200, r.text[:200]
    assert r.json()["choices"][0]["message"]["content"] == "mock reply"


def test_a_real_reply_is_still_success():
    asyncio.run(_a_real_reply_is_still_success())


# --------------------------------------------------------------------------
# 2026-11-14 现场那一族（安装版 D:\Software\Plobi 上复现的两次读数）
# --------------------------------------------------------------------------
# 1) `workbuddy/hy3` + `max_tokens=8`：quota_hub 回 502「没货」，而同一请求直连
#    magpie 是 200 —— content=null、reasoning_content 有字、finish_reason='length'。
#    一个合理请求因为思考吃满预算被判成上游空话；客户端再当故障重试，同一个请求
#    于是把上游预算烧第二遍。
# 2) 19:51:01/04/10 那三次 502：本地侧车正在重启插件宿主，客户端读成"坏网关"，
#    3 次重试在 9 秒内砸完，而空窗是 3 分 46 秒。说 503 + Retry-After 才是实情。


def _thinking_only() -> dict:
    """A reasoning model that spent the whole budget thinking — the measured shape."""
    return {
        "id": "chatcmpl-think",
        "object": "chat.completion",
        "model": "mock/static",
        "choices": [
            {
                "index": 0,
                "finish_reason": "length",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": "先看题目要什么，再看边界……",
                },
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 8, "total_tokens": 13},
    }


async def _post_turn(c):
    return await c.post(
        "/v1/chat/completions",
        headers={"authorization": f"Bearer {KEY}"},
        json={
            "model": "mock/static",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 8,
        },
    )


async def _reasoning_only_is_a_real_reply():
    async with _ctx(_base_cfg()) as (c, reg):
        async def chat(acc, oai_req):
            return _thinking_only()

        reg.providers["mock"].chat = chat
        r = await _post_turn(c)
    assert r.status_code == 200, r.text[:300]
    message = r.json()["choices"][0]["message"]
    assert message["content"] is None, "那一轮的正文本来就该空着"
    assert message["reasoning_content"], "思考必须原样到达客户端，不能被判成没货"


def test_reasoning_only_is_a_real_reply():
    asyncio.run(_reasoning_only_is_a_real_reply())


async def _a_budget_truncated_turn_is_a_real_reply():
    """content 空且没有 reasoning，但 finish_reason='length'：上游确实跑过一轮。"""
    async with _ctx(_base_cfg()) as (c, reg):
        async def chat(acc, oai_req):
            out = _thinking_only()
            out["choices"][0]["message"] = {"role": "assistant", "content": ""}
            return out

        reg.providers["mock"].chat = chat
        r = await _post_turn(c)
    assert r.status_code == 200, r.text[:300]


def test_a_budget_truncated_turn_is_a_real_reply():
    asyncio.run(_a_budget_truncated_turn_is_a_real_reply())


async def _a_silent_stop_is_still_no_goods():
    """反向那一半：判据放宽之后，"200 而一个字都没有"还得照样判红。"""
    async with _ctx(_base_cfg()) as (c, reg):
        async def chat(acc, oai_req):
            return {
                "id": "chatcmpl-silent",
                "object": "chat.completion",
                "model": "mock/static",
                "choices": [
                    {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": ""}}
                ],
            }

        reg.providers["mock"].chat = chat
        r = await _post_turn(c)
    assert r.status_code == 502, (r.status_code, r.text[:300])
    assert "没货" in r.text or "empty completion" in r.text, r.text[:300]


def test_a_silent_stop_is_still_no_goods():
    asyncio.run(_a_silent_stop_is_still_no_goods())


async def _a_cooling_leg_says_when_to_come_back():
    """就绪中的腿要报 503 + Retry-After，而不是一个让客户端瞎重试的 502。"""
    async with _ctx(_base_cfg()) as (c, reg):
        async def chat(acc, oai_req):
            raise UpstreamError(
                503,
                "mock: the local gateway is not ready (it answered 502)",
                retryable=True,
                cooldown=2.0,
            )

        reg.providers["mock"].chat = chat
        r = await _post_turn(c)

    assert r.status_code == 503, (r.status_code, r.text[:300])
    wait = r.headers.get("retry-after")
    assert wait is not None, "没有 Retry-After，客户端只能再猜一次 9 秒"
    assert 1 <= int(wait) <= 3, wait


def test_a_cooling_leg_says_when_to_come_back():
    asyncio.run(_a_cooling_leg_says_when_to_come_back())

