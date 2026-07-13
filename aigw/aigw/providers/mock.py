"""Mock provider — local, ToS-safe, for dev/demo and end-to-end testing.

The real adapters (cursor / antigravity / workbuddy) need captured wire protocols
and your own logged-in sessions before they return 200s. The mock provider returns
OpenAI-shaped responses with NO network and NO vendor account, so you can:

  - boot the gateway today and point your own client at it for UI/dev work
  - exercise the FULL pipeline (registry -> scheduler -> provider -> OAI translation
    -> SSE) in tests, proving the gateway works without touching any ToS
  - reproduce failure modes (rate limit, dead token) to verify the scheduler's
    circuit breaker / failover / reauth handling

Per-account behaviour is set via the account's `mode` (falls back to provider-level
`mode`). Modes:

  echo   -> replies with the last user message's text (handy for prompt round-trips)
  static -> replies with a fixed string (config `reply`, default "mock reply")
  fail   -> raises a retryable 429 (drives scheduler cooldown + cross-account failover)
  dead   -> raises a 401 reauth (drives scheduler -> AccountState.REAUTH, no retry)

Config:
  mock:
    enabled: true
    models: ["mock/echo", "mock/static", "mock/fail", "mock/dead"]
    mode: echo                 # default mode for accounts without their own
    latency_ms: 0              # artificial upstream latency
    reply: "mock reply"        # canned text for `static` mode
    accounts:
      - { id: mock-ok,   mode: static }
      - { id: mock-echo, mode: echo }
      - { id: mock-fail, mode: fail }
"""
from __future__ import annotations

import time
import uuid
import asyncio
from typing import AsyncIterator

from .base import Provider, Account, Credential, UpstreamError


def _last_user_text(oai_req: dict) -> str:
    for m in reversed(oai_req.get("messages", [])):
        if m.get("role") != "user":
            continue
        c = m.get("content", "")
        if isinstance(c, str):
            return c
        # content blocks: [{"type":"text","text":...}, ...]
        return "".join(p.get("text", "") for p in c if isinstance(p, dict))
    return ""


def _completion(model: str, text: str, finish: str = "stop") -> dict:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "finish_reason": finish,
            "message": {"role": "assistant", "content": text},
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _chunk(cid: str, model: str, text=None, finish=None) -> dict:
    delta = {} if text is None else {"content": text}
    return {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


class MockProvider(Provider):
    name = "mock"

    def __init__(self, config, http):
        super().__init__(config, http)
        self.default_mode = config.get("mode", "echo")
        self.latency = float(config.get("latency_ms", 0))
        self.static_reply = config.get("reply", "mock reply")
        self.embedding_dim = int(config.get("embedding_dim", 8))
        self.served_models = tuple(config.get("models", ["mock/default"]))

    async def discover_accounts(self) -> list[Account]:
        accs: list[Account] = []
        for i, a in enumerate(self.config.get("accounts", [])):
            mode = a.get("mode", self.default_mode)
            acc = Account(
                id=a.get("id", f"mock-{i}"),
                provider=self.name,
                label=a.get("label", ""),
                cred=Credential(access_token=a.get("token", "mock-token")),
            )
            acc._mock_mode = mode  # per-account behaviour hook
            accs.append(acc)
        self.accounts = accs
        return accs

    async def ensure_fresh(self, acc: Account) -> None:
        # Mock tokens never expire. Real adapters override this with a refresh flow.
        if not acc.cred.access_token:
            raise UpstreamError(401, "mock: no token", reauth=True)

    # --- shared reply builder --------------------------------------------
    def _reply_text(self, acc: Account, oai_req: dict) -> str:
        mode = getattr(acc, "_mock_mode", self.default_mode)
        if mode == "echo":
            return _last_user_text(oai_req) or "(no user message)"
        if mode == "static":
            return self.static_reply
        # fail/dead are raised in chat/chat_stream, not here
        return self.static_reply

    def _maybe_raise(self, acc: Account):
        mode = getattr(acc, "_mock_mode", self.default_mode)
        if mode == "fail":
            raise UpstreamError(429, "mock rate limited",
                                retryable=True, cooldown=0.05)
        if mode == "dead":
            raise UpstreamError(401, "mock dead token", reauth=True)

    # --- request path ----------------------------------------------------
    async def chat(self, acc: Account, oai_req: dict) -> dict:
        self._maybe_raise(acc)
        if self.latency:
            await asyncio.sleep(self.latency / 1000.0)
        return _completion(oai_req["model"], self._reply_text(acc, oai_req))

    async def chat_stream(self, acc: Account, oai_req: dict) -> AsyncIterator[dict]:
        self._maybe_raise(acc)
        if self.latency:
            await asyncio.sleep(self.latency / 1000.0)
        text = self._reply_text(acc, oai_req)
        cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        for word in text.split(" "):
            yield _chunk(cid, oai_req["model"], word + " ")
        yield _chunk(cid, oai_req["model"], None, finish="stop")

    # --- embeddings (OpenAI /v1/embeddings) ------------------------------
    @staticmethod
    def _embed(text: str, dim: int) -> list[float]:
        import hashlib
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [round((digest[i % len(digest)] / 127.5) - 1.0, 6) for i in range(dim)]

    async def embeddings(self, acc: Account, oai_req: dict) -> dict:
        self._maybe_raise(acc)
        inp = oai_req.get("input", "")
        texts = inp if isinstance(inp, list) else [inp]
        data = [{
            "object": "embedding",
            "index": i,
            "embedding": self._embed(t, self.embedding_dim),
        } for i, t in enumerate(texts)]
        prompt_tokens = sum(len(t.split()) for t in texts) or len(texts)
        return {
            "object": "list",
            "data": data,
            "model": oai_req.get("model", "mock/embedding"),
            "usage": {"prompt_tokens": prompt_tokens, "total_tokens": prompt_tokens},
        }
