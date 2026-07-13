"""Workbuddy provider adapter (generic, config-driven).

Workbuddy's internal API is not publicly documented. Rather than hardcode guesses,
this adapter is a configurable passthrough: you capture the real endpoint + auth with
tools/mitm_discover.py, drop the details into config.yaml, and pick a `dialect`:

  dialect: "openai"     -> upstream already speaks /v1/chat/completions (passthrough)
  dialect: "anthropic"  -> upstream speaks /v1/messages (we translate both ways)

Auth + headers are fully templated so you can reproduce any MITM-captured required
header without touching code. Templates use Python str.format with this context:

  {token}          -> account.access_token
  {account_id}     -> account.id
  {model}          -> the unified model id the caller asked for
  {model_upstream} -> the model id after model_map translation
  {provider}       -> "workbuddy"

Example config:
  workbuddy:
    enabled: true
    base_url: https://wb.internal
    dialect: anthropic
    chat_path_template: "/v1/messages"          # may contain {model_upstream}
    auth: { type: bearer }
    header_templates:
      - { name: "x-app-version", value: "9.2.1" }
      - { name: "x-device-id",  value: "{account_id}" }
    models: ["workbuddy/default"]
    model_map: { "workbuddy/default": "claude-sonnet-4" }
    accounts:
      - { id: wb-1, token: ${WB_TOKEN} }
"""
from __future__ import annotations

import json
import time
import uuid
from typing import AsyncIterator

from .base import Provider, Account, Credential, UpstreamError


def _flatten_oai(parts) -> str:
    if isinstance(parts, str):
        return parts
    out = []
    for p in parts:
        if isinstance(p, dict):
            out.append(p.get("text", ""))
    return "".join(out)


