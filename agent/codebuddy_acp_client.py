"""CodeBuddy ACP client (seam B).

Drives the already-authenticated CodeBuddy CLI as an ACP agent backend and hands
it Plobi's own tool surface natively via session/new mcpServers, instead of the
flat text-completion path the opendesign-style aigw --tools "" route uses.

Why a second client instead of editing agent/copilot_acp_client.py:
- that client is Copilot-branded and bridges tools by a TEXT CONVENTION (flatten
  to one prompt, ask the model to emit an OpenAI-shaped tool-call block, scrape
  it from the text). Measured on this box 2026-10-08 that convention makes
  CodeBuddy return empty, and CodeBuddy does not honour it. CodeBuddy advertises
  delegateToolsSupport=true and natively runs tools handed to it as mcpServers,
  so this client uses the native path.
- a separate file leaves the Copilot production route untouched (no regression
  surface), per the operator's new-dedicated-client decision.

Approval: CodeBuddy sends session/request_permission before executing a delegated
tool. It is bridged to an injected Plobi approval callback; with no callback in
this process it FAILS CLOSED (deny) -- the same safe default as the Copilot
client, never auto-allowed.

OpenAI-compatible surface used by agent_runtime_helpers:
    CodeBuddyACPClient(...).chat.completions.create(model, messages, ...)

STATE: the native loop is validated end-to-end on this box 2026-10-08 (a gated
turn: CodeBuddy discovered the injected plobi MCP server, requested permission,
the bridge invoked the Plobi approval callback, the tool executed and the answer
was correct). What is NOT yet done is wiring this client into the provider
selection path (agent_runtime_helpers) — that is a separate, reviewable slice.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shlex
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

ACP_MARKER_BASE_URL = "acp://codebuddy"
_DEFAULT_TIMEOUT_SECONDS = 900.0


def _resolve_command() -> str:
    return (
        os.getenv("PLOBI_CODEBUDDY_ACP_COMMAND", "").strip()
        or os.getenv("CODEBUDDY_CLI_PATH", "").strip()
        or "codebuddy"
    )


def _resolve_args() -> list[str]:
    raw = os.getenv("PLOBI_CODEBUDDY_ACP_ARGS", "").strip()
    if not raw:
        return ["--acp"]
    return shlex.split(raw)


def _mcp_servers_entry() -> list[dict[str, Any]]:
    """Plobi's tool surface as one ACP mcpServers entry.

    Reuses plobi_quota_mcp_server.render_mcp_config so command/args stay in
    lockstep, then reshapes env into the [{name,value}] LIST the ACP transport
    expects (measured: a dict env is rejected by session/new; the list form is
    accepted and the tools register).

    Forces cwd + PYTHONPATH to the repo root: CodeBuddy spawns this server in ITS
    own cwd (often a temp dir), and ``python -m agent.*`` needs the repo root on
    the path before the module's own sys.path bootstrap can run. Without it the
    child imports nothing and advertises zero tools (measured 2026-10-08: the
    model then says "mcp__plobi__* isn't available"). render_mcp_config only
    passes PYTHONPATH through when the parent already set it, so we pin it here.
    """
    from agent.transports.plobi_quota_mcp_server import render_mcp_config

    repo_root = str(Path(__file__).resolve().parents[1])
    cfg = render_mcp_config()
    env_map = dict(cfg.get("env") or {})
    existing_pp = env_map.get("PYTHONPATH", "").strip()
    env_map["PYTHONPATH"] = (
        existing_pp + os.pathsep + repo_root if existing_pp else repo_root
    )
    env = [{"name": k, "value": str(v)} for k, v in env_map.items()]
    return [{
        "name": "plobi",
        "command": cfg["command"],
        "args": cfg["args"],
        "cwd": repo_root,
        "env": env,
    }]


def map_permission_outcome(options: list[dict[str, Any]], decision: str) -> dict[str, Any]:
    """Map a Plobi approval decision onto CodeBuddy's ACP permission options.

    decision in {once, session, always, deny}. Picks the closest optionId that is
    actually present; unknown/absent yields cancelled (deny). Never invents an
    option CodeBuddy did not offer.
    """
    if decision == "deny":
        return {"outcome": {"outcome": "cancelled"}}
    ids = [str(o.get("optionId")) for o in options]
    prefer = {
        "once": ["allow_once"],
        "session": ["allow_session", "allow_always"],
        "always": ["allow_always", "allow_session"],
    }.get(decision, ["allow_once"])
    for cand in prefer:
        if cand in ids:
            return {"outcome": {"outcome": "selected", "optionId": cand}}
    for o in options:
        if str(o.get("kind", "")).startswith("allow"):
            return {"outcome": {"outcome": "selected", "optionId": o.get("optionId")}}
    return {"outcome": {"outcome": "cancelled"}}


def ask_approval(callback: Optional[Callable[..., str]], tool_name: str, arguments: dict) -> str:
    """Consult the injected Plobi approver. Fail-closed when absent or it errors.

    callback matches Plobi's prompt_dangerous_approval shape:
    (command, description, *, allow_permanent=True) -> once|session|always|deny
    """
    if callback is None:
        logger.warning(
            "codebuddy-acp: permission request but no approver in-process -> deny "
            "(install an approval callback to allow delegated tools)"
        )
        return "deny"
    try:
        return callback(tool_name, json.dumps(arguments, ensure_ascii=False)[:200]) or "deny"
    except Exception:  # noqa: BLE001 - fail-closed on any approver error
        logger.exception("codebuddy-acp: approval callback raised; denying")
        return "deny"


def flatten_messages(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "user")
        content = m.get("content")
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if not content:
            continue
        parts.append(role + ":\n" + str(content))
    return "\n\n".join(parts)


def _ns(**kw: Any):
    class _S:
        def __init__(self, d):
            self.__dict__.update(d)
    return _S(kw)


class CodeBuddyACPClient:
    """Minimal OpenAI-client-compatible facade for the CodeBuddy ACP agent."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        acp_command: Optional[str] = None,
        acp_args: Optional[list[str]] = None,
        acp_cwd: Optional[str] = None,
        command: Optional[str] = None,
        args: Optional[list[str]] = None,
        approval_callback: Optional[Callable[..., str]] = None,
        mcp_servers: Optional[list[dict[str, Any]]] = None,
        **_: Any,
    ) -> None:
        self.base_url = base_url or ACP_MARKER_BASE_URL
        self._acp_command = acp_command or command or _resolve_command()
        self._acp_args = list(acp_args or args or _resolve_args())
        self._acp_cwd = str(Path(acp_cwd or os.getcwd()).resolve())
        self._approval_callback = approval_callback
        self._mcp_servers = mcp_servers
        self.chat = _ACPChatNamespace(self)
        self._active: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            proc, self._active = self._active, None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    def _send(self, proc, rid: int, method: str, params: dict) -> None:
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}) + "\n")
        proc.stdin.flush()

    def _respond(self, proc, rid, body: dict) -> None:
        try:
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid, **body}) + "\n")
            proc.stdin.flush()
        except Exception:  # noqa: BLE001
            pass

    def _run(self, prompt_text: str, *, timeout_seconds: float) -> str:
        try:
            proc = subprocess.Popen(
                [self._acp_command] + self._acp_args,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1, cwd=self._acp_cwd,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "Could not start CodeBuddy ACP command '" + self._acp_command + "'. "
                "Install CodeBuddy CLI or set PLOBI_CODEBUDDY_ACP_COMMAND/CODEBUDDY_CLI_PATH."
            ) from exc
        with self._lock:
            self._active = proc

        inbox: queue.Queue = queue.Queue()

        def _reader() -> None:
            for line in proc.stdout:  # type: ignore[union-attr]
                try:
                    inbox.put(json.loads(line))
                except Exception:  # noqa: BLE001
                    pass

        threading.Thread(target=_reader, daemon=True).start()

        text: list[str] = []
        state = {"rid": 0}
        pending: dict[int, Any] = {}

        def wait_for(rid: int, timeout: float):
            dl = time.monotonic() + timeout
            while time.monotonic() < dl:
                try:
                    msg = inbox.get(timeout=0.2)
                except queue.Empty:
                    continue
                # server -> host REQUEST (has both id and method): answer it
                if "id" in msg and "method" in msg:
                    self._handle_host_request(proc, msg)
                    continue
                # session/update notifications: accumulate agent text
                if msg.get("method") == "session/update":
                    upd = (msg.get("params") or {}).get("update") or {}
                    if str(upd.get("sessionUpdate", "")) == "agent_message_chunk":
                        c = upd.get("content") or {}
                        if isinstance(c, dict) and c.get("text"):
                            text.append(c["text"])
                    continue
                if msg.get("id") == rid:
                    return msg
            return None

        def call(method: str, params: dict, timeout: float):
            state["rid"] += 1
            rid = state["rid"]
            self._send(proc, rid, method, params)
            resp = wait_for(rid, timeout)
            if resp is None:
                raise TimeoutError("CodeBuddy ACP timed out on " + method)
            if "error" in resp:
                raise RuntimeError("CodeBuddy ACP " + method + " failed: " + str(resp["error"]))
            return resp.get("result") or {}

        try:
            call("initialize", {
                "protocolVersion": 1,
                "clientCapabilities": {"fs": {"readTextFile": True, "writeTextFile": True}},
                "clientInfo": {"name": "plobi-agent", "title": "Plobi Agent", "version": "0.0.0"},
            }, min(30.0, timeout_seconds))
            session = call("session/new", {
                "cwd": self._acp_cwd,
                "mcpServers": self._mcp_servers if self._mcp_servers is not None else _mcp_servers_entry(),
            }, min(90.0, timeout_seconds))
            sid = str(session.get("sessionId") or "").strip()
            if not sid:
                raise RuntimeError("CodeBuddy ACP did not return a sessionId.")
            call("session/prompt", {
                "sessionId": sid,
                "prompt": [{"type": "text", "text": prompt_text}],
            }, timeout_seconds)
        finally:
            self.close()

        return "".join(text)

    def _handle_host_request(self, proc, msg: dict) -> None:
        rid = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        if method == "session/request_permission":
            tool_call = params.get("toolCall") or {}
            name = str(tool_call.get("title") or "")
            raw = tool_call.get("rawInput") or {}
            decision = ask_approval(self._approval_callback, name, raw)
            self._respond(proc, rid, {"result": map_permission_outcome(params.get("options") or [], decision)})
        else:
            self._respond(proc, rid, {"error": {"code": -32601, "message": "host method unsupported: " + str(method)}})


class _ACPChatCompletions:
    def __init__(self, client: CodeBuddyACPClient) -> None:
        self._client = client

    def create(self, *, model: Optional[str] = None, messages: Optional[list] = None,
               timeout: Any = None, **_: Any):
        prompt_text = flatten_messages(messages or [])
        t = float(timeout) if isinstance(timeout, (int, float)) else _DEFAULT_TIMEOUT_SECONDS
        text = self._client._run(prompt_text, timeout_seconds=t)
        msg = _ns(content=text, tool_calls=None, reasoning=None, reasoning_content=None)
        choice = _ns(message=msg, finish_reason="stop")
        usage = _ns(prompt_tokens=0, completion_tokens=0, total_tokens=0)
        return _ns(choices=[choice], model=model or "codebuddy-acp", usage=usage)


class _ACPChatNamespace:
    def __init__(self, client: CodeBuddyACPClient) -> None:
        self.completions = _ACPChatCompletions(client)
