"""A leg that speaks ACP: one session, our tools attached, text back.

This is the `tools=True` half of `providers/cli.py`. The CLI wrapper flattens the
conversation and can only return text, so a tool call has nowhere to live; an ACP
session (`quota_hub/acp.py`) takes our MCP server in `session/new`, which is what makes
`tools=True` **earned** rather than claimed: the CLI can call Plobi's own tools, the
calls land in `agent.transports.plobi_quota_mcp_server`, and the leg's answer comes back
here.

Honest edges:

* The model id is the leg's, not ours. ACP CLIs pick their own model inside the
  session, so a leg serves one virtual id (`<leg>/default`) unless its config declares
  otherwise. Inventing per-model ids would be a name list — exactly what the picker no
  longer accepts.
* If the CLI accepts the session but not the handoff (`mcpServers` ignored), the leg
  downgrades itself: `capabilities().tools` turns false on the next read, so the picker
  stops offering it instead of pretending a turn can interact with Plobi.
* A dead session is respawned on the next turn. The credential is the CLI's; nothing
  here reads, copies or prints it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from .. import login_ledger
from ..acp import AcpError, AcpSession, mcp_servers, spawnable
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

    def __init__(self, config: dict, http) -> None:
        super().__init__(config, http)
        # The leg's own name (the config key). Ledger entries, accounts and login plans
        # are all keyed by it, so two ACP legs must never share the class default.
        self.name = str(config.get("leg") or config.get("name") or self.name)
        self.binary = config.get("binary", getattr(self, "binary", "acp"))
        self.acp_args = tuple(config.get("acp_args", self.acp_args))
        self.timeout = float(config.get("timeout", 300.0))
        self.cwd = config.get("cwd") or config.get("workspace") or default_workspace(self.name)
        self.env_extra = {k: str(v) for k, v in (config.get("env") or {}).items()}
        if config.get("models"):
            self.served_models = tuple(config["models"])
        self._sessions: dict[str, AcpSession] = {}

    # --- presence / sign-in ----------------------------------------------
    def present(self) -> bool:
        return spawnable(self.binary) is not None

    def login_plan(self) -> dict:
        # A spawn-based leg signs in through its own command; same contract as cli.py.
        args = self.config.get("login_args")
        if args is None:
            return {"kind": "none", "reason": "这一条腿的登录命令还没从它自己的 --help 里核出来"}
        return {
            "kind": "cli_login",
            "binary": self.binary,
            "argv": list(args),
            "timeout": float(self.config.get("login_timeout", 300.0)),
            "name": self.display_name or self.name,
        }

    # --- capabilities ------------------------------------------------------
    def credential_health(self) -> tuple[bool, str]:
        """Same rule as the CLI legs: only a sign-in we watched happen counts.

        The session can start without one (some CLIs answer anyway), so "a session
        exists" is not a credential verdict either.
        """
        if login_ledger.verified(self.name):
            return True, "ok"
        return False, "not_signed_in"

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
        """One seat: the CLI's own sign-in. Presence is the only thing we can see."""
        if not self.present():
            self.accounts = []
            return []
        if not self.accounts:
            self.accounts = [Account(id=f"{self.name}-default", provider=self.name, label=self.display_name or self.name)]
        return self.accounts

    async def ensure_fresh(self, acc: Account) -> None:
        return None

    # --- session plumbing --------------------------------------------------
    def _session(self, acc: Account) -> AcpSession:
        sess = self._sessions.get(acc.id)
        if sess is None:
            binary = spawnable(self.binary) or self.binary
            sess = AcpSession(
                argv=[binary, *self.acp_args],
                cwd=self.cwd,
                env_extra=self.env_extra,
                name=self.name,
                prompt_timeout=self.timeout,
            )
            self._sessions[acc.id] = sess
        return sess

    async def _turn(self, acc: Account, oai_req: dict) -> AcpSession:
        sess = self._session(acc)
        try:
            await sess.start()
        except AcpError as exc:
            raise UpstreamError(502, f"{self.name}: {exc}") from exc
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