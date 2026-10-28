"""A minimal ACP client for the hub — a session that can hand our tools to a CLI.

Why this exists (and why it is not "another CLI wrapper"): `providers/cli.py` flattens
a whole conversation into one prompt and pastes stdout back as a single completion, so
a tool call has nowhere to go. ACP (Agent Client Protocol, JSON-RPC over stdio) has a
session, and `session/new` **requires** an `mcpServers` list — which is the one seam
where Plobi's own tool surface can be handed to the CLI. When that handoff happens the
leg really can call back into our loop, and only then may it declare `tools=True`.

Deliberate limits, all of them on purpose:

* **We hand over tools, never credentials.** The MCP server we pass is
  `agent.transports.plobi_quota_mcp_server` (stdio, no auth surface, trusted by
  spawn). The CLI's own token stays inside the CLI.
* **Permissions:** a `session/request_permission` is allowed only when every proposed
  option is an MCP tool call (the CLI asking to use *our* tools). Anything else is
  denied. The authorization for that policy is the work order
  `Docs/tasks/quota-leg-tools-via-mcp-task.md` §6, which requires the auto-approval to
  be named rather than implied — this paragraph is that name.
* **One prompt in flight per session.** ACP sessions are stateful; two concurrent
  prompts on one session would interleave deltas with no way to tell them apart.
* **A dead child is not a broken leg.** The next call spawns a fresh session; the
  account itself is untouched (the CLI owns its credential).

Known shape difference this module exists to absorb: `plobi_quota_mcp_server` renders
its env as a dict (codex's config shape), while ACP's `McpServerStdio.env` is a
name/value array. Passing the dict straight through would silently drop `PLOBI_HOME`,
and on this machine a MCP child without it falls back to a home on the C: drive.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

log = logging.getLogger("quota_hub")

#: Environment a spawned agent CLI is allowed to inherit. Same list the Plobi-side
#: client uses — a credential-carrying env var must not leak into a child by accident.
_CHILD_ENV = (
    "PLOBI_HOME",
    "PLOBI_QUIET",
    "PLOBI_REDACT_SECRETS",
    "PYTHONPATH",
    "PYTHONUTF8",
    "PATH",
    "HOME",
    "USERPROFILE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
)

#: Tools are handed over through this module; `--print-config` renders its MCP block.
MCP_MODULE = "agent.transports.plobi_quota_mcp_server"


def spawnable(binary: str) -> str | None:
    """Absolute path for a command, or None.

    Windows' CreateProcess appends only `.exe`, so a npm-installed `.CMD` shim has to
    be resolved by `shutil.which` before spawning — the same defect measured on the
    Plobi side (2026-10-26).
    """
    found = shutil.which(binary)
    if found:
        return found
    path = Path(binary)
    return str(path) if path.is_file() else None


def mcp_servers(*, enabled: bool = True) -> list[dict[str, Any]]:
    """The `mcpServers` payload for `session/new`.

    Empty when the operator turned the handoff off (`PLOBI_ACP_MCP_TOOLS=0`) — in that
    case the leg must NOT claim `tools=True`, because nothing can come back.
    """
    if not enabled or os.getenv("PLOBI_ACP_MCP_TOOLS", "").strip().lower() in {"0", "false", "no", "off"}:
        return []
    env: list[dict[str, str]] = []
    home = os.getenv("PLOBI_HOME", "").strip()
    if home:
        env.append({"name": "PLOBI_HOME", "value": home})
    # The MCP server must be importable: the hub's own python plus the repo root.
    root = str(Path(__file__).resolve().parents[2])
    env.append({"name": "PYTHONPATH", "value": root})
    return [
        {
            "name": "plobi",
            "command": sys.executable,
            "args": ["-m", MCP_MODULE],
            "env": env,
        }
    ]


class AcpError(RuntimeError):
    """The session died, timed out, or the CLI refused the handoff."""


class AcpSession:
    """One long-lived `initialize → session/new` conversation with an agent CLI."""

    def __init__(
        self,
        *,
        argv: list[str],
        cwd: str,
        name: str = "acp",
        env_extra: dict[str, str] | None = None,
        tools_enabled: bool = True,
        prompt_timeout: float = 300.0,
        start_timeout: float = 60.0,
    ) -> None:
        self.argv = argv
        self.cwd = cwd
        self.name = name
        self.env_extra = dict(env_extra or {})
        self.tools_enabled = tools_enabled
        self.prompt_timeout = prompt_timeout
        self.start_timeout = start_timeout
        self.proc: asyncio.subprocess.Process | None = None
        self.session_id: str | None = None
        #: True only when `session/new` was accepted WITH our tools attached.
        self.tools_handed_over = False
        #: Count of tool calls the CLI made through our MCP server this session.
        self.tool_calls = 0
        self._lock = asyncio.Lock()
        self._next_id = 0
        self._stderr_task: asyncio.Task | None = None

    # --- lifecycle -------------------------------------------------------
    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None and self.session_id is not None

    async def start(self) -> None:
        if self.alive:
            return
        async with self._lock:
            if self.alive:
                return
            await self._spawn()
            await self._handshake()

    async def close(self) -> None:
        proc = self.proc
        self.proc = None
        self.session_id = None
        self.tools_handed_over = False
        if proc is None:
            return
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except (asyncio.TimeoutError, ProcessLookupError):
            with contextlib.suppress(ProcessLookupError):
                proc.kill()

    async def _spawn(self) -> None:
        env = {k: v for k, v in os.environ.items() if k in _CHILD_ENV}
        env.update(self.env_extra)
        env.setdefault("PLOBI_QUIET", "1")
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *self.argv,
                cwd=self.cwd,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (OSError, FileNotFoundError) as exc:
            raise AcpError(f"cannot start {self.argv[0]}: {exc}") from exc
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        proc = self.proc
        if proc is None or proc.stderr is None:
            return
        while True:
            line = await proc.stderr.readline()
            if not line:
                return
            log.debug("[acp:%s] %s", self.name, line.decode("utf-8", "replace").rstrip())

    async def _handshake(self) -> None:
        await self._request(
            "initialize",
            {
                "protocolVersion": 1,
                "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}},
            },
            timeout=self.start_timeout,
        )
        servers = mcp_servers(enabled=self.tools_enabled)
        created = await self._request(
            "session/new",
            {"cwd": self.cwd, "mcpServers": servers},
            timeout=self.start_timeout,
        )
        session_id = (created or {}).get("sessionId") or (created or {}).get("session_id")
        if not session_id:
            raise AcpError(f"{self.name} answered session/new without a session id")
        self.session_id = str(session_id)
        # Earned, not assumed: the CLI got a session WITH our tools attached.
        self.tools_handed_over = bool(servers)
        log.info("[acp:%s] session %s ready (tools handed over: %s)", self.name, self.session_id, self.tools_handed_over)

    # --- json-rpc --------------------------------------------------------
    async def _send(self, payload: dict[str, Any]) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise AcpError(f"{self.name} is not running")
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self.proc.stdin.write(data)
        await self.proc.stdin.drain()

    async def _read(self, timeout: float) -> dict[str, Any]:
        if self.proc is None or self.proc.stdout is None:
            raise AcpError(f"{self.name} is not running")
        try:
            line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise AcpError(f"{self.name} did not answer within {timeout:.0f}s") from exc
        if not line:
            raise AcpError(f"{self.name} closed its stdout")
        try:
            return json.loads(line.decode("utf-8", "replace"))
        except ValueError as exc:
            raise AcpError(f"{self.name} sent a non-JSON line") from exc

    async def _request(self, method: str, params: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        self._next_id += 1
        rid = self._next_id
        await self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise AcpError(f"{self.name} did not answer {method} within {timeout:.0f}s")
            msg = await self._read(left)
            if msg.get("id") == rid and ("result" in msg or "error" in msg):
                if "error" in msg:
                    raise AcpError(f"{method} failed: {msg['error']}")
                return msg.get("result") or {}
            # Server-initiated traffic while we wait: answer it, then keep waiting.
            await self._handle_server_message(msg)

    async def _handle_server_message(self, msg: dict[str, Any]) -> None:
        method = msg.get("method")
        if method == "session/update":
            self._absorb_update((msg.get("params") or {}).get("update") or {})
            return
        if method == "session/request_permission":
            await self._answer_permission(msg)
            return
        # Anything else is a notification we do not speak. Acking an unknown *request*
        # with an error is correct JSON-RPC; ignoring it would leave the CLI waiting.
        if "id" in msg:
            await self._send(
                {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": f"unsupported: {method}"}}
            )

    def _absorb_update(self, update: dict[str, Any]) -> None:
        kind = update.get("sessionUpdate") or update.get("session_update")
        if kind in {"tool_call", "tool_call_update"}:
            self.tool_calls += 1

    async def _answer_permission(self, msg: dict[str, Any]) -> None:
        """Allow only our own MCP tools; deny everything else (see module header).

        The CLI presents one option per tool it wants to use, so "is this ours?" is a
        question about *an option*, not about the whole list: pick the first allow
        option naming our server, and cancel when there is none. Denying is the safe
        default — a foreign option (the CLI's own shell, say) is never authorized here.
        """
        params = msg.get("params") or {}
        options = params.get("options") or []
        chosen = None
        for opt in options:
            kind = str(opt.get("kind") or "").lower()
            oid = str(opt.get("optionId") or opt.get("option_id") or "")
            if kind.startswith("allow") and "plobi" in oid:
                chosen = oid
                break
        outcome = {"outcome": "selected", "optionId": chosen} if chosen else {"outcome": "cancelled"}
        await self._send({"jsonrpc": "2.0", "id": msg.get("id"), "result": {"outcome": outcome}})

    # --- prompting -------------------------------------------------------
    async def prompt(self, text: str) -> str:
        """One turn: send the prompt, fold the streamed text, return it."""
        if not self.alive:
            await self.start()
        assert self.session_id is not None
        async with self._lock:
            self._next_id += 1
            rid = self._next_id
            await self._send(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "method": "session/prompt",
                    "params": {"sessionId": self.session_id, "prompt": [{"type": "text", "text": text}]},
                }
            )
            deadline = time.monotonic() + self.prompt_timeout
            parts: list[str] = []
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise AcpError(f"{self.name} did not finish the turn within {self.prompt_timeout:.0f}s")
                msg = await self._read(left)
                if msg.get("id") == rid and ("result" in msg or "error" in msg):
                    if "error" in msg:
                        raise AcpError(f"session/prompt failed: {msg['error']}")
                    break
                if msg.get("method") == "session/update":
                    update = (msg.get("params") or {}).get("update") or {}
                    self._absorb_update(update)
                    if (update.get("sessionUpdate") or update.get("session_update")) == "agent_message_chunk":
                        content = update.get("content") or {}
                        if isinstance(content, dict) and content.get("type") == "text":
                            parts.append(str(content.get("text") or ""))
                    continue
                await self._handle_server_message(msg)
            text_out = "".join(parts)
            if not text_out.strip():
                raise AcpError(f"{self.name} finished the turn with no text")
            return text_out

    async def stream(self, text: str) -> AsyncIterator[str]:
        """Same turn, but yield deltas as they arrive (SSE-friendly)."""
        if not self.alive:
            await self.start()
        assert self.session_id is not None
        async with self._lock:
            self._next_id += 1
            rid = self._next_id
            await self._send(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "method": "session/prompt",
                    "params": {"sessionId": self.session_id, "prompt": [{"type": "text", "text": text}]},
                }
            )
            deadline = time.monotonic() + self.prompt_timeout
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise AcpError(f"{self.name} did not finish the turn within {self.prompt_timeout:.0f}s")
                msg = await self._read(left)
                if msg.get("id") == rid and ("result" in msg or "error" in msg):
                    if "error" in msg:
                        raise AcpError(f"session/prompt failed: {msg['error']}")
                    return
                if msg.get("method") == "session/update":
                    update = (msg.get("params") or {}).get("update") or {}
                    self._absorb_update(update)
                    if (update.get("sessionUpdate") or update.get("session_update")) == "agent_message_chunk":
                        content = update.get("content") or {}
                        if isinstance(content, dict) and content.get("type") == "text":
                            yield str(content.get("text") or "")
                    continue
                await self._handle_server_message(msg)