class WorkbuddyProvider(Provider):
    name = "workbuddy"

    def __init__(self, config, http):
        super().__init__(config, http)
        self.base_url = config["base_url"].rstrip("/")
        self.dialect = config.get("dialect", "openai")
        self.chat_path = config.get("chat_path", "/v1/chat/completions")
        self.chat_path_tpl = config.get("chat_path_template")
        self.served_models = tuple(config.get("models", ["workbuddy/default"]))
        self._model_map = config.get("model_map", {})

    async def discover_accounts(self) -> list[Account]:
        accs = []
        for i, a in enumerate(self.config.get("accounts", [])):
            accs.append(Account(
                id=a.get("id", f"workbuddy-{i}"), provider=self.name,
                label=a.get("label", ""),
                cred=Credential(access_token=a.get("token", ""),
                                refresh_token=a.get("refresh_token", "")),
            ))
        self.accounts = accs
        return accs

    async def ensure_fresh(self, acc: Account) -> None:
        # Static tokens by default. If Workbuddy uses OAuth, add a refresh flow to
        # desktop_stores.py and call it here (same pattern as cursor/antigravity).
        if not acc.cred.access_token:
            raise UpstreamError(401, "no workbuddy token configured", reauth=True)

    # --- templating ------------------------------------------------------
    def _ctx(self, acc: Account, oai_req: dict) -> dict:
        return {
            "token": acc.cred.access_token,
            "account_id": acc.id,
            "model": oai_req.get("model", ""),
            "model_upstream": self._map_model(oai_req.get("model", "")),
            "provider": self.name,
        }

    def _render_headers(self, acc: Account, oai_req: dict) -> dict:
        ctx = self._ctx(acc, oai_req)
        auth = self.config.get("auth", {"type": "bearer"})
        h = {"content-type": "application/json"}
        if auth.get("type") == "bearer":
            h["authorization"] = f"Bearer {acc.cred.access_token}"
        elif auth.get("type") == "header":
            h[auth.get("header", "x-api-key")] = acc.cred.access_token
        # templated headers (may reference {token},{account_id},{model},...)
        for tpl in self.config.get("header_templates", []):
            name = tpl["name"]
            val = tpl.get("value", "")
            try:
                val = val.format(**ctx)
            except Exception:
                pass  # leave as literal if a placeholder is unfilled
            h[name] = val
        # static extra headers (lowest priority; do not clobber templated ones)
        for k, v in self.config.get("extra_headers", {}).items():
            h.setdefault(k, v)
        return h

    def _render_path(self, oai_req: dict) -> str:
        if self.chat_path_tpl:
            ctx = self._ctx(Account(id="", provider=self.name), oai_req)
            ctx["model_upstream"] = self._map_model(oai_req.get("model", ""))
            try:
                return self.chat_path_tpl.format(**ctx)
            except Exception:
                return self.chat_path_tpl
        return self.chat_path

    def _map_model(self, m: str) -> str:
        return self._model_map.get(m, m.split("/")[-1])

    # --- openai dialect --------------------------------------------------
    async def chat(self, acc: Account, oai_req: dict) -> dict:
        if self.dialect == "openai":
            req = dict(oai_req, model=self._map_model(oai_req["model"]))
            r = await self.http.post(self.base_url + self._render_path(oai_req),
                                     headers=self._render_headers(acc, oai_req), json=req)
            err = self._classify_http(r.status_code)
            if err:
                raise err
            r.raise_for_status()
            data = r.json()
            data["model"] = oai_req["model"]
            return data
        if self.dialect == "anthropic":
            return await self._anthropic_chat(acc, oai_req)
        raise UpstreamError(501, f"dialect {self.dialect} non-stream not implemented")

    async def chat_stream(self, acc: Account, oai_req: dict) -> AsyncIterator[dict]:
        if self.dialect == "openai":
            req = dict(oai_req, model=self._map_model(oai_req["model"]), stream=True)
            async with self.http.stream("POST", self.base_url + self._render_path(oai_req),
                                        headers=self._render_headers(acc, oai_req),
                                        json=req) as r:
                err = self._classify_http(r.status_code)
                if err:
                    raise err
                async for line in r.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        obj = json.loads(payload)
                        obj["model"] = oai_req["model"]
                        yield obj
                    except json.JSONDecodeError:
                        continue
            return
        if self.dialect == "anthropic":
            async for c in self._anthropic_stream(acc, oai_req):
                yield c
            return
        raise UpstreamError(501, f"dialect {self.dialect} stream not implemented")
        yield

    # --- anthropic dialect (translate) -----------------------------------
    def _to_anthropic(self, oai_req: dict) -> dict:
        sys_parts, msgs = [], []
        for m in oai_req["messages"]:
            role = m["role"]
            content = m["content"] if isinstance(m["content"], str) else _flatten_oai(m["content"])
            if role == "system":
                sys_parts.append(content)
                continue
            msgs.append({"role": role, "content": content})
        body = {"model": self._map_model(oai_req["model"]), "messages": msgs}
        if sys_parts:
            body["system"] = "\n".join(sys_parts)
        body["max_tokens"] = int(oai_req.get("max_tokens", 1024))
        if "temperature" in oai_req:
            body["temperature"] = oai_req["temperature"]
        if oai_req.get("stream"):
            body["stream"] = True
        return body

    def _from_anthropic(self, obj: dict, model: str) -> dict:
        text = "".join(c.get("text", "") for c in obj.get("content", [])
                       if isinstance(c, dict) and c.get("type") == "text")
        return {
            "id": obj.get("id", f"chatcmpl-{uuid.uuid4().hex[:24]}"),
            "object": "chat.completion",
            "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "finish_reason": obj.get("stop_reason", "stop"),
                         "message": {"role": "assistant", "content": text}}],
            "usage": obj.get("usage", {}),
        }

    async def _anthropic_chat(self, acc: Account, oai_req: dict) -> dict:
        body = self._to_anthropic(oai_req)
        r = await self.http.post(self.base_url + self._render_path(oai_req),
                                 headers=self._render_headers(acc, oai_req), json=body)
        err = self._classify_http(r.status_code)
        if err:
            raise err
        r.raise_for_status()
        return self._from_anthropic(r.json(), oai_req["model"])

    async def _anthropic_stream(self, acc: Account, oai_req: dict) -> AsyncIterator[dict]:
        body = self._to_anthropic(dict(oai_req, stream=True))
        cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        async with self.http.stream("POST", self.base_url + self._render_path(oai_req),
                                    headers=self._render_headers(acc, oai_req), json=body) as r:
            err = self._classify_http(r.status_code)
            if err:
                raise err
            buf = ""
            async for line in r.aiter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "content_block_delta" and \
                   obj.get("delta", {}).get("type") == "text_delta":
                    yield _chunk(cid, oai_req["model"], obj["delta"]["text"])
                elif obj.get("type") == "message_stop":
                    yield _chunk(cid, oai_req["model"], None, finish="stop")
            yield _chunk(cid, oai_req["model"], None, finish="stop")


def _chunk(cid: str, model: str, text, finish=None) -> dict:
    delta = {} if text is None else {"content": text}
    return {
        "id": cid, "object": "chat.completion.chunk",
        "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
