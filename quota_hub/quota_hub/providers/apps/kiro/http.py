"""Kiro served over its own streaming API — the tool call comes back into our loop.

Measured on this machine 2026-10-31 with kiro-cli's own credential (read-only; we never
write its database) and one short turn:

    POST https://management.us-east-1.kiro.dev/   X-Amz-Target: …Service.ListAvailableModels
        → 200, 5 models (`auto` is NOT one of them: INVALID_MODEL_ID)
    POST https://runtime.us-east-1.kiro.dev/      X-Amz-Target: …StreamingService
                                                    .GenerateAssistantResponse
        → 200, content-type application/vnd.amazon.eventstream, 1619 bytes, 10 frames

The frames settled what two third-party implementations disagreed about:

* prelude CRC = CRC32-IEEE over the first 8 bytes; message CRC = CRC32 over everything up
  to (but not including) its own 4 bytes. Both verified on all ten real frames.
* there is **no** `__streaming_termination` frame — the HTTP body just ends. Completion is
  semantic: `metadataEvent{"stopReason":"TOOL_USE"}` (or END_TURN / MAX_TOKENS).
* a tool call arrives as three `toolUseEvent` frames: `{name,toolUseId}`, then
  `{input:"<json string>"}`, then `{stop:true}`.
* header value type 7 is string and 6 is byte-array, each with a u16 length.
* the request body needs `conversationState.agentTaskType` and
  `inputSchema.json` as an **object** (a stringified schema is `REQUEST_BODY_INVALID`).
* rate limits ride HTTP 200 with `application/json`, not an event-stream — reading a
  prelude there is the misleading failure the reference projects both warn about.

Tools therefore come back natively; this leg is pure HTTP — without a credential it
raises `UpstreamError(reauth=True)` and the card asks the owner to run `kiro-cli login`.
There is no spawn path to fall back on.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import struct
import time
import uuid
import zlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Iterator

import httpx

from ...base import Account, UpstreamError

logger = logging.getLogger("quota_hub.kiro")

#: Do not start a turn on a token about to die mid-flight.
_LEASE_SKEW = 120.0
_MAX_FRAME = 16 * 1024 * 1024

_CREDENTIAL_KEYS = ("kirocli:social:token", "kirocli:oidc:token", "kirocli:odic:token",
                    "codewhisperer:social:token", "codewhisperer:oidc:token")
_PROFILE_KEY = "api.codewhisperer.profile"

_STOP_REASON = {"END_TURN": "stop", "STOP": "stop", "MAX_TOKENS": "length", "TOOL_USE": "tool_calls"}
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?Z?$")


def _epoch(text: object) -> float | None:
    """`expires_at` arrives as an RFC3339 string (measured: 27 chars, `...Z`)."""
    if isinstance(text, bool) or text is None:
        return None
    if isinstance(text, (int, float)):
        stamp = float(text)
        return stamp / 1000.0 if stamp > 1e11 else stamp
    match = _ISO.match(str(text).strip())
    if not match:
        return None
    import calendar

    year, month, day, hour, minute, second = (int(part) for part in match.groups())
    try:
        return calendar.timegm((year, month, day, hour, minute, second, 0, 0, 0))
    except (ValueError, OverflowError):
        return None


def database_path(config: dict) -> Path:
    """kiro-cli's own database - overridable so no test reads (or locks) his file."""
    explicit = str(config.get("data_sqlite") or os.environ.get("KIRO_DATA_SQLITE") or "").strip()
    if explicit:
        return Path(explicit)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "kiro-cli" / "data.sqlite3"


