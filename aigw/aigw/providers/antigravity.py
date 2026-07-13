"""Antigravity (Google Gemini Code Assist) provider adapter.

Verified protocol (reverse-engineered from Antigravity's own ``language_server.log``
and cross-checked against the reference lucasliet/antigravity-proxy + Google gemini-cli
sources). This is NOT guesswork — every field below is what the real client sends.

Base hosts
----------
  daily : https://daily-cloudcode-pa.googleapis.com   (the app's default / sandbox)
  prod  : https://cloudcode-pa.googleapis.com

Endpoints (api_version ``v1internal``)
--------------------------------------
  POST /v1internal:loadCodeAssist          -> returns ``cloudaicompanionProject`` (project id)
  POST /v1internal:fetchAvailableModels
  POST /v1internal:generateContent
  POST /v1internal:streamGenerateContent?alt=sse

Auth
----
  Bearer OAuth2 access token; refreshed from a Google refresh token via
  oauth2.googleapis.com/token (the refresh token is the one the user obtains through
  the ``aigw auth antigravity`` bootstrap, or via env ANTIGRAVITY_REFRESH_TOKEN).

Request envelope (the "Cloud Code" wrapper the API actually expects)
--------------------------------------------------------------------
  {
    "project":    "<projectId | ''>",
    "model":      "<short model id, e.g. gemini-3-pro>",
    "request":    { "contents":[...], "systemInstruction":{...},
                    "generationConfig":{...}, "tools":[...], "sessionId":"..." },
    "userAgent":  "antigravity",
    "requestType":"agent",
    "requestId":  "agent-<uuid>"
  }

Key headers: Authorization, Content-Type, X-Client-Name, X-Client-Version,
x-goog-api-client (Google validates this), X-Machine-Session-Id, and for streaming
Accept: text/event-stream.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from typing import AsyncIterator, Optional

from .base import Provider, Account, Credential, AccountState, UpstreamError
from ..tokens import desktop_stores as ds


# Antigravity's own client constants (from the reference proxy / gemini-cli).
DEFAULT_HOST_DAILY = "https://daily-cloudcode-pa.googleapis.com"
DEFAULT_HOST_PROD = "https://cloudcode-pa.googleapis.com"

# Headers the real Antigravity client sends. Google rejects the call without a
# believable x-goog-api-client, so we mirror the binary exactly.
ANTIGRAVITY_HEADERS = {
    "X-Client-Name": "antigravity",
    "X-Client-Version": "1.107.0",
    "x-goog-api-client": "gl-node/18.18.2 fire/0.8.6 grpc/1.10.x",
    "User-Agent": "antigravity/1.107.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}

# loadCodeAssist body metadata (ideType 9 = Antigravity, pluginType 2 = Gemini).
CLIENT_METADATA = {
    "ideType": 9,
    "pluginType": 2,
    "platform": 3,  # 3 = macOS in the proxy's platform enum
}


class AntigravityProvider(Provider):
    name = "antigravity"
    served_models = (
        "antigravity/gemini-3-pro",
        "antigravity/gemini-3-flash",
        "antigravity/claude-sonnet-4-6",
    )

    MODEL_MAP = {
        "antigravity/gemini-3-pro": "gemini-3-pro",
        "antigravity/gemini-3-flash": "gemini-3-flash",
        "antigravity/claude-sonnet-4-6": "claude-sonnet-4-6",
    }

    def __init__(self, config, http):
        super().__init__(config, http)
        self.host = config.get("host", DEFAULT_HOST_DAILY).rstrip("/")
        self.api_version = config.get("api_version", "v1internal")
        self.thinking = config.get("thinking", False)
        self.client_metadata = config.get("client_metadata", CLIENT_METADATA)
        self.extra_headers = config.get("extra_headers", {})
        # Allow forcing a project id even if loadCodeAssist cannot discover one.
        self.fallback_project = config.get("project_id", "")

    # --- discovery -------------------------------------------------------
    async def discover_accounts(self) -> list[Account]:
        accs: list[Account] = []
        cid = self.config.get("client_id") or ds.GOOGLE_CC_CLIENT_ID
        csec = self.config.get("client_secret") or ds.GOOGLE_CC_CLIENT_SECRET

        # 1) config-declared accounts (refresh token may be a composite)
        for i, a in enumerate(self.config.get("accounts", [])):
            acc = self._make_account(
                id=a.get("id", f"antigravity-cfg-{i}"),
                label=a.get("label", ""),
                refresh_token=a.get("refresh_token", ""),
                access_token=a.get("access_token", ""),
                project_id=a.get("project_id", ""),
                client_id=a.get("client_id") or cid,
                client_secret=a.get("client_secret") or csec,
            )
            if acc is not None:
                accs.append(acc)

        # 2) env refresh token (the proxy's documented, headless-safe method)
        env_rt = os.environ.get("ANTIGRAVITY_REFRESH_TOKEN", "")
        if env_rt:
            acc = self._make_account(
                id="antigravity-env-refresh", label="env ANTIGRAVITY_REFRESH_TOKEN",
                refresh_token=env_rt, client_id=cid, client_secret=csec)
            if acc is not None:
                accs.append(acc)

        # 3) env captured access token (quick live test without a refresh token)
        env_at = os.environ.get("ANTIGRAVITY_ACCESS_TOKEN", "")
        if env_at:
            accs.append(self._access_only_account("antigravity-env-access", env_at, cid, csec))

        # 4) keychain gemini/antigravity access token (go-keyring blob). Only useful
        #    when a *fresh* token is present; normally the app does not write one back.
        #    Skip it when a refresh token is already available (env/config) — the
        #    keychain token is almost always stale and would just waste a 401 round-trip.
        if not env_rt:
            kc_at = ds.read_antigravity_access_token()
            if kc_at:
                accs.append(self._access_only_account("antigravity-keychain", kc_at, cid, csec))

        # 5) sqlite ~/.antigravity/db.sqlite (compatibility with other setups)
        local = ds.read_antigravity()
        if local and local.get("refresh_token"):
            acc = self._make_account(
                id="antigravity-local", label="local session (db.sqlite)",
                refresh_token=local["refresh_token"], client_id=cid, client_secret=csec)
            if acc is not None:
                accs.append(acc)

        self.accounts = accs
        return accs

    def _make_account(self, *, id, label, refresh_token, access_token="",
                      project_id="", client_id, client_secret) -> Optional[Account]:
        if not refresh_token and not access_token:
            return None
        rt, proj, _ = ds.parse_composite_refresh(refresh_token) if refresh_token else (
            refresh_token, project_id, "")
        cred = Credential(
            access_token=access_token,
            refresh_token=rt,
            extra={
                "client_id": client_id,
                "client_secret": client_secret,
                "project_id": proj or project_id or self.fallback_project,
                "session_id": uuid.uuid4().hex + str(int(time.time())),
            },
        )
        return Account(id=id, provider=self.name, label=label, cred=cred)

    def _access_only_account(self, id, access_token, client_id, client_secret) -> Account:
        return Account(
            id=id, provider=self.name, label=id,
            cred=Credential(
                access_token=access_token,
                extra={
                    "client_id": client_id, "client_secret": client_secret,
                    "project_id": self.fallback_project,
                    "session_id": uuid.uuid4().hex + str(int(time.time())),
                    "no_refresh": True,
                },
            ),
        )

    # --- refresh ---------------------------------------------------------
    async def ensure_fresh(self, acc: Account) -> None:
        cred = acc.cred
        if cred.refresh_token:
            if cred.access_token and not cred.is_expired():
                return
            async with acc.lock:
                if cred.access_token and not cred.is_expired():
                    return
                try:
                    res = await ds.refresh_antigravity(
                        self.http, cred.refresh_token,
                        cred.extra["client_id"], cred.extra.get("client_secret", ""))
                except Exception as e:
                    raise UpstreamError(401, f"antigravity refresh failed: {e}", reauth=True)
                cred.access_token = res["access_token"]
                cred.expires_at = res["expires_at"]
                if not cred.extra.get("project_id") and res.get("project_id"):
                    cred.extra["project_id"] = res["project_id"]
            return
        # access-token-only account: cannot refresh
        if not cred.access_token:
            raise UpstreamError(401, "antigravity: no access token and no refresh token",
                                reauth=True)
        if cred.is_expired() and cred.extra.get("no_refresh"):
            acc.state = AccountState.REAUTH
            raise UpstreamError(401, "antigravity: access token expired and no refresh token",
                                reauth=True)

    # --- project discovery ----------------------------------------------
    async def _project_id(self, acc: Account) -> str:
        if acc.cred.extra.get("project_id"):
            return acc.cred.extra["project_id"]
        pid = await self._load_code_assist(acc)
        if pid:
            acc.cred.extra["project_id"] = pid
            return pid
        return self.fallback_project

    async def _load_code_assist(self, acc: Account) -> str:
        url = f"{self.host}/{self.api_version}:loadCodeAssist"
        body = {"metadata": self.client_metadata, "mode": 1}
        try:
            r = await self.http.post(url, headers=self._headers(acc.cred.access_token),
                                     json=body, timeout=20.0)
        except Exception:
            return ""
        if r.status_code != 200:
            return ""
        try:
            data = r.json()
        except Exception:
            return ""
        proj = data.get("cloudaicompanionProject")
        if isinstance(proj, dict):
            proj = proj.get("id", "")
        return proj or ""

    # --- request path ----------------------------------------------------
    def _url(self, stream: bool) -> str:
        if stream:
            return f"{self.host}/{self.api_version}:streamGenerateContent?alt=sse"
        return f"{self.host}/{self.api_version}:generateContent"

    def _headers(self, token: str, session_id: str = "", model: str = "",
                 accept: str = "application/json") -> dict:
        h = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": accept,
            **ANTIGRAVITY_HEADERS,
        }
        h.update(self.extra_headers)
        if session_id:
            h["X-Machine-Session-Id"] = session_id
        # Claude thinking models need this beta header
        low = model.lower()
        if "claude" in low and "thinking" in low:
            h["anthropic-beta"] = "interleaved-thinking-2025-05-14"
        return h

    def _short_model(self, oai_model: str) -> str:
        return self.MODEL_MAP.get(oai_model, oai_model.split("/")[-1])

    def _to_gemini(self, oai_req: dict, session_id: str) -> dict:
        contents, system_txt = [], []
        for m in oai_req["messages"]:
            role = m["role"]
            if role == "system":
                system_txt.append(_as_text(m.get("content")))
                continue
            if role == "tool":
                # OpenAI tool result -> Gemini functionResponse (user turn)
                contents.append({
                    "role": "user",
                    "parts": [{"functionResponse": {
                        "name": m.get("name", ""),
                        "response": {"result": _as_text(m.get("content"))},
                    }}],
                })
                continue
            parts = []
            if role == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    fn = tc.get("function", {})
                    raw = fn.get("arguments", "{}")
                    try:
                        args = json.loads(raw) if isinstance(raw, str) else (raw or {})
                    except Exception:
                        args = {}
                    parts.append({"functionCall": {
                        "name": fn.get("name", ""), "args": args or {}}})
            else:
                text = _as_text(m.get("content"))
                if text:
                    parts.append({"text": text})
            if parts:
                grole = "model" if role == "assistant" else "user"
                contents.append({"role": grole, "parts": parts})

        body = {"contents": contents}
        if system_txt:
            # the real client sends systemInstruction with role:"user"
            body["systemInstruction"] = {
                "role": "user",
                "parts": [{"text": "\n".join(system_txt)}],
            }
        gen = {}
        if "temperature" in oai_req:
            gen["temperature"] = oai_req["temperature"]
        if "max_tokens" in oai_req:
            gen["maxOutputTokens"] = oai_req["max_tokens"]
        if self.thinking or oai_req.get("reasoning_effort"):
            budget = {"minimal": 1024, "low": 4096, "medium": 8192,
                      "high": 16384}.get(oai_req.get("reasoning_effort"), 8192)
            gen["thinkingConfig"] = {"thinkingBudget": budget,
                                     "includeThoughts": bool(self.thinking)}
        if gen:
            body["generationConfig"] = gen
        tools = _to_gemini_tools(oai_req.get("tools"))
        if tools:
            body["tools"] = tools
        body["sessionId"] = session_id
        return body

    def _wrap(self, oai_req: dict, project: str, model: str, session_id: str) -> dict:
        inner = self._to_gemini(oai_req, session_id)
        return {
            "project": project,
            "model": model,
            "request": inner,
            "userAgent": "antigravity",
            "requestType": "agent",
            "requestId": "agent-" + uuid.uuid4().hex,
        }

    async def chat(self, acc: Account, oai_req: dict) -> dict:
        project = await self._project_id(acc)
        sid = acc.cred.extra.get("session_id", "")
        model = self._short_model(oai_req["model"])
        payload = self._wrap(oai_req, project, model, sid)
        r = await self.http.post(self._url(False),
                                 headers=self._headers(acc.cred.access_token, sid, model),
                                 json=payload, timeout=120.0)
        err = self._classify_http(r.status_code)
        if err:
            self._note_quota(acc, r)
            raise err
        try:
            data = r.json()
        except Exception as e:
            raise UpstreamError(502, f"antigravity: invalid JSON response ({e})")
        # The API wraps the Gemini payload in a top-level "response" object.
        data = data.get("response", data)
        return _gemini_to_oai(data, oai_req["model"], project)

    async def chat_stream(self, acc: Account, oai_req: dict) -> AsyncIterator[dict]:
        project = await self._project_id(acc)
        sid = acc.cred.extra.get("session_id", "")
        model = self._short_model(oai_req["model"])
        payload = self._wrap(oai_req, project, model, sid)
        cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        async with self.http.stream("POST", self._url(True),
                                    headers=self._headers(acc.cred.access_token, sid, model,
                                                          accept="text/event-stream"),
                                    json=payload, timeout=120.0) as r:
            err = self._classify_http(r.status_code)
            if err:
                self._note_quota(acc, r)
                raise err
            async for line in r.aiter_lines():
                line = line.strip()
                if not line or not line.startswith("data:"):
                    continue
                payload_s = line[5:].strip()
                if not payload_s or payload_s == "[DONE]":
                    continue
                try:
                    obj = json.loads(payload_s)
                except json.JSONDecodeError:
                    continue
                # The API wraps the Gemini payload in a top-level "response" object.
                obj = obj.get("response", obj)
                text = _extract_gemini_text(obj)
                if text:
                    yield _chunk(cid, oai_req["model"], text)
                fn = _extract_gemini_function_call(obj)
                if fn:
                    yield _tool_chunk(cid, oai_req["model"], fn)
            yield _chunk(cid, oai_req["model"], None, finish="stop")

    def _note_quota(self, acc, resp):
        rem = resp.headers.get("x-ratelimit-remaining")
        if rem is not None:
            acc.quota["remaining"] = rem


# --- translation helpers ---------------------------------------------------
def _as_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return str(content)


def _to_gemini_tools(tools):
    if not tools:
        return None
    decls = []
    for t in tools:
        if not isinstance(t, dict) or t.get("type") != "function":
            continue
        fn = t.get("function", {})
        decls.append({
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters", {}),
        })
    return [{"functionDeclarations": decls}] if decls else None


def _extract_gemini_text(obj: dict) -> str:
    try:
        return "".join(p.get("text", "")
                       for p in obj["candidates"][0]["content"]["parts"]
                       if "text" in p)
    except (KeyError, IndexError, TypeError):
        return ""


def _extract_gemini_function_call(obj: dict):
    try:
        for p in obj["candidates"][0]["content"]["parts"]:
            if "functionCall" in p:
                return p["functionCall"]
    except (KeyError, IndexError, TypeError):
        return None
    return None


def _gemini_to_oai(obj: dict, model: str, project: str) -> dict:
    text_parts = []
    tool_calls = []
    try:
        for p in obj["candidates"][0]["content"]["parts"]:
            if "text" in p:
                text_parts.append(p["text"])
            elif "functionCall" in p:
                fc = p["functionCall"]
                tool_calls.append({
                    "id": "call_" + uuid.uuid4().hex[:8],
                    "type": "function",
                    "function": {
                        "name": fc.get("name", ""),
                        "arguments": json.dumps(fc.get("args", {}), ensure_ascii=False),
                    },
                })
    except (KeyError, IndexError, TypeError):
        pass
    usage = obj.get("usageMetadata", {})
    msg = {"role": "assistant",
           "content": "".join(text_parts) if text_parts else None}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "finish_reason": "tool_calls" if tool_calls else "stop",
            "message": msg,
        }],
        "usage": {
            "prompt_tokens": usage.get("promptTokenCount", 0),
            "completion_tokens": usage.get("candidatesTokenCount", 0),
            "total_tokens": usage.get("totalTokenCount", 0),
        },
    }


def _chunk(cid: str, model: str, text, finish=None) -> dict:
    delta = {} if text is None else {"content": text}
    return {
        "id": cid, "object": "chat.completion.chunk",
        "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def _tool_chunk(cid: str, model: str, fn: dict) -> dict:
    return {
        "id": cid, "object": "chat.completion.chunk",
        "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "delta": {"tool_calls": [{
            "index": 0,
            "id": "call_" + uuid.uuid4().hex[:8],
            "type": "function",
            "function": {
                "name": fn.get("name", ""),
                "arguments": json.dumps(fn.get("args", {}), ensure_ascii=False),
            },
        }]}, "finish_reason": None}],
    }
