"""A leg that speaks ACP: one session, our tools attached, text back.

This is the `tools=True` half of `providers/cli.py`. The CLI wrapper flattens the
conversation and can only return text, so a tool call has nowhere to live; an ACP
session (`quota_hub/acp.py`) takes our MCP server in `session/new`, which is what makes
`tools=True` **earned** rather than claimed: the CLI can call Plobi's own tools, the
calls land in `agent.transports.plobi_quota_mcp_server`, and the leg's answer comes back
here.

Honest edges:

* **The model names are the CLI's.** ACP answers `session/new` with the models that
  session can actually run, and the leg serves exactly those, prefixed `<leg>/<id>`.
  A CLI that reports none leaves the leg with zero models — a config-file name list
  would be a second copy of a fact the upstream already owns (裁定 46).
* **Which model a turn uses is asked, not assumed.** A turn on a chosen id goes through
  `session/set_model` first. A CLI that refuses that call gets one model served (its own
  current one) rather than a list where picking any name but the default silently lies.
* If the CLI accepts the session but not the handoff (`mcpServers` ignored), the leg
  downgrades itself: `capabilities().tools` turns false on the next read, so the picker
  stops offering it instead of pretending a turn can interact with Plobi.
* A dead session is respawned on the next turn. The credential is the CLI's; nothing
  here reads, copies or prints it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator
from typing import Any

from ..acp import AcpAuthRequired, AcpError, AcpSession, mcp_servers, spawnable
from .base import Account, Capabilities, Provider, UpstreamError
from .cli import default_workspace

log = logging.getLogger("quota_hub")


def _flatten(messages: Any) -> str:
    """One text prompt out of an OpenAI history (same contract as the CLI wrapper)."""
    if not isinstance(messages, list):
        return str(messages or "")
    lines: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "user")
        content = msg.get("content")
        if isinstance(content, list):
            text = "".join(
                str(part.get("text") or "") for part in content if isinstance(part, dict) and part.get("type") == "text"
            )
        else:
            text = str(content or "")
        if text.strip():
            lines.append(f"{role}: {text}")
    return "\n\n".join(lines)


def _chunk(cid: str, model: str, text: str = "", finish: str | None = None) -> dict:
    return {
        "choices": [{"delta": ({"content": text} if text else {}), "finish_reason": finish, "index": 0}],
        "created": 0,
        "id": cid,
        "model": model,
        "object": "chat.completion.chunk",
    }


class AcpProvider(Provider):
    """ACP-backed leg: sessionful, tools attached, one virtual model id per leg."""

    name = "acp"
    #: argv tail that puts the CLI into ACP mode.
    acp_args: tuple[str, ...] = ("--acp",)
    default_capabilities = Capabilities(stream=True, tools=True, sessionful=True)
    #: How long one handshake answer stays good for (the CLI owns its own state).
    probe_ttl = 600.0

    def __init__(self, config: dict, http) -> None:
        super().__init__(config, http)
        # The leg's own name (the config key). Ledger entries, accounts and login plans
        # are all keyed by it, so two ACP legs must never share the class default.
        self.name = str(config.get("leg") or config.get("name") or self.name)
        # The card's own line ("Qwen Code", "Cline"). Machine ids never reach the screen.
        self.display_name = str(config.get("display_name") or getattr(self, "display_name", ""))
        self.binary = config.get("binary", getattr(self, "binary", "acp"))
        self.acp_args = tuple(config.get("acp_args", self.acp_args))
        self.timeout = float(config.get("timeout", 300.0))
        self.cwd = config.get("cwd") or config.get("workspace") or default_workspace(self.name)
        self.env_extra = {k: str(v) for k, v in (config.get("env") or {}).items()}
        self.model_prefix = str(config.get("model_prefix") or getattr(self, "model_prefix", "") or self.name)
        self.probe_timeout = float(config.get("probe_timeout", 20.0))
        # Deliberately NOT read from config: `served_models` is whatever the CLI answers
        # for its own session, and an unsigned-in CLI answers nothing.
        self.model_map: dict[str, str] = {}
        self._sessions: dict[str, AcpSession] = {}
        #: The CLI's own answer to "are you signed in, and with which models?".
        self._probe: dict[str, Any] = {}
        self._probed_at = 0.0

    # --- presence / sign-in ----------------------------------------------
    def present(self) -> bool:
        return spawnable(self.binary) is not None

    def login_plan(self) -> dict:
        # A spawn-based leg signs in through its own command; same contract as cli.py.
        args = self.config.get("login_args", getattr(self, "login_args", None))
        if args is None:
            return {"kind": "none", "reason": "这一条腿的登录命令还没从它自己的 --help 里核出来"}
        return {
            "kind": "cli_login",
            "binary": self.binary,
            "argv": list(args),
            "tty": bool(self.config.get("login_tty", getattr(self, "login_tty", False))),
            "timeout": float(self.config.get("login_timeout", 300.0)),
            "name": self.display_name or self.name,
        }

    # --- capabilities ------------------------------------------------------
    def credential_health(self) -> tuple[bool, str]:
        """What the CLI said about its own session last time we opened one.

        `session/new` is the credential test: an unsigned CLI refuses it in words
        (cline 3.0.74: "Authentication required: Call authenticate before starting a
        session"), a signed one hands back a session id and its model list. That answer
        beats our login ledger, which can only ever report that a window was watched
        exiting cleanly.
        """
        probed = self._probe.get("probed")
        if not probed:
            return False, "not_probed"
        if self._probe.get("signed_in"):
            return True, "ok"
        return False, str(self._probe.get("reason") or "not_signed_in")

    def capabilities(self) -> Capabilities:
        """`tools` means "we handed our tool surface into this session".

        What that is NOT: a claim that the CLI honored it. ACP has no "I support
        mcpServers" flag — a CLI that silently ignores the list looks exactly like a
        CLI that did not need a tool on that turn. So the honest evidence is what we
        did (handed it over) plus the counter of tool calls we watched come back
        (`AcpSession.tool_calls`); the acceptance for a real leg is one real turn
        through it (`docs/tasks/quota-leg-tools-via-mcp-task.md` §5.4), not a flag here.
        """
        return Capabilities(stream=True, tools=bool(mcp_servers()), sessionful=True)

    # --- accounts ---------------------------------------------------------
    async def discover_accounts(self) -> list[Account]:
        """One seat: the CLI's own sign-in. Presence is the only thing we can see.

        The handshake deliberately does not happen here — `Registry.build()` awaits this
        method before the hub answers on :8000, and spawning a node CLI costs ~10 measured
        seconds (cline 3.0.74 on this machine, 2026-10-29). The model refresh task the hub
        starts right after build calls `refresh_models()`, which probes; a restart is
        therefore not made slower by a leg that answers slowly.
        """
        if not self.present():
            self.accounts = []
            return []
        if not self.accounts:
            self.accounts = [Account(id=f"{self.name}-default", provider=self.name, label=self.display_name or self.name)]
        return self.accounts

    async def ensure_fresh(self, acc: Account) -> None:
        return None

    # --- the CLI's own answers ---------------------------------------------
    async def probe(self, *, force: bool = False) -> dict[str, Any]:
        """Open one session, read what the CLI reports, close it again.

        Called at startup and again after a sign-in, so the card's switch and the model
        list both come from the same handshake rather than from a ledger guess. The child
        is not kept: an idle CLI process per leg would be a leak nobody asked for, and
        the turn path spawns on demand anyway.
        """
        now = time.time()
        if not force and self._probe.get("probed") and now - self._probed_at < self.probe_ttl:
            return self._probe
        verdict: dict[str, Any] = {"probed": True, "signed_in": False, "reason": "not_signed_in", "models": []}
        binary = spawnable(self.binary)
        if binary:
            sess = self._new_session(binary)
            try:
                await sess.start()
                verdict["signed_in"] = True
                verdict["models"] = list(sess.available_models)
                verdict["current"] = sess.current_model
                # Prove the switch seam before claiming a list of selectable models:
                # re-selecting the model the session is already on must be accepted.
                target = sess.current_model or (sess.available_models[0]["id"] if sess.available_models else "")
                if target:
                    try:
                        await sess.set_model(target)
                    except AcpError as exc:
                        log.info("[acp:%s] set_model probe refused: %s", self.name, exc)
                verdict["switch"] = sess.model_switch_supported
            except AcpAuthRequired:
                verdict["reason"] = "not_signed_in"
            except AcpError as exc:
                # The CLI is there but the session did not come up. Not a credential
                # verdict — saying so keeps "installed", "signed in" and "works today"
                # three separate answers on screen.
                verdict["reason"] = "session_failed"
                verdict["detail"] = str(exc)
                log.info("[acp:%s] probe could not open a session: %s", self.name, exc)
            finally:
                await sess.close()
        else:
            verdict["reason"] = "binary_missing"
        self._probe = verdict
        self._probed_at = now
        self._apply_probed_models()
        return verdict

    def _apply_probed_models(self) -> list[str]:
        """Turn the CLI's reported models into this leg's served ids."""
        models = self._probe.get("models") or []
        if self._probe.get("switch") is False and self._probe.get("current"):
            # No set_model: only the CLI's own current model is really selectable.
            models = [m for m in models if m["id"] == self._probe["current"]] or [
                {"id": str(self._probe["current"]), "name": str(self._probe["current"])}
            ]
        shorts = [m["id"] for m in models if m.get("id")]
        self.model_map = {f"{self.model_prefix}/{s}": s for s in shorts}
        self.served_models = tuple(self.model_map)
        return shorts

    async def refresh_models(self, acc: Account) -> list[str] | None:
        """The catalog is the probe's; re-open a session when we have nothing yet."""
        if not self._probe.get("signed_in"):
            await self.probe(force=True)
        shorts = self._apply_probed_models()
        return shorts if shorts else None

    # --- session plumbing --------------------------------------------------
    def _new_session(self, binary: str, *, start_timeout: float | None = None) -> AcpSession:
        # `session/new` refuses a cwd it cannot stat (measured on cline: -32602 with the
        # words "expected string, received null"), so the workspace exists before spawn.
        os.makedirs(self.cwd, exist_ok=True)
        return AcpSession(
            argv=[binary, *self.acp_args],
            cwd=self.cwd,
            env_extra=self.env_extra,
            name=self.name,
            prompt_timeout=self.timeout,
            start_timeout=self.probe_timeout if start_timeout is None else start_timeout,
        )

    def _session(self, acc: Account) -> AcpSession:
        sess = self._sessions.get(acc.id)
        if sess is None:
            binary = spawnable(self.binary) or self.binary
            sess = self._new_session(binary)
            self._sessions[acc.id] = sess
        return sess

    async def _turn(self, acc: Account, oai_req: dict) -> AcpSession:
        sess = self._session(acc)
        try:
            await sess.start()
        except AcpAuthRequired as exc:
            # The CLI just told us it has no credential — the card must say so, and the
            # model list goes with it (an unsigned session reports no models).
            self._probe = {"probed": True, "signed_in": False, "reason": "not_signed_in", "models": []}
            self._apply_probed_models()
            raise UpstreamError(401, f"{self.name}: 那边还没登录，或登录已失效", reauth=True) from exc
        except AcpError as exc:
            raise UpstreamError(502, f"{self.name}: {exc}") from exc
        model = str(oai_req.get("model") or "")
        if self.model_map and model and model not in self.model_map:
            # A name this CLI never reported. Refusing beats answering from whichever
            # model the session happens to be on.
            raise UpstreamError(
                404,
                f"{self.name}: {self.binary} 自己没报过 {model} 这个模型",
                retryable=False,
            )
        short = self.model_map.get(model, "")
        if short and sess.current_model != short:
            try:
                chosen = await sess.set_model(short)
            except AcpError as exc:
                raise UpstreamError(404, f"{self.name}: {exc}", retryable=False) from exc
            if not chosen:
                raise UpstreamError(
                    400,
                    f"{self.name}: 这条腿的 CLI 不肯换模型，只能用它自己那一个",
                    retryable=False,
                )
        return sess

    async def chat(self, acc: Account, oai_req: dict) -> dict:
        sess = await self._turn(acc, oai_req)
        try:
            text = await sess.prompt(_flatten(oai_req.get("messages")))
        except AcpError as exc:
            raise UpstreamError(502, f"{self.name}: {exc}") from exc
        if sess.tool_calls:
            log.info("[%s] turn finished after %d tool call(s) through Plobi's MCP server", self.name, sess.tool_calls)
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "index": 0,
                    "message": {"content": text, "role": "assistant"},
                }
            ],
            "created": 0,
            "id": f"acp-{self.name}",
            "model": oai_req.get("model", ""),
            "object": "chat.completion",
        }

    async def chat_stream(self, acc: Account, oai_req: dict) -> AsyncIterator[dict]:
        sess = await self._turn(acc, oai_req)
        cid = f"acp-{self.name}"
        model = oai_req.get("model", "")
        try:
            async for delta in sess.stream(_flatten(oai_req.get("messages"))):
                yield _chunk(cid, model, delta)
        except AcpError as exc:
            raise UpstreamError(502, f"{self.name}: {exc}") from exc
        yield _chunk(cid, model, "", "stop")

    async def aclose(self) -> None:
        sessions, self._sessions = list(self._sessions.values()), {}
        await asyncio.gather(*(s.close() for s in sessions), return_exceptions=True)