def _row_value(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value if isinstance(value, str) else ""


def read_stored_auth(config: dict) -> dict[str, Any]:
    """The account kiro-cli keeps, read-only. Never written, never logged.

    Returns {} when the file, the table or the token is missing. The payload is
    snake_case on this machine (measured): access_token / expires_at / refresh_token /
    provider / profile_arn.
    """
    path = database_path(config)
    if not path.is_file():
        return {}
    try:
        # `mode=ro` only. `immutable=1` would let us keep serving a stale page after
        # kiro-cli renews the row - the one thing this read exists to notice.
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=0.25)
    except sqlite3.Error:
        return {}
    try:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "auth_kv" not in tables:
            return {}
        keys = {k: _row_value(v) for k, v in con.execute("SELECT key, value FROM auth_kv")}
        profile_arn = ""
        if "state" in tables:
            for key, value in con.execute("SELECT key, value FROM state"):
                if str(key) == _PROFILE_KEY:
                    text = _row_value(value)
                    try:
                        parsed = json.loads(text)
                        profile_arn = str(parsed.get("arn") or parsed) if isinstance(parsed, dict) else text
                    except ValueError:
                        profile_arn = text
                    break
        for name in _CREDENTIAL_KEYS:
            text = keys.get(name)
            if not text:
                continue
            try:
                payload = json.loads(text)
            except ValueError:
                continue
            if not isinstance(payload, dict):
                continue
            token = str(payload.get("access_token") or payload.get("accessToken") or "")
            if not token:
                continue
            arn = str(payload.get("profile_arn") or payload.get("profileArn") or profile_arn or "")
            return {"access_token": token,
                    "refresh_token": str(payload.get("refresh_token") or payload.get("refreshToken") or ""),
                    "expires_at": _epoch(payload.get("expires_at") or payload.get("expiresAt")),
                    "profile_arn": arn,
                    "provider": str(payload.get("provider") or ""),
                    "region": str(payload.get("region") or _region_from_arn(arn))}
        return {}
    except sqlite3.Error:
        return {}
    finally:
        con.close()


def _region_from_arn(arn: str) -> str:
    parts = (arn or "").split(":")
    return parts[3] if len(parts) >= 4 and parts[3] else "us-east-1"


def decode_frames(data: bytes) -> Iterator[tuple[str, dict, Any]]:
    """Yield (event_type, headers, payload) for each AWS event-stream frame.

    CRCs are verified because a silent desync would look like a truncated answer: prelude
    CRC over bytes 0..8, message CRC over everything before its own 4 bytes.
    """
    pos = 0
    size = len(data)
    while pos + 12 <= size:
        total, headers_len = struct.unpack(">II", data[pos:pos + 8])
        if total < 16 or total > _MAX_FRAME or pos + total > size:
            return
        prelude_crc = struct.unpack(">I", data[pos + 8:pos + 12])[0]
        if (zlib.crc32(data[pos:pos + 8]) & 0xFFFFFFFF) != prelude_crc:
            return
        message_crc = struct.unpack(">I", data[pos + total - 4:pos + total])[0]
        if (zlib.crc32(data[pos:pos + total - 4]) & 0xFFFFFFFF) != message_crc:
            return
        cursor = pos + 12
        end = cursor + headers_len
        headers: dict[str, Any] = {}
        while cursor + 1 <= end - 1:
            name_len = data[cursor]
            cursor += 1
            name = data[cursor:cursor + name_len].decode("utf-8", "replace")
            cursor += name_len
            vtype = data[cursor]
            cursor += 1
            if vtype in (0, 1):
                headers[name] = vtype == 0
            elif vtype == 2:
                headers[name] = struct.unpack(">b", data[cursor:cursor + 1])[0]
                cursor += 1
            elif vtype == 3:
                headers[name] = struct.unpack(">h", data[cursor:cursor + 2])[0]
                cursor += 2
            elif vtype == 4:
                headers[name] = struct.unpack(">i", data[cursor:cursor + 4])[0]
                cursor += 4
            elif vtype == 5:
                headers[name] = struct.unpack(">q", data[cursor:cursor + 8])[0]
                cursor += 8
            elif vtype in (6, 7):
                (length,) = struct.unpack(">H", data[cursor:cursor + 2])
                cursor += 2
                raw = data[cursor:cursor + length]
                cursor += length
                headers[name] = raw if vtype == 6 else raw.decode("utf-8", "replace")
            elif vtype == 8:
                headers[name] = struct.unpack(">q", data[cursor:cursor + 8])[0]
                cursor += 8
            elif vtype == 9:
                headers[name] = data[cursor:cursor + 16].hex()
                cursor += 16
            else:
                headers[name] = f"<unknown vtype {vtype}>"
                break
        payload = data[end:pos + total - 4]
        try:
            parsed: Any = json.loads(payload.decode("utf-8")) if payload else {}
        except (UnicodeDecodeError, ValueError):
            parsed = {"raw": payload.decode("utf-8", "replace")}
        event = str(headers.get(":event-type") or headers.get(":exception-type")
                    or headers.get(":error-code") or "")
        yield event, headers, parsed
        pos += total


