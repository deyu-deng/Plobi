"""Workbuddy provider adapter (generic, config-driven API passthrough).

LEGACY / NON-COMPLIANT ROUTE. Workbuddy's internal API is not publicly
documented. Rather than hardcode guesses, this adapter is a configurable
passthrough: you capture the real endpoint + auth with tools/mitm_discover.py,
drop the details into config.yaml, and pick a `dialect`:

Prefer the compliant routes instead:
  - workbuddy_cli  -> spawn a Workbuddy CLI subprocess (no token scraping)
  - workbuddy_gui  -> Windows UI Automation of the desktop app
  - workbuddy      -> hybrid: prefer CLI, fall back to GUI (WorkbuddyHybridProvider)

This `workbuddy_api` adapter stays available for builds that DO expose a clean
HTTP API you can legitimately call with your own key.

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
from collections.abc import AsyncIterator
from typing import Any

from ...base import Account, Capabilities, Credential, Provider, UpstreamError
from ....tokens import desktop_stores as ds

# Refresh this early, so a request never starts on a token that dies mid-flight.
TOKEN_SKEW_SECONDS = 300

#: Where the vendor publishes what this account may run, and what each of those
#: models says about itself.
#:
#: Measured 2026-11-09 on this machine (`GET /v2/enterprises/personal/models`
#: with the desktop app's own token): the roster is the "cli" agent's list —
#: 16 ids — and every row carries the window menu that model sells
#: (`contextWindow.supportedLengths`: 300000 / 600000 / 960000 on the hy4 rows,
#: with `defaultLength`), its own ceiling (`maxInputTokens`), its output cap
#: (`maxOutputTokens`) and the thinking levels it takes
#: (`reasoning.supportedEfforts`). `auto` has no menu and one fixed window.
#:
#: Until this existed the leg served a list we had copied by hand (10-13),
#: so the picker offered names the vendor no longer sells (`hy4-preview`,
#: `hy3-x`, `minimax-m2.7`) and said nothing about any window — leaving the
#: picker to invent one out of Plobi's own stale table (user, 11-09 18:4x:
#: 「显示的模型也不正确。别人Workbuddy都是Hy4了。你还是Hy4 Preview.」 and
#: 「上下文调整选项根本不符合这个模型」).
CATALOG_PATH = "/v2/enterprises/personal/models"


def _flatten_oai(parts) -> str:
    if isinstance(parts, str):
        return parts
    out = []
    for p in parts:
        if isinstance(p, dict):
            out.append(p.get("text", ""))
    return "".join(out)


class WorkbuddyApiProvider(Provider):
    name = "workbuddy_api"
    display_name = "CodeBuddy"

    def present(self) -> bool:
        """Present only if the local CodeBuddy session (or a configured key) exists.

        This leg rides software the user already installed; with neither a session nor
        a key there is nothing here to show a card for, and nothing to download.
        """
        return bool(self.accounts)

    def login_plan(self) -> dict:
        """No sign-in of ours to do: this leg reads the desktop app's own local session.

        The user already signed into CodeBuddy over there. Telling him to "connect" here
        would be a lie about where the credential lives, so the honest answer is
        "already signed in next door" — and if that session is gone, the verdict that
        says so is `credential_health()`, not a broken button.
        """
        return {"kind": "local_session", "name": self.display_name,
                "reason": "它用的是 CodeBuddy 桌面端自己的登录，Plobi 这边不用再登一次"}
    # Own-key HTTP passthrough: tools only if upstream dialect supports them.
    default_capabilities = Capabilities(
        stream=True,
        tools=True,
        vision=False,
        embeddings=False,
        sessionful=False,
        compliance="research",
    )

    def __init__(self, config, http):
        super().__init__(config, http)
        self.base_url = config["base_url"].rstrip("/")
        self.dialect = config.get("dialect", "openai")
        self.chat_path = config.get("chat_path", "/v1/chat/completions")
        self.chat_path_tpl = config.get("chat_path_template")
        # The card id, not the class name: this leg is configured under two ids
        # (`workbuddy` for the CodeBuddy HTTP route, `workbuddy_api` for the
        # passthrough build), and the ids we publish must carry the one the
        # desktop joins on.
        self.leg_id = str(config.get("leg") or self.name)
        self.model_prefix = f"{self.leg_id}/"
        #: Where the vendor publishes this account's roster and each model's knobs.
        self.catalog_path = str(config.get("catalog_path") or CATALOG_PATH)
        # No seed. The roster is the vendor's own (see `refresh_models`): a list
        # we wrote ourselves is a second truth that rots the day the vendor
        # renames a model — which is how `hy4-preview` outlived the vendor's
        # `hy4free` / `hy4std` in the picker for a week.
        self.served_models: tuple[str, ...] = ()
        self._model_map = config.get("model_map", {})
        #: The vendor's own rows, keyed by the id we publish them under.
        self._model_rows: dict[str, dict] = {}

    def credential_health(self) -> tuple[bool, str]:
        """Live when the desktop record holds a token that works or can be renewed.

        Unlike the Google leg we CAN renew this one ourselves (the refresh token
        sits in the same record), so an expired access token is still a usable leg.
        """
        st = ds.codebuddy_token_status()
        if not st.get("present"):
            return False, "no_desktop_credential"
        left = st.get("expires_in_seconds")
        if left is not None and left <= 0:
            if st.get("can_refresh"):
                return True, "renewable"
            return False, "credential_expired"
        return True, "ok"

    STORE_TOKEN_SOURCE = "codebuddy_desktop_store"

    def _wants_store(self, a: dict) -> bool:
        return (a.get("token_source") or self.config.get("token_source") or "") == self.STORE_TOKEN_SOURCE

    def _store_account(self, a: dict, i: int) -> "Account | None":
        """Build an account from the desktop app's own auth record.

        The values are read fresh every call and never written back — the desktop
        app owns that file. If it is not logged in we return None and let the
        caller report "no account", rather than inventing a credential.
        """
        got = ds.read_codebuddy_token()
        if not got:
            return None
        access, refresh, exp = got
        return Account(
            id=a.get("id", f"codebuddy-store-{i}"),
            provider=self.name,
            label=a.get("label", "CodeBuddy desktop store"),
            cred=Credential(
                access_token=access,
                refresh_token=refresh,
                expires_at=exp or 0.0,
                extra={"token_source": self.STORE_TOKEN_SOURCE},
            ),
        )

    async def discover_accounts(self) -> list[Account]:
        accs = []
        declared = self.config.get("accounts", []) or ([{}] if self.config.get("token_source") else [])
        for i, a in enumerate(declared):
            if self._wants_store(a):
                store_acc = self._store_account(a, i)
                if store_acc is not None:
                    accs.append(store_acc)
                continue
            accs.append(
                Account(
                    id=a.get("id", f"workbuddy-{i}"),
                    provider=self.name,
                    label=a.get("label", ""),
                    cred=Credential(
                        access_token=a.get("token", ""), refresh_token=a.get("refresh_token", "")
                    ),
                )
            )
        self.accounts = accs
        return accs

    async def _refresh_store_token(self, acc: Account) -> None:
        """Mint a new access token with the record's refresh token.

        Kept in memory only: the desktop app owns the file, and writing our renewed
        token back into it would make us a second writer of someone else's state.
        """
        if not acc.cred.refresh_token:
            raise UpstreamError(
                401,
                "workbuddy_api: desktop token expired and no refresh token in the record "
                "— open CodeBuddy/WorkBuddy and sign in once",
                reauth=True,
            )
        url = self.base_url + "/v2/auth/token/refresh"
        headers = {
            "content-type": "application/json",
            "X-Refresh-Token": acc.cred.refresh_token,
            "X-Auth-Refresh-Source": "plugin",
        }
        try:
            r = await self.http.post(url, headers=headers, json={})
        except Exception as e:
            raise UpstreamError(502, f"workbuddy_api refresh transport {type(e).__name__}")
        if r.status_code != 200:
            raise UpstreamError(
                401,
                f"workbuddy_api refresh refused with HTTP {r.status_code} — sign in to the "
                f"desktop app once",
                reauth=True,
            )
        try:
            data = r.json()
        except Exception:
            raise UpstreamError(502, "workbuddy_api refresh returned a non-JSON body")
        # The vendor wraps the payload; accept either shape rather than guessing one.
        body = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else data
        new_access = str(body.get("access_token") or body.get("accessToken") or "")
        if not new_access:
            keys = sorted(body.keys())[:12] if isinstance(body, dict) else type(body).__name__
            raise UpstreamError(
                502,
                f"workbuddy_api refresh response carried no access token (keys: {keys})",
            )
        acc.cred.access_token = new_access
        new_refresh = str(body.get("refresh_token") or body.get("refreshToken") or "")
        if new_refresh:
            acc.cred.refresh_token = new_refresh
        exp = ds._jwt_exp(new_access) or 0.0
        if not exp:
            try:
                raw_exp = body.get("expiresAt") or body.get("expires_at")
                exp = float(raw_exp) / 1000.0 if raw_exp else 0.0
            except Exception:
                exp = 0.0
        if exp:
            acc.cred.expires_at = exp

    async def ensure_fresh(self, acc: Account) -> None:
        if acc.cred.extra.get("token_source") == self.STORE_TOKEN_SOURCE:
            # Same self-heal as the antigravity leg: the desktop app may have
            # renewed its own record since we discovered this account.
            fresh = ds.read_codebuddy_token()
            if fresh:
                access, refresh, exp = fresh
                changed = access != acc.cred.access_token
                if changed:
                    acc.cred.access_token = access
                    acc.cred.refresh_token = refresh or acc.cred.refresh_token
                    if exp:
                        acc.cred.expires_at = exp
                elif exp:
                    acc.cred.expires_at = max(acc.cred.expires_at or 0.0, exp)
            if not acc.cred.access_token:
                raise UpstreamError(
                    401,
                    "workbuddy_api: no CodeBuddy desktop credential — open the desktop app "
                    "and sign in once",
                    reauth=True,
                )
            if not acc.cred.expires_at or acc.cred.expires_at - time.time() > TOKEN_SKEW_SECONDS:
                return
            await self._refresh_store_token(acc)
            return
        # Static tokens by default.
        if not acc.cred.access_token:
            raise UpstreamError(401, "no workbuddy token configured", reauth=True)

    # --- the vendor's own catalogue ---------------------------------------
    async def refresh_models(self, acc: Account) -> list[str] | None:
        """The roster the vendor publishes for this account, read now.

        Returns the ids we publish (``<leg>/<upstream id>``) on success, and
        ``None`` on any failure — the Registry then keeps whatever we had, and a
        leg that never managed a read simply offers nothing. That is deliberate:
        a hand-written list is a second truth about names that only the vendor
        can vouch for, and a name the vendor dropped must leave the picker
        rather than sit there promising a turn that will fail.

        The read costs nothing (the catalogue is free) and the same token that
        serves a turn opens it.
        """
        try:
            await self.ensure_fresh(acc)
            res = await self.http.get(
                self.base_url + self.catalog_path,
                headers=self._render_headers(acc, {"model": ""}),
            )
            if res.status_code != 200:
                return None
            payload = res.json()
        except Exception:  # noqa: BLE001 — a catalogue read must not break routing
            return None
        if not isinstance(payload, dict):
            return None
        data = payload.get("data")
        if not isinstance(data, dict):
            return None

        rows = {
            str(row.get("id")): row
            for row in (data.get("models") or [])
            if isinstance(row, dict) and row.get("id")
        }
        roster = self._roster_of(data)
        if not roster:
            return None

        self._model_rows = {
            f"{self.model_prefix}{mid}": rows[mid] for mid in roster if mid in rows
        }
        self.served_models = tuple(f"{self.model_prefix}{mid}" for mid in roster)
        return list(self.served_models)

    @staticmethod
    def _roster_of(data: dict) -> list[str]:
        """Which models this account may run, as the vendor's own agent says.

        The catalogue lists one entry per agent; the one tagged ``default`` is the
        agent the product itself runs, and its ``models`` list is the roster.
        An agent without a model list (a sub-agent that borrows the parent's)
        says nothing and is skipped.
        """
        agents = data.get("agents")
        if not isinstance(agents, list):
            return []
        fallback: list[str] = []
        for agent in agents:
            if not isinstance(agent, dict):
                continue
            models = agent.get("models")
            if not isinstance(models, list) or not models:
                continue
            ids = [str(m).strip() for m in models if str(m).strip()]
            if not ids:
                continue
            tags = [str(t).strip().lower() for t in (agent.get("tags") or [])]
            if "default" in tags:
                return ids
            if not fallback:
                fallback = ids
        return fallback

    def tuning(self, model: str) -> dict:
        """What this model lets the user change, from the vendor's own row.

        Measured 2026-11-09: the hy4 rows sell a **menu** of windows
        (300000 / 600000 / 960000, default 300000) and say so with
        ``contextWindow.supportedLengths``; ``auto`` sells none and has one
        fixed window (``maxInputTokens``). Each row also states its own ceiling
        and output cap, and the thinking levels it takes
        (``reasoning.supportedEfforts``).

        A model that says nothing about a window gets no window from us either:
        Plobi's own table is a guess, and a guess is what the user was looking
        at when they said 「上下文调整选项根本不符合这个模型」.
        """
        row = self._model_rows.get(model) or {}
        out: dict[str, Any] = {}

        window = row.get("contextWindow") if isinstance(row.get("contextWindow"), dict) else {}
        lengths = sorted({
            int(value) for value in (window.get("supportedLengths") or [])
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
        })
        ceiling = row.get("maxInputTokens")
        if lengths:
            default = window.get("defaultLength")
            out["context"] = {
                "adjustable": len(lengths) > 1,
                "choices": lengths,
                "default": (
                    int(default)
                    if isinstance(default, (int, float)) and not isinstance(default, bool)
                    and int(default) in lengths
                    else lengths[0]
                ),
                "max": lengths[-1],
            }
        elif isinstance(ceiling, (int, float)) and not isinstance(ceiling, bool) and ceiling > 0:
            # One window, and the vendor says it is not adjustable.
            out["context"] = {
                "adjustable": False,
                "choices": [int(ceiling)],
                "default": int(ceiling),
                "max": int(ceiling),
            }

        cap = row.get("maxOutputTokens")
        if isinstance(cap, (int, float)) and not isinstance(cap, bool) and cap > 0:
            out["max_output"] = int(cap)

        reasoning = row.get("reasoning") if isinstance(row.get("reasoning"), dict) else {}
        efforts = [
            str(level).strip().lower()
            for level in (reasoning.get("supportedEfforts") or [])
            if str(level).strip()
        ]
        if efforts:
            out["efforts"] = efforts
            default_effort = str(reasoning.get("defaultEffort") or "").strip().lower()
            out["default_effort"] = default_effort if default_effort in efforts else efforts[0]
        return out

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
    async def _collect_openai_stream(self, acc: Account, oai_req: dict) -> dict:
        """Drive the SSE path and fold it back into one chat completion.

        Needed because the upstream refuses non-stream (code 11101). Tool calls
        arrive *split across frames* — `function.name` in one delta, the JSON in
        `function.arguments` accumulated over several — so they are keyed by
        `index` and joined before returning, exactly as the vendor's own client does.
        """
        content: list[str] = []
        reasoning: list[str] = []
        calls: dict[int, dict] = {}
        finish = None
        usage = None
        async for frame in self.chat_stream(acc, oai_req):
            if isinstance(frame.get("usage"), dict):
                usage = frame["usage"]
            for ch in frame.get("choices") or []:
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
                delta = ch.get("delta") or {}
                if isinstance(delta.get("content"), str):
                    content.append(delta["content"])
                for key in ("reasoning", "reasoning_content"):
                    if isinstance(delta.get(key), str):
                        reasoning.append(delta[key])
                for tc in delta.get("tool_calls") or []:
                    slot = calls.setdefault(
                        tc.get("index", len(calls)),
                        {"id": "", "type": "function",
                         "function": {"name": "", "arguments": ""}},
                    )
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["function"]["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["function"]["arguments"] += fn["arguments"]

        message: dict = {"role": "assistant", "content": "".join(content)}
        if calls:
            message["tool_calls"] = [calls[i] for i in sorted(calls)]
        if reasoning:
            message["reasoning"] = "".join(reasoning)
        out = {
            "id": f"chatcmpl-workbuddy-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": oai_req.get("model", ""),
            "choices": [{"index": 0, "message": message,
                         "finish_reason": finish or ("tool_calls" if calls else "stop")}],
        }
        if usage:
            out["usage"] = usage
        return out

    async def chat(self, acc: Account, oai_req: dict) -> dict:
        if self.dialect == "openai":
            # Measured 2026-10-12: a JSON (non-stream) request is refused with
            # {"code":11101,"msg":"Non-stream chat request is currently not
            # supported"} from both failover origins — and a 400 rather than a 401
            # is also the proof the bearer token authenticated. So never ask for
            # one; always stream, then aggregate.
            return await self._collect_openai_stream(acc, oai_req)
        if self.dialect == "anthropic":
            return await self._anthropic_chat(acc, oai_req)
        raise UpstreamError(501, f"dialect {self.dialect} non-stream not implemented")

    async def chat_stream(self, acc: Account, oai_req: dict) -> AsyncIterator[dict]:
        if self.dialect == "openai":
            req = dict(oai_req, model=self._map_model(oai_req["model"]), stream=True)
            async with self.http.stream(
                "POST",
                self.base_url + self._render_path(oai_req),
                headers=self._render_headers(acc, oai_req),
                json=req,
            ) as r:
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
        text = "".join(
            c.get("text", "")
            for c in obj.get("content", [])
            if isinstance(c, dict) and c.get("type") == "text"
        )
        return {
            "id": obj.get("id", f"chatcmpl-{uuid.uuid4().hex[:24]}"),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": obj.get("stop_reason", "stop"),
                    "message": {"role": "assistant", "content": text},
                }
            ],
            "usage": obj.get("usage", {}),
        }

    async def _anthropic_chat(self, acc: Account, oai_req: dict) -> dict:
        body = self._to_anthropic(oai_req)
        r = await self.http.post(
            self.base_url + self._render_path(oai_req),
            headers=self._render_headers(acc, oai_req),
            json=body,
        )
        err = self._classify_http(r.status_code)
        if err:
            raise err
        r.raise_for_status()
        return self._from_anthropic(r.json(), oai_req["model"])

    async def _anthropic_stream(self, acc: Account, oai_req: dict) -> AsyncIterator[dict]:
        body = self._to_anthropic(dict(oai_req, stream=True))
        cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        async with self.http.stream(
            "POST",
            self.base_url + self._render_path(oai_req),
            headers=self._render_headers(acc, oai_req),
            json=body,
        ) as r:
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
                except json.JSONDecodeError:
                    continue
                if (
                    obj.get("type") == "content_block_delta"
                    and obj.get("delta", {}).get("type") == "text_delta"
                ):
                    yield _chunk(cid, oai_req["model"], obj["delta"]["text"])
                elif obj.get("type") == "message_stop":
                    yield _chunk(cid, oai_req["model"], None, finish="stop")
            yield _chunk(cid, oai_req["model"], None, finish="stop")


def _chunk(cid: str, model: str, text, finish=None) -> dict:
    delta = {} if text is None else {"content": text}
    return {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
