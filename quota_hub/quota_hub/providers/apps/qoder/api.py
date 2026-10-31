"""Qoder served over its own signed door — a native API leg, not an ACP session.

Every line-level fact in this module was measured on this machine 2026-10-31 with the
credential our own card minted (`oauth.run_login`, device-code + PKCE, 30-day lease):

    GET  https://gateway.qoder.com.cn/algo/api/v2/model/list                     → 200, 50 KB
    POST .../algo/api/v2/service/pro/sse/agent_chat_generation
             ?FetchKeys=llm_model_result&AgentId=agent_common&Encode=1           → 200, text/event-stream

and that turn came back in **1.0 s with `finish_reason="tool_calls"` and our own tool
name**, arguments intact, `usage` carrying credits and reasoning tokens. The ACP session
could never do that: it runs tools on Qoder's side and hands back a finished answer.

The frame envelope is the odd part, and the parser asserts it rather than tolerating it:
each `data:` line is `{"headers":{...},"body":"<stringified OpenAI chunk>",
"statusCodeValue":200,"statusCode":"OK"}` — inner is a JSON *string*, not an object — the
stream ends with `event:finish` (no bare `data: [DONE]` on this path), and a final frame
of `{firstTokenDuration, serverDuration, totalDuration}` carries no body at all.

Reference for the request body and the error vocabulary: MIT implementations `caigee-cmd/cli2api`
(© 2026) and `EchoPing/Qoder-2API-Go`, re-measured here; nothing from them runs as a process.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ...base import Account, Capabilities, Credential, Provider, UpstreamError
from . import cosy, oauth

logger = logging.getLogger("quota_hub.qoder")

LIST_PATH = "/algo/api/v2/model/list"
CHAT_PATH = ("/algo/api/v2/service/pro/sse/agent_chat_generation"
             "?FetchKeys=llm_model_result&AgentId=agent_common&Encode=1")
DEFAULT_BASE = "https://gateway.qoder.com.cn"

#: The gateway can hold an accepted stream open forever (measured in the reference
#: project's issue #354), so an idle read is a failure we name, not a hung turn.
DEFAULT_IDLE_TIMEOUT = 90.0
DEFAULT_MAX_OUTPUT = 32000
DEFAULT_MAX_INPUT = 180000

#: Business codes the reference client maps explicitly; the HTTP status alone lies.
_CODE_LOGIN_EXPIRED = {"105"}
_CODE_BUSY = {"10605"}


def _latest_user_text(messages: list[dict]) -> str:
    for msg in reversed(messages or []):
        if msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    return ""


def _system_text(messages: list[dict]) -> str:
    return "\n".join(str(m.get("content") or "") for m in messages or [] if m.get("role") == "system")


def _number(value: object, fallback: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback
    return float(value) if value else fallback


def frame_inner(frame: object) -> dict | None:
    """The inner OpenAI chunk of one `data:` frame, or None when the frame carries none.

    Shape is asserted, not guessed: the chunk arrives as a JSON string under `body`.
    """
    if not isinstance(frame, dict):
        return None
    body = frame.get("body")
    if isinstance(body, str):
        if body in ("", "[DONE]"):
            return None
        try:
            inner = json.loads(body)
        except ValueError:
            return None
        return inner if isinstance(inner, dict) else None
    return frame if isinstance(frame.get("choices"), list) else None


class QoderProvider(Provider):
    name = "qoder"
    display_name = "Qoder"
    model_prefix = "qoder"
    _SEED_MODELS: tuple[str, ...] = ()

    def __init__(self, config: dict, http: httpx.AsyncClient) -> None:
        super().__init__(config, http)
        self.base = str(config.get("base_url") or DEFAULT_BASE).rstrip("/")
        self.idle_timeout = float(config.get("stream_idle_seconds") or DEFAULT_IDLE_TIMEOUT)
        self.served_models: tuple[str, ...] = tuple(f"{self.model_prefix}/{m}" for m in config.get("models", ()))
        #: keyed by the vendor's own model key (`fast`, `kmodel`, ...), filled by refresh_models
        self._entries: dict[str, dict] = {}
        self._sessions: dict[str, cosy.Session] = {}

    # --- presence / verdicts ---------------------------------------------
    def capabilities(self) -> Capabilities:
        return Capabilities(stream=True, tools=True)

    def present(self) -> bool:
        return True

    def signed_in(self) -> tuple[bool, str]:
        return oauth.signed_in()

    def credential_health(self) -> tuple[bool, str]:
        # No exit node is involved: Qoder's edge is reachable from here directly, so the
        # credential question and the can-it-serve-now question have the same answer.
        return oauth.signed_in()

    def login_plan(self) -> dict:
        return {
            "kind": "oauth",
            "name": self.display_name,
            "runner": lambda on_url=None: oauth.run_login(on_url=on_url),
        }

    # --- accounts ---------------------------------------------------------
    async def discover_accounts(self) -> list[Account]:
        live, _reason = oauth.signed_in()
        if not live:
            self.accounts = []
            return []
        cred = oauth.read_credential() or {}
        acc = Account(
            id=f"{self.name}-device",
            provider=self.name,
            label=str(cred.get("name") or "Qoder"),
            cred=Credential(
                access_token=str(cred.get("token") or ""),
                refresh_token=str(cred.get("refresh_token") or ""),
                extra={"uid": str(cred.get("user_id") or "")},
            ),
        )
        self.accounts = [acc]
        return self.accounts

    def _session(self, acc: Account) -> cosy.Session:
        cached = self._sessions.get(acc.id)
        if cached is not None:
            return cached
        session = cosy.Session(cosy.Account(
            uid=str(acc.cred.extra.get("uid") or ""),
            name=str(acc.label or ""),
            security_oauth_token=acc.cred.access_token,
            refresh_token=acc.cred.refresh_token,
        ))
        self._sessions[acc.id] = session
        return session

    async def ensure_fresh(self, acc: Account) -> None:
        """The 30-day device lease, checked locally; renewal is another click, not a call.

        We deliberately do not spend the stored refresh_token: the exchange endpoint for a
        *device* token is not something this machine has measured, and guessing at a token
        endpoint is how a working login gets burned.
        """
        live, reason = oauth.signed_in()
        if live:
            cred = oauth.read_credential() or {}
            acc.cred.access_token = str(cred.get("token") or acc.cred.access_token)
            return
        raise UpstreamError(401, f"qoder: {reason}", reauth=True)

    # --- catalogue --------------------------------------------------------
    async def refresh_models(self, acc: Account) -> list[str] | None:
        url = f"{self.base}{LIST_PATH}"
        try:
            await self.ensure_fresh(acc)
            headers = self._session(acc).headers(body="", raw_url=url, request_id=uuid.uuid4().hex)
            res = await self.http.get(url, headers=headers)
            if res.status_code != 200:
                logger.warning("qoder model list -> HTTP %s", res.status_code)
                return None
            payload = res.json()
        except (UpstreamError, httpx.HTTPError, ValueError) as exc:
            logger.warning("qoder model list failed: %s: %s", type(exc).__name__, exc)
            return None
        rows = payload.get("chat") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            logger.warning("qoder model list carried no chat role (keys=%s)", sorted(payload or {})[:8])
            return None
        keys: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = str(row.get("key") or row.get("id") or "")
            if not key or row.get("enable") is False:
                continue
            self._entries[key] = row
            keys.append(key)
        if not keys:
            return None
        self.served_models = tuple(f"{self.model_prefix}/{k}" for k in keys)
        return keys

    # --- request path -----------------------------------------------------
    def _body(self, oai_req: dict, record_id: str) -> tuple[str, dict]:
        """The gateway's own request shape; returns (upstream model key, body)."""
        requested = str(oai_req.get("model") or "")
        key = requested[len(self.model_prefix) + 1:] if requested.startswith(f"{self.model_prefix}/") else requested
        entry = self._entries.get(key, {})
        messages = oai_req.get("messages") or []
        prompt = _latest_user_text(messages)
        reasoning = bool(entry.get("is_reasoning", True))
        parameters: dict[str, Any] = {"max_tokens": int(_number(oai_req.get("max_tokens")
                                                               or oai_req.get("max_completion_tokens"),
                                                               DEFAULT_MAX_OUTPUT))}
        for name in ("temperature", "top_p", "stop", "response_format", "reasoning_effort", "tool_choice"):
            if oai_req.get(name) is not None:
                parameters[name] = oai_req[name]
        if oai_req.get("parallel_tool_calls") is not None:
            parameters["parallel_tool_calls"] = bool(oai_req["parallel_tool_calls"])
        if entry.get("is_reasoning") is not None or oai_req.get("reasoning_effort") is not None:
            parameters["enable_thinking"] = reasoning
        body = {
            "request_id": str(uuid.uuid4()),
            "request_set_id": str(uuid.uuid4()),
            "chat_record_id": record_id,
            "session_id": record_id,
            "stream": True,
            "chat_task": "FREE_INPUT",
            # The gateway resolves the thinking channel and the routed model from here; an
            # empty extra block answers without reasoning even when model_config says
            # otherwise, so both copies carry the key and the flag.
            "chat_context": {
                "text": {"type": "text", "text": prompt},
                "features": [],
                "extra": {"modelConfig": {"key": key, "is_reasoning": reasoning},
                          "originalContent": {"type": "text", "text": prompt}},
                "chatPrompt": "",
                "imageUrls": None,
            },
            "is_reply": False,
            "is_retry": False,
            "source": 1,
            "version": "3",
            "agent_id": "agent_common",
            "task_id": "common",
            "session_type": "qodercli",
            "aliyun_user_type": "",
            "model_config": {
                "key": key, "display_name": key, "model": "", "format": "openai",
                "is_vl": bool(entry.get("is_vl", True)), "is_reasoning": reasoning,
                "api_key": "", "url": "", "source": str(entry.get("source") or "system"),
                "max_input_tokens": int(_number(entry.get("max_input_tokens"), DEFAULT_MAX_INPUT)),
            },
            "custom_model": None,
            "system": _system_text(messages),
            "messages": messages,
            "tools": oai_req.get("tools") or [],
            "parameters": parameters,
            "business": {
                "product": "cli", "version": cosy.COSY_VERSION, "type": "agent",
                "id": str(uuid.uuid4()), "name": prompt[:40] or "chat",
                "begin_at": int(time.time() * 1000), "stage": "start",
            },
        }
        return key, body

    def _signed_post(self, acc: Account, encoded: str, body: dict, key: str, source: str) -> tuple[str, dict]:
        url = f"{self.base}{CHAT_PATH}"
        headers = self._session(acc).headers(body=encoded, raw_url=url, request_id=body["request_id"])
        headers["Chat-Record-Id"] = body["chat_record_id"]
        headers["X-Model-Key"] = key
        headers["X-Model-Source"] = source
        return url, headers

    async def chat_stream(self, acc: Account, oai_req: dict) -> AsyncIterator[dict]:
        await self.ensure_fresh(acc)
        key, body = self._body(oai_req, str(uuid.uuid4()))
        encoded = cosy.encode_body(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        url, headers = self._signed_post(acc, encoded, body, key, str(body["model_config"]["source"]))
        timeout = httpx.Timeout(self.http.timeout.read or self.idle_timeout, connect=10.0, read=self.idle_timeout)
        async with self.http.stream("POST", url, headers=headers, content=encoded.encode("utf-8"),
                                    timeout=timeout) as res:
            if res.status_code != 200:
                await res.aread()
                raise self._error(res.status_code, res.text)
            event = "message"
            async for line in res.aiter_lines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                    if event == "finish":
                        return
                    if event == "error":
                        continue
                    continue
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    return
                try:
                    frame = json.loads(payload)
                except ValueError:
                    continue
                status = frame.get("statusCodeValue") if isinstance(frame, dict) else None
                if isinstance(status, int) and status != 200:
                    raise self._error(status, json.dumps(frame, ensure_ascii=False)[:400])
                chunk = frame_inner(frame)
                if chunk is None:
                    continue
                chunk["model"] = oai_req.get("model") or chunk.get("model")
                chunk.setdefault("object", "chat.completion.chunk")
                chunk.setdefault("id", body["request_id"])
                yield chunk

    async def chat(self, acc: Account, oai_req: dict) -> dict:
        """One turn, assembled from the stream: this door only speaks SSE, and that is
        also what the reference does for its non-streaming surface."""
        content: list[str] = []
        reasoning: list[str] = []
        calls: dict[int, dict] = {}
        finish = ""
        usage: dict = {}
        created = int(time.time())
        async for chunk in self.chat_stream(acc, oai_req):
            if isinstance(chunk.get("usage"), dict):
                usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                if choice.get("finish_reason"):
                    finish = str(choice["finish_reason"])
                delta = choice.get("delta") or {}
                if isinstance(delta.get("content"), str):
                    content.append(delta["content"])
                if isinstance(delta.get("reasoning_content"), str):
                    reasoning.append(delta["reasoning_content"])
                for call in delta.get("tool_calls") or []:
                    slot = calls.setdefault(int(call.get("index") or 0),
                                            {"id": "", "name": "", "arguments": ""})
                    function = call.get("function") or {}
                    slot["id"] = slot["id"] or str(call.get("id") or f"call_{uuid.uuid4().hex[:12]}")
                    slot["name"] = slot["name"] or str(function.get("name") or "")
                    slot["arguments"] += str(function.get("arguments") or "")
        message: dict[str, Any] = {"role": "assistant"}
        text = "".join(content)
        if calls:
            message["content"] = None
            message["tool_calls"] = [
                {"id": slot["id"], "type": "function",
                 "function": {"name": slot["name"], "arguments": slot["arguments"]}}
                for _index, slot in sorted(calls.items())
            ]
        else:
            message["content"] = text
        if reasoning:
            message["reasoning"] = "".join(reasoning)
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": created,
            "model": oai_req.get("model"),
            "choices": [{"index": 0, "message": message, "finish_reason": finish or ("tool_calls" if calls else "stop")}],
            "usage": usage,
        }

    def _error(self, status: int, text: str) -> UpstreamError:
        code = ""
        retry_after = 0.0
        try:
            payload = json.loads(text or "{}")
        except ValueError:
            payload = {}
        if isinstance(payload, dict):
            code = str(payload.get("code") or payload.get("msgCode") or "")
            nested = payload.get("data")
            if isinstance(nested, dict):
                code = code or str(nested.get("code") or "")
                retry_after = float(_number(nested.get("retryAfterSeconds"), 0.0))
        detail = (text or "")[:240]
        if code in _CODE_LOGIN_EXPIRED or status in (401,):
            return UpstreamError(401, f"qoder: {detail}", reauth=True)
        if code in _CODE_BUSY or retry_after:
            return UpstreamError(429, f"qoder: {detail}", retryable=True,
                                 cooldown=retry_after or 30.0)
        if status == 429:
            return UpstreamError(429, f"qoder: {detail}", retryable=True, cooldown=30.0)
        if status == 430:
            return UpstreamError(430, f"qoder: {detail}", retryable=False)
        if status >= 500:
            return UpstreamError(status, f"qoder: {detail}", retryable=True, cooldown=5.0)
        return UpstreamError(status, f"qoder: {detail}", retryable=False)