def _sanitize_schema(tools: list[dict]) -> list[dict]:
    """OpenAI tool array → Kiro's union entries, with the schema as an object.

    Upstream refuses a schema whose root is not `type: object` (TOOL_SCHEMA_INVALID), and a
    stringified schema is REQUEST_BODY_INVALID, so both get normalized here.
    """
    entries: list[dict] = []
    for tool in tools or []:
        function = tool.get("function") if isinstance(tool, dict) else None
        if not isinstance(function, dict) or not function.get("name"):
            continue
        schema = function.get("parameters") or {}
        if isinstance(schema, str):
            try:
                schema = json.loads(schema)
            except ValueError:
                schema = {}
        if not isinstance(schema, dict) or schema.get("type") != "object":
            schema = {"type": "object", "properties": (schema or {}).get("properties", {}) if isinstance(schema, dict) else {}}
        entries.append({"toolSpecification": {
            "name": str(function["name"])[:64],
            "description": str(function.get("description") or ""),
            "inputSchema": {"json": schema}}})
    return entries


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    return "" if content is None else str(content)


def build_conversation(oai_req: dict, model_id: str) -> dict:
    """OpenAI messages → Kiro's `conversationState` (current message + history)."""
    messages = list(oai_req.get("messages") or [])
    systems = [_message_text(m.get("content")) for m in messages if m.get("role") == "system"]
    turns = [m for m in messages if m.get("role") != "system"]

    history: list[dict] = []
    pending_results: list[dict] = []
    current_content = ""

    for index, message in enumerate(turns):
        role = str(message.get("role"))
        last = index == len(turns) - 1
        if role == "tool":
            pending_results.append({"toolUseId": str(message.get("tool_call_id") or ""),
                                    "status": "success",
                                    "content": [{"text": _message_text(message.get("content"))}]})
            if not last:
                continue
        elif role == "assistant":
            calls = message.get("tool_calls") or []
            entry: dict[str, Any] = {"assistantResponseMessage": {
                "content": _message_text(message.get("content")),
                "toolUses": [{"toolUseId": str(call.get("id") or ""),
                              "name": str(((call.get("function") or {}).get("name")) or ""),
                              "input": _json_or_text((call.get("function") or {}).get("arguments"))}
                             for call in calls]}}
            if not last or (not calls and not pending_results):
                history.append(entry)
            if last and (not calls):
                continue
        elif role == "user":
            if last:
                current_content = _message_text(message.get("content"))
            else:
                item: dict[str, Any] = {"userInputMessage": {"content": _message_text(message.get("content")),
                                                             "origin": "KIRO_CLI"}}
                history.append(item)

    if not current_content and turns:
        current_content = _message_text(turns[-1].get("content"))

    context: dict[str, Any] = {
        "envState": {"operatingSystem": "windows",
                     "currentWorkingDirectory": str(oai_req.get("cwd") or os.getcwd())},
    }
    tools = _sanitize_schema(oai_req.get("tools") or [])
    if tools:
        context["tools"] = tools
    if pending_results:
        context["toolResults"] = pending_results
    if systems:
        context["systemPrompt"] = "\n".join(systems)
    return {
        "conversationId": str(oai_req.get("conversation_id") or uuid.uuid4()),
        "chatTriggerType": "MANUAL",
        "agentTaskType": "vibe",
        "currentMessage": {"userInputMessage": {
            "content": current_content,
            "modelId": model_id,
            "origin": "KIRO_CLI",
            "userInputMessageContext": context}},
        **({"history": history} if history else {}),
    }


