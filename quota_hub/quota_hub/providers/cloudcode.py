"""The Google Cloud Code Assist envelope, as ONE implementation.

Why a module instead of two copies: measured on this machine 2026-10-28, the Gemini CLI's
own `oauth-personal` path does not talk to `generativelanguage.googleapis.com` at all — its
bundle says `CODE_ASSIST_ENDPOINT = "https://cloudcode-pa.googleapis.com"` with
`CODE_ASSIST_API_VERSION = "v1internal"` (chunk-DKZARV6U.js), which is the same backend the
Antigravity leg already drives. A Google-account token therefore has ONE door, and every
leg that holds such a token should go through it. Duplicating the envelope would be two
places to get a reverse-engineered format wrong — and the Antigravity leg's history in this
repo (the `data["response"]` unwrap that used to sit after a `raise`) is what that costs.

What stays per-leg (and it is all of the difference): which OAuth client pair, where the
credential lives, the host variant, and the client metadata / headers the product sends.
Those are supplied by the caller; nothing here guesses a product's identity.

Translation direction is the usual one: OpenAI in → Gemini/Code Assist out, and
`functionCall` back out as OpenAI `tool_calls` — the only shape an agent loop can drive, and
the reason a leg is pickable at all.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any, Callable

logger = logging.getLogger(__name__)

from .cloudcode_wire import (
    _describe_google_error,
    _as_text,
    _to_gemini_tools,
    _extract_gemini_text,
    _extract_gemini_function_call,
    _gemini_to_oai,
    _chunk,
    _tool_chunk,
    _parse_antigravity_models,
)
from .base import UpstreamError

API_VERSION = "v1internal"

#: The thinking budget each Plobi effort word means for a model that thinks in budgets
#: rather than in named tiers (Gemini / GPT-OSS on the Code Assist door). One table, two
#: readers: `to_gemini` sends it, and the Antigravity leg names it back to the picker.
THINKING_BUDGET_BY_EFFORT = {"minimal": 1024, "low": 4096, "medium": 8192, "high": 16384}


class CloudCodeClient:
    """One Code Assist transport, parameterised by the product that owns the token."""

    def __init__(
        self,
        http,
        *,
        host: str,
        headers: dict[str, str],
        client_metadata: dict[str, Any],
        user_agent: str = "antigravity",
        request_type: str = "agent",
        api_version: str = API_VERSION,
        extra_headers: dict[str, str] | None = None,
        thinking: bool = False,
        timeout: float = 120.0,
        label: str = "cloudcode",
    ) -> None:
        self.http = http
        self.host = host.rstrip("/")
        self.api_version = api_version
        self.headers = dict(headers)
        self.client_metadata = dict(client_metadata)
        self.user_agent = user_agent
        self.request_type = request_type
        self.extra_headers = dict(extra_headers or {})
        self.thinking = thinking
        self.timeout = timeout
        self.label = label
        #: Last thought_signature seen on an upstream functionCall part; the next
        #: turn forwards it on its own functionCall parts (Code Assist rejects
        #: calls without one — measured 2026-11-02).
        self._last_thought_signature: str | None = None

    # --- endpoints --------------------------------------------------------
    def url(self, stream: bool) -> str:
        if stream:
            return f"{self.host}/{self.api_version}:streamGenerateContent?alt=sse"
        return f"{self.host}/{self.api_version}:generateContent"

    def headers_for(self, token: str, session_id: str = "", model: str = "", accept: str = "application/json") -> dict:
        h = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": accept,
            **self.headers,
        }
        h.update(self.extra_headers)
        if session_id:
            h["X-Machine-Session-Id"] = session_id
        low = (model or "").lower()
        if "claude" in low and "thinking" in low:
            h["anthropic-beta"] = "interleaved-thinking-2025-05-14"
        return h

    # --- discovery --------------------------------------------------------
    async def load_project(self, token: str, session_id: str = "") -> str:
        url = f"{self.host}/{self.api_version}:loadCodeAssist"
        try:
            r = await self.http.post(
                url, headers=self.headers_for(token, session_id), json={"metadata": self.client_metadata, "mode": 1}, timeout=20.0
            )
        except Exception:
            return ""
        if r.status_code != 200:
            return ""
        try:
            data = r.json()
        except Exception:
            return ""
        proj = data.get("cloudaicompanionProject") if isinstance(data, dict) else None
        if isinstance(proj, dict):
            proj = proj.get("id", "")
        return proj or ""

    async def fetch_models_payload(self, token: str, project: str = "") -> dict:
        """One `fetchAvailableModels` round trip, answered as upstream wrote it.

        The legs need this shape (not just the names) because each model's own row is
        the only honest source for what a picker may offer: measured 2026-11-01,
        `claude-opus-5-5-high` reports `maxTokens: 1000000` / `maxOutputTokens: 128000`,
        `gpt-oss-120b-medium` reports 131072 / 32768, and every row says for itself
        whether it thinks (`supportsThinking`, `minThinkingBudget`).
        """
        url = f"{self.host}/{self.api_version}:fetchAvailableModels"
        body = {"project": project} if project else {}
        try:
            r = await self.http.post(url, headers=self.headers_for(token), json=body, timeout=20.0)
        except Exception as e:
            raise UpstreamError(502, f"{self.label}: fetchAvailableModels transport {type(e).__name__}: {e}")
        if r.status_code == 401:
            raise UpstreamError(401, f"{self.label}: fetchAvailableModels unauthorized", reauth=True)
        if r.status_code != 200:
            raise UpstreamError(r.status_code, f"{self.label}: fetchAvailableModels returned {r.status_code}: {(r.text or '')[:400]}")
        try:
            data = r.json()
        except Exception as e:
            raise UpstreamError(502, f"{self.label}: fetchAvailableModels invalid JSON {type(e).__name__}: {e}")
        return data if isinstance(data, dict) else {}

    async def available_models(self, token: str, project: str = "") -> list[str]:
        """The model list the account is actually entitled to — our ONLY name source."""
        return _parse_antigravity_models(await self.fetch_models_payload(token, project))

    # --- the turn ---------------------------------------------------------
    def to_gemini(self, oai_req: dict, session_id: str, model: str = "") -> dict:
        # Code Assist enforces a strict turn grammar: a functionCall turn must come
        # immediately after a user turn or after a function response turn, and every
        # functionCall part must carry the thought_signature of the preceding thought
        # turn (measured 2026-11-03 on a 78-msg / 46k-token context and 2026-11-02 on
        # an unsigned call). We therefore pair every tool round here:
        #   - a functionCall turn is only emitted when a functionResponse turn follows;
        #   - an orphan call or an orphan response (long-context trim dropped its mate)
        #     is dropped instead of being sent into a 400.
        contents: list[dict[str, Any]] = []
        system_txt: list[str] = []
        tc_names: dict[str, str] = {}
        pending_call: int | None = None
        for m in oai_req["messages"]:
            role = m["role"]
            if role == "system":
                system_txt.append(_as_text(m.get("content")))
                continue
            if role == "tool":
                fn_name = (
                    m.get("name")
                    or tc_names.get(str(m.get("tool_call_id") or ""), "")
                    or "tool_result"
                )
                part = {
                    "functionResponse": {
                        "name": fn_name,
                        "response": {"result": _as_text(m.get("content"))},
                    }
                }
                open_response_batch = (
                    contents
                    and contents[-1]["role"] == "user"
                    and all("functionResponse" in p for p in contents[-1]["parts"])
                )
                if pending_call is not None:
                    # The functionCall whose turn we are answering is right behind
                    # us — the grammar is satisfied.
                    pending_call = None
                elif not open_response_batch:
                    # No pending call and no open functionResponse batch: this tool
                    # result arrived without its call (long-context trim). Dropping
                    # it is the only shape that does not violate the grammar.
                    logger.warning(
                        "%s: dropping an orphan functionResponse (%s) — no preceding functionCall",
                        self.label,
                        fn_name,
                    )
                    continue
                if open_response_batch:
                    contents[-1]["parts"].append(part)
                else:
                    contents.append({"role": "user", "parts": [part]})
                continue
            parts = []
            if role == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    fn = tc.get("function", {})
                    tc_id = str(tc.get("id") or "")
                    fn_name = str(fn.get("name") or "")
                    if tc_id and fn_name:
                        tc_names[tc_id] = fn_name
                    raw = fn.get("arguments", "{}")
                    try:
                        args = json.loads(raw) if isinstance(raw, str) else (raw or {})
                    except Exception:
                        args = {}
                    call_part: dict[str, Any] = {"functionCall": {"name": fn_name, "args": args or {}}}
                    # The upstream requires a thought_signature on the part. Prefer
                    # one the caller forwarded on the message; fall back to the last
                    # signature we captured from an upstream functionCall; otherwise
                    # drop the call (an unsigned call is rejected with a 400 anyway).
                    sig = (
                        str(m.get("thought_signature") or "")
                        or (self._last_thought_signature or "")
                    )
                    if sig:
                        call_part["thought_signature"] = sig
                        parts.append(call_part)
                    else:
                        logger.warning(
                            "%s: dropping an unsigned functionCall (%s) — no thought_signature to forward",
                            self.label,
                            fn_name,
                        )
            else:
                text = _as_text(m.get("content"))
                if text:
                    parts.append({"text": text})
            if not parts:
                continue
            # A plain assistant turn arriving while a functionCall is still unpaired
            # means the functionResponse turn was lost; drop the orphan call so no
            # functionCall turn is left dangling.
            if pending_call is not None:
                dropped = contents.pop(pending_call)
                logger.warning(
                    "%s: dropping %d orphan functionCall part(s) — the functionResponse turn is missing (long-context trim?)",
                    self.label,
                    len(dropped.get("parts", [])),
                )
                pending_call = None
            contents.append({"role": "model" if role == "assistant" else "user", "parts": parts})
            if role == "assistant" and any("functionCall" in p for p in parts):
                pending_call = len(contents) - 1
        if pending_call is not None:
            dropped = contents.pop(pending_call)
            logger.warning(
                "%s: dropping %d trailing orphan functionCall part(s) at end of turn",
                self.label,
                len(dropped.get("parts", [])),
            )

        body: dict[str, Any] = {"contents": contents}
        if system_txt:
            body["systemInstruction"] = {"role": "user", "parts": [{"text": "\n".join(system_txt)}]}
        gen: dict[str, Any] = {}
        if "temperature" in oai_req:
            gen["temperature"] = oai_req["temperature"]
        if "max_tokens" in oai_req:
            gen["maxOutputTokens"] = oai_req["max_tokens"]
        low_model = (model or str(oai_req.get("model") or "")).lower().split("/")[-1]
        fixed_tier = (
            low_model.endswith(("-low", "-medium", "-high", "-extra-low", "-lite"))
            or "claude" in low_model
            or "gpt-oss" in low_model
        )
        if self.thinking or (oai_req.get("reasoning_effort") and not fixed_tier):
            budget = THINKING_BUDGET_BY_EFFORT.get(oai_req.get("reasoning_effort"), 8192)
            gen["thinkingConfig"] = {"thinkingBudget": budget, "includeThoughts": bool(self.thinking)}
        if gen:
            body["generationConfig"] = gen
        tools = _to_gemini_tools(oai_req.get("tools"))
        if tools:
            body["tools"] = tools
        body["sessionId"] = session_id
        return body

    def wrap(self, oai_req: dict, project: str, model: str, session_id: str) -> dict:
        return {
            "project": project,
            "model": model,
            "request": self.to_gemini(oai_req, session_id, model),
            "userAgent": self.user_agent,
            "requestType": self.request_type,
            "requestId": self.request_type + "-" + uuid.uuid4().hex,
        }

    def _classify(self, status: int, body_text: str = "") -> UpstreamError | None:
        if status in (200, 201):
            return None
        reauth = status == 401
        detail = ""
        if body_text:
            try:
                parsed = json.loads(body_text)
                if isinstance(parsed, dict) and "error" in parsed:
                    detail = f": {_describe_google_error(parsed['error'])}"
            except Exception:
                detail = f": {body_text.strip()[:240]}"
        return UpstreamError(
            status,
            f"{self.label}: upstream returned {status}{detail}",
            reauth=reauth,
            retryable=status in (429, 500, 502, 503),
        )

    async def chat(self, *, token: str, project: str, model: str, oai_req: dict, session_id: str = "",
                   on_response: Callable[[Any], None] | None = None) -> dict:
        payload = self.wrap(oai_req, project, model, session_id)
        r = await self.http.post(
            self.url(False), headers=self.headers_for(token, session_id, model), json=payload, timeout=self.timeout
        )
        # The leg may still want to read the response itself (rate-limit headers, quota
        # notes) without knowing anything about the envelope.
        if on_response is not None:
            on_response(r)
        err = self._classify(r.status_code, getattr(r, "text", "") or "")
        if err:
            raise err
        try:
            data = r.json()
        except Exception as e:
            raise UpstreamError(502, f"{self.label}: invalid JSON response ({e})")
        if not isinstance(data, dict):
            raise UpstreamError(502, f"{self.label}: upstream returned a non-object payload")
        if "error" in data:
            raise UpstreamError(502, f"{self.label} upstream: {_describe_google_error(data['error'])}", retryable=False)
        # Code Assist wraps the Gemini payload in a top-level "response" object.
        return _gemini_to_oai(data.get("response", data), oai_req["model"], project)

    async def chat_stream(self, *, token: str, project: str, model: str, oai_req: dict, session_id: str = "",
                          on_response: Callable[[Any], None] | None = None) -> AsyncIterator[dict]:
        payload = self.wrap(oai_req, project, model, session_id)
        cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        async with self.http.stream(
            "POST",
            self.url(True),
            headers=self.headers_for(token, session_id, model, accept="text/event-stream"),
            json=payload,
            timeout=self.timeout,
        ) as r:
            if on_response is not None:
                on_response(r)
            if r.status_code not in (200, 201):
                raw_err = ""
                if hasattr(r, "aread"):
                    try:
                        raw_err = (await r.aread()).decode("utf-8", "replace")
                    except Exception:
                        raw_err = ""
                err = self._classify(r.status_code, raw_err)
                if err:
                    raise err
            async for line in r.aiter_lines():
                line = line.strip()
                if line.startswith("data:") and "[DONE]" not in line:
                    try:
                        probe = json.loads(line[5:].strip())
                        if isinstance(probe, dict) and "error" in probe:
                            raise UpstreamError(502, f"{self.label} upstream: {_describe_google_error(probe['error'])}", retryable=False)
                    except (json.JSONDecodeError, UpstreamError):
                        pass
                if not line or not line.startswith("data:"):
                    continue
                payload_s = line[5:].strip()
                if not payload_s or payload_s == "[DONE]":
                    continue
                try:
                    obj = json.loads(payload_s)
                except json.JSONDecodeError:
                    continue
                obj = obj.get("response", obj)
                text = _extract_gemini_text(obj)
                if text:
                    yield _chunk(cid, oai_req["model"], text)
                fn = _extract_gemini_function_call(obj)
                if fn:
                    sig = self._extract_thought_signature(obj)
                    if sig:
                        self._last_thought_signature = sig
                    yield _tool_chunk(cid, oai_req["model"], fn)
            yield _chunk(cid, oai_req["model"], None, finish="stop")
