"""Gemini (Google account via the Gemini CLI's own OAuth) as an OpenAI-shaped leg.

The shape is the one that already works for CodeBuddy and Antigravity (裁定 204): read the
product's **own** local credential, call the vendor's documented HTTP API, and translate
the answer into OpenAI `tool_calls` — the only shape L1 can drive.

Measured 2026-10-28 on this machine: `gemini --acp` answers
`Gemini API key is missing or not configured`, and `~/.gemini/` holds `config/`,
`history/`, `installation_id`, `projects.json` — **no credential file**. So this leg is
dark until the owner signs the CLI in once (`gemini` → its own browser/device flow). The
reader below is deliberately tolerant about the file's key names because the shape can
only be read *after* that sign-in; it accepts the documented Google OAuth names
(`access_token` / `refresh_token` / `expiry_date`) and the short forms, and treats
anything it cannot parse as "no credential" — never as "signed in".

**No TUN needed (裁定 206 follow-up, user 2026-10-28)**: the hub runs with
`trust_env: false` (2026-10-14: a dead local proxy poisoned every upstream), so this leg
cannot lean on `HTTP(S)_PROXY` either — and Google is unreachable directly on this network.
So the leg takes an **explicit per-leg proxy** (`providers.gemini_api.proxy`, or
`QUOTA_HUB_GEMINI_PROXY`), used only by this leg's client. A dead proxy then breaks one
leg with a clear error instead of every upstream at once, and the rest of the hub stays
direct. Nothing else about the machine (no TUN, no system-wide proxy) has to change.

The translation is the part the ACP legs cannot give:

    Gemini `functionCall {name, args}`  →  OpenAI `tool_calls[{id, function{name, arguments}}]`

Ids are synthesised (`call_<n>`) because Google's shape carries none; arguments are
re-serialised to JSON text, which is what an OpenAI client expects back.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx

from ....providers.base import Account, Capabilities, Credential, Provider, UpstreamError

log = logging.getLogger("quota_hub")

STORE_ENV = "QUOTA_HUB_GEMINI_STORE"
BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
#: (There is no default model list on purpose: `/models` is the only name source.)


def store_path() -> Path:
    explicit = os.environ.get(STORE_ENV, "").strip()
    if explicit:
        return Path(explicit)
    return Path.home() / ".gemini" / "oauth_creds.json"


def read_credential() -> dict[str, Any] | None:
    path = store_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("gemini credential file unreadable (%s); treating it as absent", type(exc).__name__)
        return None
    if not isinstance(data, dict):
        return None
    token = str(data.get("access_token") or data.get("access") or "").strip()
    if not token:
        return None
    # `expiry_date` is milliseconds since the epoch (the CLI's own field); accept
    # seconds too so a hand-written file does not read as expired forever.
    raw = data.get("expiry_date") or data.get("expires_at") or data.get("expires") or 0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 0.0
    expires_at = value / 1000.0 if value > 1e11 else value
    return {
        "access_token": token,
        "refresh_token": str(data.get("refresh_token") or ""),
        "expires_at": expires_at,
    }


def _functions(tools: Any) -> list[dict[str, Any]]:
    """OpenAI `tools` → Gemini `functionDeclarations` (parameters are JSON Schema too)."""
    declarations: list[dict[str, Any]] = []
    for tool in tools or []:
        fn = (tool or {}).get("function") or {}
        name = str(fn.get("name") or "").strip()
        if not name:
            continue
        decl: dict[str, Any] = {"name": name}
        if fn.get("description"):
            decl["description"] = str(fn["description"])
        params = fn.get("parameters")
        if isinstance(params, dict) and params:
            decl["parameters"] = params
        declarations.append(decl)
    return declarations


def _to_openai(model: str, body: dict[str, Any]) -> dict[str, Any]:
    """Gemini `generateContent` answer → OpenAI chat.completion."""
    candidates = body.get("candidates") or [{}]
    candidate = candidates[0] if isinstance(candidates[0], dict) else {}
    parts = ((candidate.get("content") or {}).get("parts")) or []
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for index, part in enumerate(parts):
        if not isinstance(part, dict):
            continue
        if "text" in part:
            text_parts.append(str(part["text"]))
        call = part.get("functionCall")
        if isinstance(call, dict) and call.get("name"):
            args = call.get("args")
            tool_calls.append(
                {
                    "id": f"call_{index}_{call['name']}",
                    "type": "function",
                    "function": {
                        "name": str(call["name"]),
                        "arguments": json.dumps(args or {}, ensure_ascii=False),
                    },
                }
            )
    finish = "tool_calls" if tool_calls else str(candidate.get("finishReason") or "stop").lower()
    if finish not in {"stop", "tool_calls", "length", "content_filter"}:
        finish = "stop"
    message: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts) or (None if tool_calls else "")}
    if tool_calls:
        message["tool_calls"] = tool_calls
    usage = body.get("usageMetadata") or {}
    return {
        "id": f"gemini-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "finish_reason": finish, "message": message}],
        "usage": {
            "prompt_tokens": int(usage.get("promptTokenCount") or 0),
            "completion_tokens": int(usage.get("candidatesTokenCount") or 0),
            "total_tokens": int(usage.get("totalTokenCount") or 0),
        },
    }


def _contents(messages: Any) -> list[dict[str, Any]]:
    contents: list[dict[str, Any]] = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "user")
        text = msg.get("content")
        if not isinstance(text, str):
            text = "" if text is None else json.dumps(text, ensure_ascii=False)
        if not text.strip():
            continue
        gemini_role = "model" if role == "assistant" else "user"
        contents.append({"role": gemini_role, "parts": [{"text": text}]})
    return contents


def egress_proxy() -> tuple[str, str]:
    """The node the user configured in Settings → VPN, as an HTTP proxy URL.

    The VPN chip already owns a node pool and runs a Clash-style engine; its
    `mixed-port` is the one address every client in Plobi can share (measured
    2026-10-28: `PLOBI_HOME/egress/config.yaml`, `mixed-port: 7895`). So a leg does
    not need its own proxy string — it needs to know whether that port is alive.
    """
    home = os.environ.get("PLOBI_HOME", "").strip()
    if not home:
        return "", "no_proxy"
    cfg = Path(home) / "egress" / "config.yaml"
    if not cfg.is_file():
        return "", "no_proxy"
    port = 0
    try:
        for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            key, _, value = line.partition(":")
            if key.strip() in {"mixed-port", "port", "socks-port"} and value.strip().isdigit():
                port = int(value.strip())
                break
    except OSError:
        return "", "no_proxy"
    if not port:
        return "", "no_proxy"
    import socket

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            pass
    except OSError:
        return "", "proxy_unreachable"
    return f"http://127.0.0.1:{port}", "ok"

class GeminiApiProvider(Provider):
    name = "gemini_api"
    display_name = "Gemini CLI"
    default_capabilities = Capabilities(stream=True, tools=True)

    def __init__(self, config: dict, http) -> None:
        super().__init__(config, http)
        self.base_url = str(config.get("base_url") or BASE_URL).rstrip("/")
        self.timeout = float(config.get("timeout", 180.0))
        self.served_models: tuple[str, ...] = tuple(config.get("models") or ())
        # Per-leg proxy: only Google traffic goes through it (see the module header).
        explicit = str(config.get("proxy") or os.environ.get("QUOTA_HUB_GEMINI_PROXY") or "").strip()
        self.proxy_reason = "ok"
        if explicit:
            self.proxy = explicit
        else:
            # 裁定 206：设置里那个 VPN 节点就是全 Plobi 的出口，腿不用再单独填一次。
            shared, self.proxy_reason = egress_proxy()
            self.proxy = shared
        self._proxied = None
        if self.proxy:
            self._proxied = httpx.AsyncClient(proxy=self.proxy, timeout=httpx.Timeout(self.timeout, connect=15.0))
            log.info("[gemini_api] using an explicit proxy for this leg only")

    # --- presence / credential -------------------------------------------
    def present(self) -> bool:
        return read_credential() is not None

    def login_plan(self) -> dict:
        return {
            "kind": "cli_login",
            "binary": self.config.get("binary", "gemini"),
            "argv": list(self.config.get("login_args", ["--version"])),
            "timeout": float(self.config.get("login_timeout", 300.0)),
            "name": self.display_name,
        }

    def credential_health(self) -> tuple[bool, str]:
        data = read_credential()
        if data is None:
            return False, "no_desktop_credential"
        # Credential first (it is the step a fresh install has to take), node second:
        # once a sign-in exists, "no node / node down" is the reason a turn cannot run.
        if self.proxy_reason != "ok":
            return False, self.proxy_reason
        expires = float(data.get("expires_at") or 0.0)
        if expires and expires <= time.time():
            return False, "credential_expired"
        return True, "ok"

    # --- accounts / models ------------------------------------------------
    async def _list_models(self, token: str) -> list[str]:
        try:
            client = self._proxied or self.http
            res = await client.get(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {token}"},
                timeout=self.timeout,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("[gemini_api] model list unreachable: %s", type(exc).__name__)
            return []
        if res.status_code != 200:
            return []
        try:
            body = res.json()
        except ValueError:
            return []
        ids: list[str] = []
        for row in body.get("models") or []:
            name = str((row or {}).get("name") or "")
            if not name.startswith("models/"):
                continue
            methods = row.get("supportedGenerationMethods") or []
            if "generateContent" not in methods:
                continue
            ids.append(f"{self.name}/{name.split('/', 1)[1]}")
        return ids

    async def discover_accounts(self) -> list[Account]:
        live, _reason = self.credential_health()
        data = read_credential()
        if not live or data is None:
            self.accounts = []
            self.served_models = ()
            return []
        token = str(data.get("access_token") or "")
        self.accounts = [
            Account(
                id=f"{self.name}-default",
                provider=self.name,
                label=self.display_name,
                cred=Credential(
                    access_token=token,
                    refresh_token=str(data.get("refresh_token") or ""),
                    expires_at=float(data.get("expires_at") or 0.0),
                ),
            )
        ]
        # 裁定 204.5：名字只认上游自报。`/models` 读不到就是没有模型，不拿默认表顶替。
        self.served_models = tuple(await self._list_models(token))
        return self.accounts

    async def ensure_fresh(self, acc: Account) -> None:
        if acc.cred.is_expired():
            raise UpstreamError(401, "gemini credential expired — sign in again with `gemini`", reauth=True)

    # --- the turn ----------------------------------------------------------
    def _payload(self, oai_req: dict) -> dict[str, Any]:
        payload: dict[str, Any] = {"contents": _contents(oai_req.get("messages"))}
        declarations = _functions(oai_req.get("tools"))
        if declarations:
            payload["tools"] = [{"functionDeclarations": declarations}]
        generation: dict[str, Any] = {}
        if oai_req.get("temperature") is not None:
            generation["temperature"] = oai_req["temperature"]
        if oai_req.get("max_tokens"):
            generation["maxOutputTokens"] = oai_req["max_tokens"]
        if generation:
            payload["generationConfig"] = generation
        return payload

    async def chat(self, acc: Account, oai_req: dict) -> dict:
        model = str(oai_req.get("model") or "")
        bare = model.split("/", 1)[1] if "/" in model else model
        client = self._proxied or self.http
        res = await client.post(
            f"{self.base_url}/models/{bare}:generateContent",
            headers={"Authorization": f"Bearer {acc.cred.access_token}", "content-type": "application/json"},
            json=self._payload(oai_req),
            timeout=self.timeout,
        )
        if res.status_code != 200:
            raise UpstreamError(res.status_code, res.text[:400])
        try:
            return _to_openai(model, res.json())
        except ValueError as exc:
            raise UpstreamError(502, f"gemini answered a non-JSON body: {exc}") from exc

    async def chat_stream(self, acc: Account, oai_req: dict) -> AsyncIterator[dict]:
        # v1 folds the answer into one chunk (SSE proxying needs its own tests); the
        # turn shape is what matters, and `tool_calls` survive the fold.
        result = await self.chat(acc, oai_req)
        choice = (result.get("choices") or [{}])[0]
        yield {
            "id": result["id"],
            "object": "chat.completion.chunk",
            "created": result["created"],
            "model": result["model"],
            "choices": [{"index": 0, "delta": choice.get("message") or {}, "finish_reason": choice.get("finish_reason")}],
        }
