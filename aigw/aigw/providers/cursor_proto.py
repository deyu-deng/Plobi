r"""Cursor Connect/protobuf client.

Why this file exists
--------------------
Cursor's chat endpoint is the `aiserver.v1.ChatService` Connect service, not REST.
We cannot ship a working implementation without the real protobuf schema, which is
undocumented. This module is the *integration point*:

  - It tries to import the generated `cursor_pb2` stubs.
  - If they are missing, every method raises a clear UpstreamError pointing at the
    exact `protoc` commands to generate them.
  - If they exist, `CursorProtoClient` shows the *concrete* request-build /
    response-parse logic against the protobuf. The field names are marked `VERIFY`
    because they depend on the schema you recover with `protoc --decode_raw`.

Generate the stubs (after capturing a real request — see tools/mitm_discover.py):
    protoc --python_out=aigw/proto --proto_path=aigw/proto aigw/proto/cursor.proto

Then set `cursor.use_proto: true` in config.yaml and adjust the VERIFY fields.

Connect framing (important)
---------------------------
Cursor uses the **binary Connect protocol** (`content-type: application/connect+proto`).
Each streamed message on the wire is framed as:  1 flag byte + 4-byte big-endian
length + payload. `_deframe_connect` below handles that. (Connect also supports
JSON via `content-type: application/json` — see `cursor.py`'s connect_json fallback.)
"""
from __future__ import annotations

import struct
import time
import uuid
from typing import AsyncIterator, Optional

from .base import Account, UpstreamError

try:
    from ..proto import cursor_pb2 as pb  # type: ignore
    _PB_OK = True
except Exception:  # ImportError or protobuf not installed
    pb = None
    _PB_OK = False

# VERIFY: the real host + StreamUnifiedChat path (capture it).
CURSOR_CHAT_HOST = "https://api2.cursor.sh"
CURSOR_CHAT_PATH = "/aiserver.v1.ChatService/StreamUnifiedChat"


def _deframe_connect(data: bytes):
    """Yield one payload per Connect-framed message.

    Wire format: [flag:1][length:4 BE][payload:length]  (repeated).
    Connect uses flag 0x00 for unary and streams each message the same way.
    """
    i = 0
    n = len(data)
    while i + 5 <= n:
        _flag = data[i]
        length = struct.unpack(">I", data[i + 1:i + 5])[0]
        payload = data[i + 5:i + 5 + length]
        yield _flag, payload
        i += 5 + length


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") for p in content if isinstance(p, dict))


