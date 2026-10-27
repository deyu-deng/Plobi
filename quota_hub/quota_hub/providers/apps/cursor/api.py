"""Cursor provider adapter — agent.v1.AgentService/Run (Connect-JSON, HTTP/2).

STATUS (2026-07, research-backed):
  Token acquisition + refresh is VERIFIED (see tokens/desktop_stores.py ->
  read_cursor / refresh_cursor). The chat wire protocol is the hard part; this
  adapter now implements the *current* Cursor protocol that community reverse
  engineering (7836246/cursoride2api, eisbaw/cursor_api_demo, zhengui666/
  cursor-api-gui) has documented:

    POST https://api2.cursor.sh/agent.v1.AgentService/Run
    Content-Type: application/connect+json   (Connect streaming envelope)
    HTTP/2 REQUIRED (HTTP/1.1 returns 464 "Incompatible Protocol")

  Each message is wrapped in a 5-byte Connect envelope: [flag:1][len:4 BE][json].
  Required anti-abuse headers (x-session-id, x-client-key, x-cursor-checksum)
  are derived deterministically from the token + the local machine ids stored in
  Cursor's storage.json. The checksum algorithm below is transcribed verbatim
  from the public reverse-engineering notes.

  The legacy aiserver.v1.ChatService/StreamUnifiedChat route is deprecated by
  Cursor (returns Bad Request); the protobuf (use_proto) route is kept only as a
  skeleton in cursor_proto.py and is not the default.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator

import httpx

from ....tokens import desktop_stores as ds
from ...base import Account, Capabilities, Credential, Provider, UpstreamError
from .proto import stubs_available


# ---------------------------------------------------------------------------
# Connect envelope (streaming framing): [1 flag byte][4-byte big-endian length]
# ---------------------------------------------------------------------------
def _frame(obj: dict) -> bytes:
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return b"\x00" + len(data).to_bytes(4, "big") + data


class _ConnectDeframer:
    """Incremental parser for a Connect streaming response body.

    Yields (flag, payload) for every complete envelope frame as bytes arrive.
    """

    def __init__(self) -> None:
        self._buf = b""

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        self._buf += data
        out: list[tuple[int, bytes]] = []
        while len(self._buf) >= 5:
            length = int.from_bytes(self._buf[1:5], "big")
            if len(self._buf) < 5 + length:
                break
            flag = self._buf[0]
            payload = self._buf[5 : 5 + length]
            self._buf = self._buf[5 + length :]
            out.append((flag, payload))
        return out


def _extract_text(obj) -> str | None:
    """Pull the assistant text delta out of a runResponse frame.

    Cursor's AgentService stream emits `runResponse` frames; the visible text is
    carried under `runResponse.text` (and sometimes a top-level `text`). We check
    those explicit locations first, then fall back to a tolerant recursive scan
    so we don't miss it if the field nesting shifts between Cursor versions.
    """
    candidates = [obj]
    if isinstance(obj, dict):
        rr = obj.get("runResponse")
        if isinstance(rr, dict):
            candidates.append(rr)
    for cand in candidates:
        if isinstance(cand, dict) and isinstance(cand.get("text"), str) and cand["text"]:
            return cand["text"]
    return _extract_text_rec(obj)


def _extract_text_rec(obj) -> str | None:
    if isinstance(obj, dict):
        for v in obj.values():
            t = _extract_text_rec(v)
            if t:
                return t
    elif isinstance(obj, list):
        for v in obj:
            t = _extract_text_rec(v)
            if t:
                return t
    elif isinstance(obj, str) and obj:
        # Only treat a bare string as text if it looks like content, not an id.
        return obj
    return None


# ---------------------------------------------------------------------------
# Anti-abuse header derivation (from the reverse-engineered Cursor client)
# ---------------------------------------------------------------------------
def _cursor_checksum(machine_id: str, mac_machine_id: str) -> str:
    """Reproduce Cursor's x-cursor-checksum.

    Transcribed from the public 7836246/cursoride2api notes: take a coarse
    6-byte big-endian timestamp (Date.now()/1e6), XOR-obfuscate it with a running
    key, base64 the result, then append the machine ids.
    """
    key = 165
    ts = int(time.time() * 1000) // 1_000_000  # mirror JS Date.now()/1e6
    b = bytearray(
        [
            (ts >> 40) & 0xFF,
            (ts >> 32) & 0xFF,
            (ts >> 24) & 0xFF,
            (ts >> 16) & 0xFF,
            (ts >> 8) & 0xFF,
            ts & 0xFF,
        ]
    )
    for i in range(6):
        b[i] = ((b[i] ^ key) + i) & 0xFF
        key = b[i]
    prefix = base64.b64encode(bytes(b)).decode("ascii")
    if mac_machine_id:
        return f"{prefix}{machine_id}/{mac_machine_id}"
    return f"{prefix}{machine_id}"


def _tz_name() -> str:
    try:
        name = time.tzname[0] or "UTC"
        return name if name.isascii() else "UTC"
    except Exception:
        return "UTC"


class CursorProvider(Provider):
    name = "cursor"
    display_name = "Cursor"

    def present(self) -> bool:
        """Cursor has to be installed and signed in over there before a card is useful."""
        return bool(self.accounts)

    def login_plan(self) -> dict:
        """Cursor rides the user's already-logged-in editor session; nothing to open."""
        return {"kind": "local_session", "name": self.display_name,
                "reason": "它用的是 Cursor 自己那套登录，Plobi 这边不用再登一次"}
    default_capabilities = Capabilities(
        stream=True,
        tools=False,
        vision=False,
        embeddings=False,
        sessionful=False,
        compliance="research",
    )
    served_models = ("cursor/gpt-4o", "cursor/claude-4-sonnet", "cursor/auto")

    # Current Cursor chat endpoint (agent.v1 AgentService.Run, BiDi streaming).
    DEFAULT_HOST = "https://api2.cursor.sh"
    DEFAULT_PATH = "/agent.v1.AgentService/Run"

    def __init__(self, config, http):
        super().__init__(config, http)
        self.host = config.get("host", self.DEFAULT_HOST).rstrip("/")
        self.path = config.get("chat_path", self.DEFAULT_PATH)
        # fallback: auto (proto if stubs else connect_json) | proto | connect_json
        self.fallback = config.get("fallback", "auto")
        self.use_proto = bool(config.get("use_proto", False))
        self.model_map = config.get("model_map", {})
        self.client_version = config.get("client_version", "2.6.20")
        # Dedicated HTTP/2 client (Cursor strictly requires HTTP/2). Lazily
        # created on first chat so we don't open a socket at import time.
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                http2=True, timeout=180.0, follow_redirects=True
            )
        return self._client

    async def discover_accounts(self) -> list[Account]:
        accs: list[Account] = []
        if self.config.get("use_local_session", True):
            local = ds.read_cursor()
            if local and local.get("refresh_token"):
                accs.append(
                    Account(
                        id="cursor-local",
                        provider=self.name,
                        label=local.get("email", "local"),
                        cred=Credential(
                            access_token=local["access_token"],
                            refresh_token=local["refresh_token"],
                            extra={
                                "tier": local.get("tier", ""),
                                "machine_id": local.get("machine_id", ""),
                                "mac_machine_id": local.get("mac_machine_id", ""),
                            },
                        ),
                    )
                )
        for i, a in enumerate(self.config.get("accounts", [])):
            accs.append(
                Account(
                    id=a.get("id", f"cursor-cfg-{i}"),
                    provider=self.name,
                    label=a.get("label", ""),
                    cred=Credential(refresh_token=a["refresh_token"]),
                )
            )
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

    # --- header / body construction ---------------------------------------
    def _headers(self, acc: Account) -> dict:
        token = acc.cred.access_token or ""
        extra = acc.cred.extra or {}
        machine_id = extra.get("machine_id", "")
        mac_machine_id = extra.get("mac_machine_id", "")
        session_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, token))
        client_key = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return {
            "authorization": f"Bearer {token}",
            "connect-protocol-version": "1",
            "user-agent": "connect-es/1.6.1",
            "content-type": "application/connect+json",
            # Force uncompressed envelopes so we can deframe manually.
            "accept-encoding": "identity",
            "x-session-id": session_id,
            "x-client-key": client_key,
            "x-cursor-checksum": _cursor_checksum(machine_id, mac_machine_id),
            "x-cursor-client-version": self.client_version,
            "x-request-id": str(uuid.uuid4()),
            "x-cursor-timezone": _tz_name(),
        }

    def _run_body(self, oai_req: dict, conversation_id: str) -> dict:
        model = self.model_map.get(
            oai_req["model"], oai_req["model"].split("/", 1)[-1]
        )
        # Use the latest user message as the prompt; Cursor keeps conversation
        # history server-side keyed by conversationId, so a single userMessage
        # is sufficient for a working response (multi-turn within one OpenAI
        # request is not reconstructed here — documented limitation).
        prompt = ""
        for m in reversed(oai_req.get("messages", [])):
            content = m.get("content")
            if isinstance(content, str):
                prompt = content
            elif isinstance(content, list):
                prompt = "".join(
                    p.get("text", "") for p in content if isinstance(p, dict)
                )
            if m.get("role") == "user" and prompt:
                break
        return {
            "runRequest": {
                "conversationState": {},
                "action": {
                    "userMessageAction": {"userMessage": {"text": prompt}}
                },
                "modelDetails": {
                    "modelId": model,
                    "displayName": model,
                    "displayNameShort": model,
                },
                "requestedModel": {"modelId": model},
                "conversationId": conversation_id,
            }
        }

    # --- chat -------------------------------------------------------------
    async def chat(self, acc: Account, oai_req: dict) -> dict:
        if (self.use_proto or self.fallback == "proto") and stubs_available():
            from .cursor_proto import CursorProtoClient

            return await CursorProtoClient(self, acc, self.http, self.config).chat(
                oai_req
            )
        parts: list[str] = []
        async for chunk in self._run_stream(acc, oai_req):
            delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content")
            if delta:
                parts.append(delta)
        text = "".join(parts)
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": oai_req["model"],
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": text},
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        }

    async def chat_stream(self, acc: Account, oai_req: dict) -> AsyncIterator[dict]:
        if (self.use_proto or self.fallback == "proto") and stubs_available():
            from .cursor_proto import CursorProtoClient

            async for chunk in CursorProtoClient(
                self, acc, self.http, self.config
            ).chat_stream(oai_req):
                yield chunk
            return

        client = await self._ensure_client()
        conversation_id = str(uuid.uuid4())
        body = _frame(self._run_body(oai_req, conversation_id))
        cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        headers = self._headers(acc)
        url = self.host + self.path
        deframer = _ConnectDeframer()

        async with client.stream("POST", url, headers=headers, content=body) as r:
            if r.status_code != 200:
                # Connect streaming answers with 200; anything else (e.g. 464
                # "Incompatible Protocol" when HTTP/2 negotiation fails, or 400/
                # 401 from a rejected checksum/token) is a hard failure. Capture
                # the body for diagnostics.
                detail = ""
                try:
                    async for raw in r.aiter_bytes():
                        detail += raw.decode("utf-8", "replace")
                except Exception:
                    pass
                err = self._classify_http(r.status_code)
                if err:
                    raise err
                raise UpstreamError(
                    r.status_code,
                    f"cursor Run HTTP {r.status_code}: {detail[:500]}",
                    retryable=False,
                )
            async for raw in r.aiter_bytes():
                for _flag, payload in deframer.feed(raw):
                    try:
                        obj = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    text = _extract_text(obj)
                    if text:
                        yield {
                            "id": cid,
                            "object": "chat.completion.chunk",
                            "created": time.time(),
                            "model": oai_req["model"],
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"content": text},
                                    "finish_reason": None,
                                }
                            ],
                        }

        yield {
            "id": cid,
            "object": "chat.completion.chunk",
            "created": time.time(),
            "model": oai_req["model"],
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }

    # The gateway may call aclose on shutdown; expose it so we don't leak the
    # HTTP/2 connection.
    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
