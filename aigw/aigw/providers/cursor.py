"""Cursor provider adapter.

HONEST STATUS: token acquisition + refresh below is VERIFIED and works. The chat
wire protocol is the hard part and is NOT plug-and-play:

  Cursor does NOT expose a REST chat API. Even when you configure a custom model,
  traffic is repackaged into Cursor's own Connect/gRPC protobuf protocol and routed
  through api2.cursor.sh (service `aiserver.v1.ChatService`, methods like
  StreamUnifiedChat). Requests carry anti-abuse headers: `x-cursor-checksum`,
  `x-cursor-client-version`, a per-machine id, and a Connect content-type
  (`application/connect+proto` or `application/proto`). See burpheart/cursor-tap
  reverse-engineering notes.

Two viable strategies:

  A) Connect-protobuf client (this file, skeleton). You must first capture the
     .proto (StreamUnifiedChatRequest/Response) via tools/mitm_discover.py, generate
     Python stubs, and replicate the checksum. Highest effort, no extra process.

  B) MITM piggyback (recommended to bootstrap). Run mitmproxy in front of a real
     Cursor instance; your gateway forwards OpenAI requests to a local injector that
     replays them through Cursor's own client. Lower fidelity to "headless" but far
     faster to get working. Documented in tools/mitm_discover.py.

Until the protobuf is captured, chat()/chat_stream() raise a clear NotImplemented
UpstreamError instead of silently returning garbage.
"""
from __future__ import annotations

from typing import AsyncIterator

from .base import Provider, Account, Credential, UpstreamError
from ..tokens import desktop_stores as ds
from .cursor_proto import stubs_available


