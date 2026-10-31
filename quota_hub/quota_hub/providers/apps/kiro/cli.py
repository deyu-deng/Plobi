"""Kiro (AWS) — its own streaming API first, the spawn of `kiro-cli` as the floor.

Chat (compliant):

    kiro-cli chat --no-interactive --trust-all-tools "<prompt>"
    kiro-cli chat --no-interactive --trust-all-tools --model <model> "<prompt>"

``KIRO_API_KEY`` (ksk_…) is honoured by the CLI for headless runs; otherwise the
CLI uses its own browser/device login. We never read or copy its credential.

Quota: Kiro exposes ``/usage`` only inside an interactive V3 session; there is no
headless usage command in 1.x. So this leg deliberately leaves ``acc.quota``
empty instead of fabricating a percentage. Chat still consumes the same plan.

Install : npm i -g kiro-cli   (or curl -fsSL https://cli.kiro.dev/install | bash)
Verify  : kiro-cli --version ; kiro-cli chat --no-interactive "say hi"
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time

from ...base import Account, Capabilities
from ...cli import CliProvider, _strip_ansi
from .http import KiroHttpMixin

_HELP_TIMEOUT = 20.0
_WHOAMI_TTL = 600.0

# stdout is designed for a terminal; drop the frame/UI noise, keep the answer.
_UI_LINE = re.compile(r"^\s*(?:[╭╰│─╮╯▔▁]|>\s)")


def _clean_kiro_stdout(stdout: str) -> str:
    text = _strip_ansi(stdout)
    lines = [line for line in text.splitlines() if not _UI_LINE.match(line)]
    return "\n".join(lines).strip()


def _models_from_payload(data) -> list[str]:
    rows = data.get("models") if isinstance(data, dict) else data
    if isinstance(rows, dict):
        return [str(k).strip() for k in rows if str(k).strip()]
    if not isinstance(rows, list):
        return []
    out: list[str] = []
    for row in rows:
        if isinstance(row, str):
            value = row
        elif isinstance(row, dict):
            value = row.get("modelId") or row.get("model_id") or row.get("id") or row.get("name")
        else:
            continue
        if isinstance(value, str) and value.strip():
            out.append(value.strip())
    return list(dict.fromkeys(out))



MCP_SERVER_NAME = "plobi"
MCP_ENV_TTL = 300.0


def parse_stream_json(stdout: str) -> dict:
    """Kiro's `--output-format stream-json` answer (ACP-shaped JSONL) → what we need.

    Tolerant by design: a run that prints no JSON Lines at all is the old text shape, and
    we must not turn a format change into an empty answer.
    """
    chunks: list[str] = []
    tool_events: list[dict] = []
    credits = 0.0
    context_percent = None
    final_text = ""
    stop_reason = ""
    saw_json = False
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        saw_json = True
        data = event.get("data") or {}
        kind = event.get("type")
        if kind == "sessionUpdate":
            update = data.get("update") or {}
            if update.get("sessionUpdate") == "agent_message_chunk":
                text = ((update.get("content") or {}).get("text")) or ""
                if text:
                    chunks.append(text)
            elif str(update.get("sessionUpdate") or "").startswith("tool_call"):
                tool_events.append(update)
        elif kind == "metadata":
            if data.get("contextUsagePercentage") is not None:
                context_percent = data["contextUsagePercentage"]
            for row in data.get("meteringUsage") or []:
                if isinstance(row, dict) and str(row.get("unit", "")).startswith("credit"):
                    try:
                        credits += float(row.get("value") or 0.0)
                    except (TypeError, ValueError):
                        pass
        elif kind == "runFinished":
            final_text = str(data.get("finalText") or "")
            stop_reason = str(data.get("stopReason") or data.get("status") or "")
    text = "".join(chunks) or final_text
    return {
        "text": text if saw_json else _clean_kiro_stdout(stdout),
        "tool_events": tool_events,
        "credits": credits,
        "context_percent": context_percent,
        "stop_reason": stop_reason,
        "shaped": saw_json,
    }


class KiroSpawnProvider(CliProvider):
    name = "kiro_cli"
    display_name = "Kiro"
    binary = "kiro-cli"
    # Verified from `kiro-cli login --help` on this machine 2026-10-26
    # (`kiro-cli auth` does not exist — it errors "unrecognized subcommand").
    # Kiro is a login-only candidate for the model picker, not a tool-capable leg.
    login_args = ("login",)
    prompt_flag = ""  # prompt is positional
    args_template = [
        "chat",
        "--no-interactive",
        "--trust-all-tools",
        "--output-format",
        "stream-json",
        "{model_flag}",
        "{prompt}",
    ]
    model_flag = "--model"
    timeout = 300.0
    isolate_workspace = True
    #: Its stream is JSONL, so it is forwarded line by line (`stream_text`) instead of
    #: being run whole and handed back as one blob.
    stream_from_chat = False
    #: Discovered ids keep the same prefix the seed and config use, so the
    #: picker shows one namespace for one product.
    model_prefix = "kiro"
    served_models = ("kiro/default",)
    model_map = {"kiro/default": ""}
    #: The last ACP-shaped run, for the quota fields and for the tool evidence.
    last_run: dict = {}
    #: The CLI's own answer about its session. `kiro-cli login` refuses when already
    #: signed in, so a click cannot be the only way to learn the truth.
    whoami_argv = ("whoami",)
    #: Per-turn streaming state (reset in `chat_stream`, filled by `stream_text`).
    _streamed_text = False
    _stream_credits = 0.0
    _stream_context = None

    async def chat_stream(self, acc, oai_req):
        """Same turn, but the model's token groups leave as they arrive.

        Measured 2026-10-29: this leg used to run the whole turn and emit one chunk at the
        end, so a client that asked for SSE got a single delta — the CLI was streaming all
        along (`--output-format stream-json` carries one `agent_message_chunk` per group).
        """
        self._streamed_text = False
        self._stream_credits = 0.0
        self._stream_context = None
        async for chunk in super().chat_stream(acc, oai_req):
            yield chunk

    def stream_text(self, raw: bytes, acc=None) -> str:
        """One JSONL line → the assistant text only; everything else is collected.

        The stream also carries tool calls, credit metering and a final snapshot. None of
        that belongs in the token stream: metering goes onto the account (so the card's
        credit line keeps working on streamed turns), the rest is ignored here and still
        parsed in full by `parse_stream_json` on the non-streaming path.
        """
        line = _strip_ansi(raw.decode(errors="replace")).strip()
        if not line.startswith("{"):
            return ""
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return ""
        if not isinstance(event, dict):
            return ""
        data = event.get("data") or {}
        kind = event.get("type")
        if kind == "sessionUpdate":
            update = data.get("update") or {}
            if update.get("sessionUpdate") == "agent_message_chunk":
                text = str(((update.get("content") or {}).get("text")) or "")
                if text:
                    self._streamed_text = True
                return text
            return ""
        if kind == "metadata":
            percent = data.get("contextUsagePercentage")
            if percent is not None:
                self._stream_context = percent
            for row in data.get("meteringUsage") or []:
                if isinstance(row, dict) and str(row.get("unit", "")).startswith("credit"):
                    try:
                        self._stream_credits += float(row.get("value") or 0.0)
                    except (TypeError, ValueError):
                        pass
            if acc is not None:
                acc.quota["credits_used_last_turn"] = round(self._stream_credits, 4)
                if self._stream_context is not None:
                    acc.quota["context_percent"] = self._stream_context
            return ""
        if kind == "runFinished":
            # Only when the run never sent a chunk — otherwise the answer arrives twice.
            return "" if self._streamed_text else str(data.get("finalText") or "")
        return ""

    def credential_health(self) -> tuple[bool, str]:
        live, reason = super().credential_health()
        if live:
            return True, "ok"
        if self.signed_in_over_cli():
            return True, "ok"
        return False, reason

    def signed_in_over_cli(self, ttl: float = _WHOAMI_TTL) -> bool:
        """Ask the CLI whether it has a session, and remember the answer for a while.

        Measured 2026-10-28: the owner IS signed into Kiro (`Logged in with GitHub`), but
        `login_ledger` only records sign-ins *we* started — so the card told him to log in
        and the picker hid a leg that works. The login ledger stays the stronger evidence;
        this is a credential probe, not a guess: it reads the CLI's own verdict, spends no
        quota, and keeps nothing it prints (the e-mail in that output is not stored).
        """
        now = time.time()
        cached = getattr(self, "_whoami_seen", None)
        if cached is not None and now - cached[0] < ttl:
            return cached[1]
        binary = self.binary_path or shutil.which(self.binary)
        live = False
        if binary:
            try:
                proc = subprocess.run(
                    [binary, *self.whoami_argv],
                    capture_output=True,
                    text=True,
                    timeout=8.0,
                    env=dict(os.environ, **self.env_extra),
                )
                live = proc.returncode == 0 and proc.stdout.strip().lower().startswith("logged in")
            except (OSError, subprocess.TimeoutExpired, ValueError):
                live = False
        self._whoami_seen = (now, live)
        return live

    def parse_response(self, stdout: str, oai_req: dict) -> dict:
        parsed = parse_stream_json(stdout)
        # The credit cost arrives in the same stream: this is the leg's only honest
        # spend signal, so it goes on the account rather than being thrown away.
        self.last_run = parsed
        if self.accounts:
            acc = self.accounts[0]
            if parsed["credits"]:
                acc.quota["credits_used_last_turn"] = round(parsed["credits"], 4)
            if parsed["context_percent"] is not None:
                acc.quota["context_percent"] = round(float(parsed["context_percent"]), 2)
        return super().parse_response(parsed["text"], oai_req)

    # --- the tool surface: registered, not claimed ----------------------
    def capabilities(self) -> Capabilities:
        caps = self.default_capabilities.as_dict()
        # `tools` is only true once OUR MCP server is in Kiro's config, i.e. once a tool
        # call can actually come back into Plobi. A text-only transport must stay out of
        # the picker (the gate is the owner's ruling, not our preference).
        caps["tools"] = bool(self.mcp_registered())
        return Capabilities(**caps)

    def mcp_config(self) -> tuple[str, list[str], dict]:
        """The command Kiro should run to reach Plobi's own tool surface."""
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
        home = os.environ.get("PLOBI_HOME", "").strip()
        env = {"PYTHONPATH": repo_root}
        if home:
            env["PLOBI_HOME"] = home
        return sys.executable, ["-m", "agent.transports.plobi_quota_mcp_server"], env

    def mcp_registered(self, ttl: float = MCP_ENV_TTL) -> bool:
        """Is `plobi` in Kiro's MCP config? Register once if the leg is allowed to."""
        now = time.time()
        cached = getattr(self, "_mcp_seen", None)
        if cached is not None and now - cached[0] < ttl:
            return cached[1]
        wired = self._mcp_listed()
        if not wired and self.config.get("register_mcp", True):
            self._mcp_add()
            wired = self._mcp_listed()
        self._mcp_seen = (now, wired)
        return wired

    def _run_mcp(self, argv: list[str]) -> str:
        binary = self.binary_path or shutil.which(self.binary)
        if not binary:
            return ""
        try:
            proc = subprocess.run(
                [binary, *argv], capture_output=True, text=True, timeout=25.0,
                env=dict(os.environ, **self.env_extra),
            )
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return ""
        return (proc.stdout or "") + (proc.stderr or "")

    def _mcp_listed(self) -> bool:
        out = self._run_mcp(["mcp", "list"])
        if out.strip().startswith(("[", "{")):
            try:
                data = json.loads(out[out.strip().index(out.strip()[0]):])
            except ValueError:
                data = None
            if data is not None:
                return MCP_SERVER_NAME in json.dumps(data)
        return re.search(r"(?<![A-Za-z0-9_])" + MCP_SERVER_NAME + r"(?![A-Za-z0-9_])", out) is not None

    def _mcp_add(self) -> None:
        command, args, env = self.mcp_config()
        self._run_mcp([
            "mcp", "add", "--name", MCP_SERVER_NAME, "--scope", "global", "--force",
            "--command", command, "--args", json.dumps(args),
            *[item for pair in env.items() for item in ("--env", f"{pair[0]}={pair[1]}")],
        ])


    async def refresh_models(self, acc: Account) -> list[str] | None:
        """Ask the CLI for its own model list (``--list-models --format json``).

        Retried once, and the reason is logged when both attempts fail. Measured
        2026-10-29: at cold boot this call sometimes came back unusable while the same
        command run by hand answered in 1.8 s — and because stderr was discarded the
        failure was invisible, so the leg kept serving its one-row seed and the picker
        offered a model literally named `default`.
        """
        if not self.binary_path:
            return None
        reason = ""
        for attempt in (1, 2):
            short, reason = await self._fetch_models_once()
            if short:
                prefix = self.model_prefix or self.name
                self.served_models = tuple(f"{prefix}/{m}" for m in short)
                self.model_map = {f"{prefix}/{m}": m for m in short}
                self.models_refreshed = True
                return short
            if attempt == 1:
                await asyncio.sleep(0.6)
        logging.getLogger("quota_hub").warning(
            "%s model refresh failed twice (%s); keeping the seed catalog", self.name, reason
        )
        return None

    async def _fetch_models_once(self) -> tuple[list[str], str]:
        """One `--list-models` attempt: (short ids, why it failed or "")."""
        argv = [self.binary_path or self.binary, "chat", "--list-models", "--format", "json"]
        env = dict(os.environ)
        env.update(self.env_extra)
        if self.cwd:
            os.makedirs(self.cwd, exist_ok=True)
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_HELP_TIMEOUT)
        except OSError as exc:
            return [], f"spawn: {exc}"
        except asyncio.TimeoutError:
            return [], f"no answer in {_HELP_TIMEOUT:.0f}s"
        if proc.returncode != 0:
            return [], f"exit {proc.returncode}: {stderr.decode(errors='replace').strip()[:200]}"
        raw = _strip_ansi(stdout.decode(errors="replace")).strip()
        # The payload is an OBJECT whose first value is an array: cutting at the first
        # `[` — which is what this used to do — leaves a trailing `}` and the parse fails,
        # so the leg silently kept its one-row seed. Try the whole body first.
        data = None
        for candidate in (raw, raw[raw.find("["):] if "[" in raw else "", raw[raw.find("{"):] if "{" in raw else ""):
            if not candidate:
                continue
            try:
                data = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            break
        if data is None:
            return [], f"unparseable output: {raw[:120]}"
        short = _models_from_payload(data)
        if not short:
            return [], f"no model ids in output: {raw[:120]}"
        return short, ""


class KiroCliProvider(KiroHttpMixin, KiroSpawnProvider):
    """One card, two mouths: Kiro's streaming API in front, the spawn path as the floor.

    The split above is deliberate. `KiroCliProvider` used to define the spawn methods
    itself, so a mixin placed in front of it in the bases was shadowed by its own class
    body and every override silently did nothing (measured 2026-10-31: catalogue returned
    None without a request). Putting the spawn implementation one class *behind* the mixin
    is what makes the mixin's `super()` reach it.

    Which mouth is used stays a config decision - `transport: api` in the hub's config, the
    same convention the registry already uses for `transport: acp` - so adding or rolling
    back a leg never means editing a table.
    """