class CursorProtoClient:
    def __init__(self, provider, account: Account, http, config: dict):
        self.provider = provider
        self.account = account
        self.http = http
        self.config = config
        self.host = config.get("host", CURSOR_CHAT_HOST).rstrip("/")
        self.path = config.get("chat_path", CURSOR_CHAT_PATH)

    # --- public surface (same shape as Provider.chat / chat_stream) -------
    async def chat(self, oai_req: dict) -> dict:
        if not _PB_OK:
            raise self._missing_stubs()
        req = self._build_request(oai_req)
        headers = self._headers()
        r = await self.http.post(self.host + self.path, headers=headers,
                                 content=req.SerializeToString())
        err = self.provider._classify_http(r.status_code)
        if err:
            raise err
        r.raise_for_status()
        return self._to_oai(r.content, oai_req["model"])

    async def chat_stream(self, oai_req: dict) -> AsyncIterator[dict]:
        if not _PB_OK:
            raise self._missing_stubs()
        req = self._build_request(oai_req)
        cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        headers = self._headers()
        async with self.http.stream("POST", self.host + self.path,
                                    headers=headers, content=req.SerializeToString()) as r:
            err = self.provider._classify_http(r.status_code)
            if err:
                raise err
            buf = b""
            async for raw in r.aiter_raw():
                buf += raw
                # consume every COMPLETE Connect-framed message; keep the rest
                i = 0
                while i + 5 <= len(buf):
                    length = struct.unpack(">I", buf[i + 1:i + 5])[0]
                    end = i + 5 + length
                    if end > len(buf):
                        break  # incomplete frame; wait for more bytes
                    payload = buf[i + 5:end]
                    for obj in self._parse_stream(payload, oai_req["model"], cid):
                        yield obj
                    i = end
                buf = buf[i:]
            # flush any trailing complete frames (defensive)
            for _flag, payload in _deframe_connect(buf):
                for obj in self._parse_stream(payload, oai_req["model"], cid):
                    yield obj
            yield self._finish(cid, oai_req["model"])

    # --- concrete (schema-dependent) build / parse -----------------------
    def _build_request(self, oai_req: dict):
        """VERIFY every field name/number against `protoc --decode_raw` output."""
        req = pb.StreamUnifiedChatRequest()           # VERIFY message name
        req.model = self._map_model(oai_req["model"])  # VERIFY field
        for m in oai_req["messages"]:
            turn = req.turns.add()                     # VERIFY repeated field
            turn.role = m["role"]                       # VERIFY ("user"/"assistant"/"system")
            turn.text = _text(m["content"])             # VERIFY
        req.stream = True                              # VERIFY
        # attach metadata headers captured from the real client if present
        for k, v in (self.config.get("request_fields") or {}).items():
            setattr(req, k, v)                          # VERIFY keys
        return req

    def _to_oai(self, raw: bytes, model: str) -> dict:
        """Parse a (non-streamed) StreamUnifiedChatResponse into OpenAI shape."""
        resp = pb.StreamUnifiedChatResponse()           # VERIFY
        resp.ParseFromString(raw)
        text = "".join(p.text for p in resp.parts)      # VERIFY repeated `parts`
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    def _parse_stream(self, raw: bytes, model: str, cid: str) -> list[dict]:
        """Parse ONE Connect-framed protobuf message into OpenAI chunk dicts."""
        resp = pb.StreamUnifiedChatResponse()           # VERIFY
        resp.ParseFromString(raw)
        out = []
        for p in resp.parts:                            # VERIFY
            if p.text:                                  # VERIFY field
                out.append({
                    "id": cid, "object": "chat.completion.chunk",
                    "created": int(time.time()), "model": model,
                    "choices": [{"index": 0,
                                 "delta": {"content": p.text},
                                 "finish_reason": None}],
                })
        return out

    def _finish(self, cid: str, model: str) -> dict:
        return {"id": cid, "object": "chat.completion.chunk",
                "created": int(time.time()), "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}

    # --- helpers ---------------------------------------------------------
    def _map_model(self, m: str) -> str:
        return self.config.get("model_map", {}).get(m, m.split("/")[-1])

    def _headers(self) -> dict:
        h = {
            "authorization": f"Bearer {self.account.cred.access_token}",
            "content-type": "application/connect+proto",
            "connect-protocol-version": "1",
        }
        # VERIFY: x-cursor-checksum derivation (machine id + timestamp obfuscation).
        # See burpheart/cursor-tap for the algorithm; mirror it here.
        ck = self.config.get("checksum")
        if ck:
            h["x-cursor-checksum"] = ck
        else:
            h["x-cursor-checksum"] = self._checksum()
        # mirror any captured client-metadata headers
        h.update(self.config.get("extra_headers", {}))
        return h

    def _checksum(self) -> str:
        return self.config.get("checksum", "")

    @staticmethod
    def _missing_stubs() -> UpstreamError:
        return UpstreamError(
            501,
            "cursor protobuf stubs missing. Capture a real request with "
            "tools/mitm_discover.py, then: protoc --python_out=aigw/proto "
            "--proto_path=aigw/proto aigw/proto/cursor.proto  (see "
            "aigw/proto/cursor.proto). Set cursor.use_proto: true after.",
            retryable=False,
        )


def stubs_available() -> bool:
    return _PB_OK