class CursorProvider(Provider):
    name = "cursor"
    served_models = ("cursor/gpt-4o", "cursor/claude-4-sonnet", "cursor/auto")

    # VERIFY: real chat host + path
    DEFAULT_HOST = "https://api2.cursor.sh"
    DEFAULT_PATH = "/aiserver.v1.ChatService/StreamUnifiedChat"

    def __init__(self, config, http):
        super().__init__(config, http)
        self.host = config.get("host", self.DEFAULT_HOST).rstrip("/")
        self.path = config.get("chat_path", self.DEFAULT_PATH)
        # fallback: auto (proto if stubs else connect_json if template) |
        #          proto | connect_json
        self.fallback = config.get("fallback", "auto")
        self.model_map = config.get("model_map", {})

    async def discover_accounts(self) -> list[Account]:
        accs: list[Account] = []
        if self.config.get("use_local_session", True):
            local = ds.read_cursor()
            if local and local.get("refresh_token"):
                accs.append(Account(
                    id="cursor-local",
                    provider=self.name,
                    label=local.get("email", "local"),
                    cred=Credential(access_token=local["access_token"],
                                    refresh_token=local["refresh_token"],
                                    extra={"tier": local.get("tier", "")}),
                ))
        for i, a in enumerate(self.config.get("accounts", [])):
            accs.append(Account(
                id=a.get("id", f"cursor-cfg-{i}"), provider=self.name,
                label=a.get("label", ""),
                cred=Credential(refresh_token=a["refresh_token"]),
            ))
        self.accounts = accs
        return accs

    async def ensure_fresh(self, acc: Account) -> None:
        if acc.cred.access_token and not acc.cred.is_expired():
            return
        async with acc.lock:
            if acc.cred.access_token and not acc.cred.is_expired():
                return
            try:
                res = await ds.refresh_cursor(self.http, acc.cred.refresh_token)
            except PermissionError as e:
                raise UpstreamError(401, str(e), reauth=True)
            except Exception as e:
                raise UpstreamError(401, f"cursor refresh failed: {e}", reauth=True)
            acc.cred.access_token = res["access_token"]
            acc.cred.expires_at = res["expires_at"]

    # --- chat -------------------------------------------------------------
    # Strategy: if `use_proto` is set and the generated cursor_pb2 stubs exist,
    # delegate to the binary Connect client. Otherwise fall back (auto/proto/
    # connect_json) to the JSON Connect transport, which needs a body template
    # because Cursor's message schema is still undocumented.
    async def chat(self, acc: Account, oai_req: dict) -> dict:
        if (self.config.get("use_proto") or self.fallback == "proto") and stubs_available():
            from .cursor_proto import CursorProtoClient
            return await CursorProtoClient(self, acc, self.http, self.config).chat(oai_req)
        if self.fallback in ("auto", "connect_json"):
            return await self._connect_json_chat(acc, oai_req)
        raise UpstreamError(
            501,
            "cursor chat not wired: capture the aiserver.v1.ChatService protobuf "
            "with tools/mitm_discover.py, generate stubs, set cursor.use_proto: true.",
            retryable=False,
        )

    async def chat_stream(self, acc: Account, oai_req: dict) -> AsyncIterator[dict]:
        if (self.config.get("use_proto") or self.fallback == "proto") and stubs_available():
            from .cursor_proto import CursorProtoClient
            async for chunk in CursorProtoClient(self, acc, self.http,
                                                 self.config).chat_stream(oai_req):
                yield chunk
            return
        if self.fallback in ("auto", "connect_json"):
            async for chunk in self._connect_json_stream(acc, oai_req):
                yield chunk
            return
        raise UpstreamError(501, "cursor stream not wired (see chat()).", retryable=False)
        yield  # make this an async generator

    # --- Connect-JSON fallback -------------------------------------------
    # Connect speaks BOTH protobuf and JSON over the same URL. This path uses
    # `content-type: application/json` + a user-supplied body template. Cursor's
    # message schema is undocumented, so you MUST provide `connect_json_body_template`
    # (a JSON object whose string values may use {model}/{prompt}/{system}/{messages}).
    def _render_connect_json(self, tpl: dict, oai_req: dict) -> dict:
        prompt, system = [], []
        for m in oai_req["messages"]:
            t = m["content"] if isinstance(m["content"], str) else "".join(
                p.get("text", "") for p in m["content"] if isinstance(p, dict))
            (system if m["role"] == "system" else prompt).append(t)
        import json as _json
        ctx = {
            "model": self.model_map.get(oai_req["model"], oai_req["model"].split("/")[-1]),
            "prompt": "\n".join(prompt),
            "system": "\n".join(system),
            "messages": oai_req["messages"],  # structured -> injected as raw JSON
        }
        # JSON-aware substitution (str.format would choke on the template's own
        # braces). Two passes:
        #   1) a field whose value is exactly "{key}" becomes the JSON-encoded
        #      value (so a list/object is injected as a real JSON value);
        #   2) "{key}" appearing inside a larger string becomes an ESCAPED string
        #      fragment, so embedded quotes can't break the JSON.
        s = _json.dumps(tpl)
        for key, val in ctx.items():
            s = s.replace('"{' + key + '}"', _json.dumps(val))
        for key, val in ctx.items():
            if isinstance(val, str):
                s = s.replace("{" + key + "}", _json.dumps(val)[1:-1])
        return _json.loads(s)

    def _connect_json_headers(self, acc: Account) -> dict:
        h = {
            "authorization": f"Bearer {acc.cred.access_token}",
            "content-type": "application/json",
            "connect-protocol-version": "1",
        }
        ck = self.config.get("checksum")
        if ck:
            h["x-cursor-checksum"] = ck
        h.update(self.config.get("extra_headers", {}))
        return h

    async def _connect_json_chat(self, acc: Account, oai_req: dict) -> dict:
        tpl = self.config.get("connect_json_body_template")
        if not tpl:
            raise UpstreamError(
                501, "cursor connect_json fallback selected but "
                "cursor.connect_json_body_template is missing. Capture the proto "
                "(cursor.use_proto: true) OR supply a JSON body template mapping "
                "the OpenAI request to Cursor's Connect JSON message.", retryable=False)
        body = self._render_connect_json(tpl, oai_req)
        r = await self.http.post(self.host + self.path,
                                 headers=self._connect_json_headers(acc), json=body)
        err = self._classify_http(r.status_code)
        if err:
            raise err
        r.raise_for_status()
        return self._connect_json_to_oai(r.json(), oai_req["model"])

    async def _connect_json_stream(self, acc: Account, oai_req: dict) -> AsyncIterator[dict]:
        import uuid as _uuid
        tpl = self.config.get("connect_json_body_template")
        if not tpl:
            raise UpstreamError(501, "cursor connect_json fallback selected but "
                                "cursor.connect_json_body_template is missing.",
                                retryable=False)
        body = self._render_connect_json(tpl, oai_req)
        cid = f"chatcmpl-{_uuid.uuid4().hex[:24]}"
        async with self.http.stream("POST", self.host + self.path,
                                    headers=self._connect_json_headers(acc),
                                    json=body) as r:
            err = self._classify_http(r.status_code)
            if err:
                raise err
            async for line in r.aiter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload in ("", "[DONE]"):
                    continue
                try:
                    obj = __import__("json").loads(payload)
                except __import__("json").JSONDecodeError:
                    continue
                yield self._connect_json_chunk(obj, cid, oai_req["model"])
            yield {"id": cid, "object": "chat.completion.chunk",
                   "created": __import__("time").time(), "model": oai_req["model"],
                   "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}

    # --- Connect-JSON response mapping (best-effort) ----------------------
    @staticmethod
    def _connect_json_to_oai(obj: dict, model: str) -> dict:
        import time as _t, uuid as _u
        text = ""
        if "choices" in obj and obj["choices"]:
            text = obj["choices"][0].get("message", {}).get("content", "")
        elif "content" in obj:
            text = obj["content"]
        elif "text" in obj:
            text = obj["text"]
        elif "parts" in obj:
            text = "".join(p.get("text", "") for p in obj["parts"])
        return {
            "id": f"chatcmpl-{_u.uuid4().hex[:24]}",
            "object": "chat.completion",
            "created": int(_t.time()), "model": model,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": text}}],
            "usage": obj.get("usage", {}),
        }

    @staticmethod
    def _connect_json_chunk(obj: dict, cid: str, model: str) -> dict:
        import time as _t
        delta = {}
        if "choices" in obj and obj["choices"] and "delta" in obj["choices"][0]:
            return obj  # already OpenAI-shaped
        if "content" in obj or "text" in obj:
            delta = {"content": obj.get("content", obj.get("text", ""))}
        elif "parts" in obj:
            delta = {"content": "".join(p.get("text", "") for p in obj["parts"])}
        return {"id": cid, "object": "chat.completion.chunk",
                "created": int(_t.time()), "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