def _json_or_text(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value if value is not None else {}


class KiroHttpMixin:
    """Serves the `kiro_cli` leg over Kiro's own streaming API — the only door.

    The credential itself is the door: no `transport` switch, because there is no second
    mouth to choose. Without a credential every entry point degrades explicitly (verdicts
    say `no_credential`, chat raises `UpstreamError(reauth=True)`, catalogue says `None`),
    and the card asks the owner to run Kiro's own login.
    """

    def _auth(self) -> dict[str, Any]:
        """The last readable session, kept across a locked read.

        Measured 2026-10-31: kiro-cli holds the database open, and a read that lands
        inside its write returns nothing. Without this, a transient lock would look like
        the session ended and a live card would start asking the owner to log in again.
        A failed read is not evidence the session ended.
        """
        data = read_stored_auth(self.config)
        if data.get("access_token"):
            self._auth_cached = data
            return data
        cached = getattr(self, "_auth_cached", None) or {}
        expires = cached.get("expires_at")
        if cached.get("access_token") and (not expires or time.time() < float(expires) - _LEASE_SKEW):
            return cached
        return {}

    def _model_id(self, requested: str) -> str:
        prefix = f"{self._prefix()}/"
        return requested[len(prefix):] if requested.startswith(prefix) else requested

    #: Our ids are namespaced by the card's own registry name (see `cli.py`).
    def _prefix(self) -> str:
        return str(getattr(self, "model_prefix", "") or self.name)

    # --- verdicts --------------------------------------------------------
    def signed_in(self) -> tuple[bool, str]:
        if self._auth().get("access_token"):
            return True, "ok"
        return False, "no_credential"

    def credential_health(self) -> tuple[bool, str]:
        data = self._auth()
        token = data.get("access_token")
        if not token:
            return False, "no_credential"
        expires = data.get("expires_at")
        if expires and time.time() >= float(expires) - _LEASE_SKEW:
            # kiro-cli renews this itself; the screen already says "打开那个应用一次就能续上".
            return False, "credential_expired"
        return True, "ok"

    # --- accounts / catalogue -------------------------------------------
    async def discover_accounts(self) -> list[Account]:
        data = self._auth()
        if not data.get("access_token"):
            return []
        raw = self.config.get("accounts") or [{}]
        accounts = [
            Account(
                id=str(a.get("id") or f"{self.name}-default"),
                provider=self.name,
                label=str(a.get("label") or self.display_name or ""),
            )
            for a in raw
        ]
        for acc in accounts:
            acc.cred.access_token = str(data["access_token"])
            acc.cred.extra["kiro_profile_arn"] = data.get("profile_arn", "")
            acc.cred.extra["kiro_region"] = data.get("region", "")
        return accounts

    async def ensure_fresh(self, acc: Account) -> None:
        """Follow kiro-cli's own renewal; we never spend its refresh token.

        Rotating it from here would sign the CLI out, and a half-rotated session is the
        kind of breakage no tooltip can explain.
        """
        data = self._auth()
        token = data.get("access_token")
        expires = data.get("expires_at")
        if not token or (expires and time.time() >= float(expires) - _LEASE_SKEW):
            raise UpstreamError(401, "kiro: credential expired", reauth=True)
        acc.cred.access_token = str(token)
        acc.cred.extra["kiro_profile_arn"] = data.get("profile_arn", "") or acc.cred.extra.get("kiro_profile_arn", "")
        acc.cred.extra["kiro_region"] = data.get("region", "") or acc.cred.extra.get("kiro_region", "")

    def _management_url(self, acc: Account) -> str:
        region = str(acc.cred.extra.get("kiro_region") or _region_from_arn(
            str(acc.cred.extra.get("kiro_profile_arn") or "")))
        return f"https://management.{region}.kiro.dev/"

    def _runtime_url(self, acc: Account) -> str:
        region = str(acc.cred.extra.get("kiro_region") or _region_from_arn(
            str(acc.cred.extra.get("kiro_profile_arn") or "")))
        return f"https://runtime.{region}.kiro.dev/"

    def _headers(self, acc: Account, target: str) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {acc.cred.access_token}",
            "Content-Type": "application/x-amz-json-1.0",
            "Accept": "*/*",
            "X-Amz-Target": target,
            "User-Agent": "aws-sdk-rust/1.3.15 ua/2.1 api/codewhispererstreaming/0.1.17593 os/windows lang/python/3.12",
            "x-amzn-codewhisperer-optout": "false",
        }
        if str(self.config.get("token_type") or "").strip():
            headers["TokenType"] = str(self.config["token_type"])
        return headers

    async def refresh_models(self, acc: Account) -> list[str] | None:
        if not self._auth().get("access_token"):
            return None
        try:
            await self.ensure_fresh(acc)
        except UpstreamError:
            return None
        prefix = self._prefix()
        try:
            res = await self.http.post(self._management_url(acc),
                                       headers=self._headers(acc, "AmazonCodeWhispererService.ListAvailableModels"),
                                       json={"origin": "KIRO_CLI",
                                             "profileArn": str(acc.cred.extra.get("kiro_profile_arn") or "")})
            if res.status_code != 200:
                return None
            data = res.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("kiro model list failed: %s: %s", type(exc).__name__, exc)
            return None
        rows = data.get("models") if isinstance(data, dict) else None
        if not isinstance(rows, list) or not rows:
            return None
        short = [str(row.get("modelId")) for row in rows if isinstance(row, dict) and row.get("modelId")]
        # Keep the rows themselves: they carry the numbers the picker may show and we
        # must never invent (measured 2026-11-01: `tokenLimits.maxInputTokens` = 1M on
        # `auto`, 200K on claude-sonnet-4.5, 164K on deepseek-3.2; `maxOutputTokens`
        # 64000; `supportedInputTypes` says IMAGE for the two Claudes and TEXT-only for
        # the others). Kiro publishes no reasoning-effort menu at all, so this leg says
        # nothing about `efforts` instead of guessing one.
        self._model_rows = {str(row.get("modelId")): row for row in rows if isinstance(row, dict)}
        self.served_models = tuple(f"{prefix}/{m}" for m in short)
        return short

    def tuning(self, model: str) -> dict:
        """Kiro's own per-model limits, from `ListAvailableModels` (no effort menu)."""
        row = getattr(self, "_model_rows", {}).get(self._model_id(model)) or {}
        limits = row.get("tokenLimits") if isinstance(row.get("tokenLimits"), dict) else {}
        out: dict[str, Any] = {}
        if isinstance(limits.get("maxInputTokens"), (int, float)):
            out["context"] = {"max": int(limits["maxInputTokens"])}
        if isinstance(limits.get("maxOutputTokens"), (int, float)):
            out["max_output"] = int(limits["maxOutputTokens"])
        return out

    # --- request path ----------------------------------------------------
    async def chat(self, acc: Account, oai_req: dict) -> dict:
        if not self._auth().get("access_token"):
            raise UpstreamError(401, "kiro: no credential, run `kiro-cli login`", reauth=True)
        content: list[str] = []
        calls: dict[str, dict] = {}
        finish = ""
        usage: dict = {}
        async for chunk in self.chat_stream(acc, oai_req):
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    content.append(str(delta["content"]))
                for call in delta.get("tool_calls") or []:
                    cid = str(call.get("id") or f"call_{len(calls)}")
                    function = call.get("function") or {}
                    slot = calls.setdefault(cid, {"id": cid, "name": "", "arguments": "", "order": len(calls)})
                    slot["name"] = slot["name"] or str(function.get("name") or "")
                    slot["arguments"] += str(function.get("arguments") or "")
        message: dict[str, Any] = {"role": "assistant"}
        if calls:
            ordered = [calls[cid] for cid in sorted(calls, key=lambda key: calls[key]["order"])]
            message["content"] = None
            message["tool_calls"] = [{"id": slot["id"], "type": "function", "function": {
                "name": slot["name"], "arguments": slot["arguments"]}} for slot in ordered]
        else:
            message["content"] = "".join(content)
        return {"id": f"kiro-{uuid.uuid4().hex[:12]}", "object": "chat.completion",
                "created": int(time.time()), "model": oai_req.get("model"),
                "choices": [{"index": 0, "message": message,
                             "finish_reason": finish or ("tool_calls" if calls else "stop")}],
                "usage": usage}

    async def chat_stream(self, acc: Account, oai_req: dict) -> AsyncIterator[dict]:
        if not self._auth().get("access_token"):
            raise UpstreamError(401, "kiro: no credential, run `kiro-cli login`", reauth=True)
        await self.ensure_fresh(acc)
        model_id = self._model_id(str(oai_req.get("model") or ""))
        body = {"conversationState": build_conversation(oai_req, model_id),
                "profileArn": str(acc.cred.extra.get("kiro_profile_arn") or "")}
        url = self._runtime_url(acc)
        emitted: dict[str, int] = {}
        async with self.http.stream(
                "POST", url,
                headers=self._headers(acc, "AmazonCodeWhispererStreamingService.GenerateAssistantResponse"),
                json=body, timeout=httpx.Timeout(180.0, connect=10.0)) as res:
            content_type = res.headers.get("content-type", "")
            raw = await res.aread()
            if res.status_code != 200 or "eventstream" not in content_type:
                # A rate limit arrives as HTTP 200 with a JSON AWS envelope; treating that
                # as a frame stream fails with a misleading "bad prelude" instead.
                raise self._error(res.status_code, raw.decode("utf-8", "replace"), content_type)
        for event, _headers, payload in decode_frames(raw):
            if not isinstance(payload, dict):
                continue
            if event == "assistantResponseEvent":
                if payload.get("content"):
                    yield self._chunk(oai_req, {"content": payload["content"]})
            elif event == "reasoningContentEvent":
                if payload.get("text"):
                    yield self._chunk(oai_req, {"reasoning_content": payload["text"]})
            elif event == "toolUseEvent":
                cid = str(payload.get("toolUseId") or f"call_{len(emitted)}")
                if cid not in emitted:
                    emitted[cid] = len(emitted)
                delta: dict[str, Any] = {"tool_calls": [{"index": emitted[cid], "id": cid, "type": "function",
                                                         "function": {"name": str(payload.get("name") or ""),
                                                                     "arguments": ""}}]}
                if payload.get("input") is not None:
                    delta["tool_calls"][0]["function"]["arguments"] = str(payload["input"])
                yield self._chunk(oai_req, delta)
            elif event == "metadataEvent":
                reason = _STOP_REASON.get(str(payload.get("stopReason") or ""), "")
                usage = _usage(payload.get("tokenUsage") or payload.get("usage") or {})
                if usage:
                    yield {"choices": [], "usage": usage, "model": oai_req.get("model"),
                           "object": "chat.completion.chunk"}
                if reason:
                    yield self._chunk(oai_req, {}, finish=reason)
            elif event == "meteringEvent":
                # Kiro reports the token counts on their own frame, not inside
                # metadataEvent (measured: metadataEvent carries only stopReason).
                usage = _usage(payload.get("usage") or payload)
                if usage:
                    yield {"choices": [], "usage": usage, "model": oai_req.get("model"),
                           "object": "chat.completion.chunk"}
            elif event in ("invalidStateEvent", "AccessDeniedException", "ThrottlingException",
                           "ValidationException", "ServiceUnavailableException"):
                raise UpstreamError(400, f"kiro {event}: {str(payload)[:200]}", retryable=event == "ThrottlingException")

    def _chunk(self, oai_req: dict, delta: dict, finish: str = "") -> dict:
        return {"id": f"kiro-{uuid.uuid4().hex[:8]}", "object": "chat.completion.chunk",
                "created": int(time.time()), "model": oai_req.get("model"),
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish or None}]}

    def _error(self, status: int, text: str, content_type: str) -> UpstreamError:
        detail = (text or "")[:240]
        if status == 401:
            return UpstreamError(401, f"kiro: {detail}", reauth=True)
        if status == 403:
            return UpstreamError(403, f"kiro: {detail}", retryable=False)
        if status == 429 or "Throttling" in detail or "TooManyRequests" in detail:
            return UpstreamError(429, f"kiro: {detail}", retryable=True, cooldown=30.0)
        if status >= 500 or "ServiceUnavailable" in detail:
            return UpstreamError(status or 503, f"kiro: {detail}", retryable=True, cooldown=5.0)
        return UpstreamError(status, f"kiro: {detail}", retryable=False)


def _int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def _usage(token_usage: Any) -> dict:
    if not isinstance(token_usage, dict):
        return {}
    prompt = _int(token_usage.get("inputTokens") or token_usage.get("uncachedInputTokens"))
    completion = _int(token_usage.get("outputTokens"))
    total = _int(token_usage.get("totalTokens")) or (prompt + completion)
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}
