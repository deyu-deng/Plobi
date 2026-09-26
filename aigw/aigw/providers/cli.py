"""Spawn-based (opendesign-style) provider base class.

Instead of reverse-engineering a desktop app's private API or scraping its tokens,
a CliProvider drives a *locally-installed, already-authenticated* coding-agent CLI
as a subprocess -- the same pattern Open Design uses to drive claude/codex/
cursor-agent/plobi. The CLI consumes its own quota; we only do process I/O and
translate the result into the OpenAI ChatCompletion shape. Zero reverse-
engineering, minimal ToS risk.

Subclasses / config only need to declare:
  - binary        : command on PATH (or absolute path / script file)
  - interpreter   : optional (e.g. python) used to run a script `binary`
  - prompt_flag   : argv flag that carries the prompt            (e.g. "-p")
  - model_flag    : optional argv flag for the model             (e.g. "--model")
  - served_models : tuple of unified model ids this provider answers
  - model_map     : unified id -> CLI model string
  - extra_args    : fixed argv appended to every invocation

The conversation is flattened into a single prompt and sent statelessly; the CLI
gets full context each turn (correct for a gateway, which is itself stateless).
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from .base import Account, Capabilities, Credential, Provider, UpstreamError


def _as_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return str(content)


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def _chunk(cid: str, model: str, text=None, finish=None) -> dict:
    delta = {} if text is None else {"content": text}
    return {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


class CliProvider(Provider):
    """Generic subprocess-driven provider. Override flags / MODE_MAP in subclass."""

    name = "cli"
    served_models: tuple[str, ...] = ()
    # Stateless spawn: no native tool protocol, no vision, no embeddings.
    # Stream is "fake" (whole stdout chunked) but clients may still request stream.
    default_capabilities = Capabilities(
        stream=True,
        tools=False,
        vision=False,
        embeddings=False,
        sessionful=False,
        compliance="compliant",
    )

    # --- config knobs (subclass defaults, overridable per provider in config) ---
    binary: str = ""
    interpreter: str | None = None
    prompt_flag: str = "-p"
    model_flag: str = "--model"
    extra_args: list[str] = []
    timeout: float = 120.0
    cwd: str | None = None
    model_map: dict[str, str] = {}

    def __init__(self, config, http):
        super().__init__(config, http)
        self.binary = config.get("binary", self.binary)
        self.interpreter = config.get("interpreter", self.interpreter)
        self.prompt_flag = config.get("prompt_flag", self.prompt_flag)
        self.model_flag = config.get("model_flag", self.model_flag)
        self.extra_args = list(config.get("extra_args", self.extra_args))
        self.timeout = float(config.get("timeout", self.timeout))
        self.cwd = config.get("cwd") or None
        self.model_map = {**self.model_map, **config.get("model_map", {})}
        self.env_extra = {k: str(v) for k, v in config.get("env", {}).items()}
        if config.get("models"):
            self.served_models = tuple(config["models"])
        self.binary_path: str | None = None

    # --- discovery -------------------------------------------------------
    def _binary_present(self) -> bool:
        if self.interpreter:
            return Path(self.binary).is_file()
        return shutil.which(self.binary) is not None or Path(self.binary).is_file()

    async def discover_accounts(self) -> list[Account]:
        import logging

        if not self._binary_present():
            # Non-fatal: gateway still boots; this provider simply has no
            # schedulable account (the scheduler skips providers with none).
            logging.getLogger("aigw").warning(
                "[%s] binary '%s' not found on PATH — provider disabled until the "
                "CLI is installed & on PATH.",
                self.name,
                self.binary,
            )
            self.accounts = []
            return []

        if self.interpreter:
            self.binary_path = self.binary
        else:
            wp = shutil.which(self.binary)
            self.binary_path = wp if wp else (self.binary if Path(self.binary).is_file() else None)

        declared = self.config.get("accounts") or [{"id": f"{self.name}-default"}]
        accs: list[Account] = []
        for i, a in enumerate(declared):
            accs.append(
                Account(
                    id=a.get("id", f"{self.name}-{i}"),
                    provider=self.name,
                    label=a.get("label", f"{self.binary} seat"),
                    # auth is owned by the CLI itself; per-account CLI args ride in
                    # cred.extra so build_invocation() can pick them up.
                    cred=Credential(access_token="cli", extra={"args": list(a.get("args", []))}),
                )
            )
        self.accounts = accs
        return accs

    async def ensure_fresh(self, acc: Account) -> None:
        # The CLI owns its own session/auth; nothing to refresh here.
        if not self.binary_path:
            raise UpstreamError(401, f"{self.name}: CLI binary missing", reauth=True)

    # --- prompt / invocation --------------------------------------------
    def format_prompt(self, oai_req: dict) -> str:
        """Flatten the OpenAI conversation into one text prompt (stateless)."""
        parts = []
        for m in oai_req.get("messages", []):
            role = m.get("role")
            text = _as_text(m.get("content"))
            if not text:
                continue
            if role == "system":
                parts.append(f"[system]\n{text}")
            elif role == "user":
                parts.append(f"[user]\n{text}")
            elif role == "assistant":
                parts.append(f"[assistant]\n{text}")
            else:
                parts.append(text)
        return "\n\n".join(parts)

    def _cli_model(self, oai_model: str) -> str:
        if oai_model in self.model_map:
            return self.model_map[oai_model]
        if "/" in oai_model:
            return oai_model.split("/", 1)[1]
        return oai_model

    def build_invocation(self, acc: Account, oai_req: dict) -> list[str]:
        argv: list[str] = []
        if self.interpreter:
            argv.append(self.interpreter)
        argv.append(self.binary_path or self.binary)
        argv += list(self.extra_args)
        argv += list(acc.cred.extra.get("args", []))
        argv += [self.prompt_flag, self.format_prompt(oai_req)]
        model = self._cli_model(oai_req["model"])
        # Only append a model flag when a real model was resolved — some CLIs
        # error on an empty/None --model, and many default sensibly without it.
        if self.model_flag and model:
            argv += [self.model_flag, model]
        return argv

    # --- response parsing ------------------------------------------------
    def parse_response(self, stdout: str, oai_req: dict) -> dict:
        text = _strip_ansi(stdout).strip()
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": oai_req["model"],
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": text},
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    # --- execution -------------------------------------------------------
    async def _spawn(self, acc: Account, oai_req: dict):
        argv = self.build_invocation(acc, oai_req)
        env = dict(os.environ)
        env.update(self.env_extra)
        return await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
            env=env,
        )

    async def chat(self, acc: Account, oai_req: dict) -> dict:
        try:
            proc = await self._spawn(acc, oai_req)
        except FileNotFoundError as e:
            raise UpstreamError(503, f"{self.name}: cannot launch CLI: {e}")
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise UpstreamError(
                504,
                f"{self.name}: CLI timed out after {self.timeout}s",
                retryable=True,
                cooldown=10.0,
            )
        if proc.returncode != 0:
            err = (stderr or b"").decode(errors="replace").strip()[:500]
            raise UpstreamError(502, f"{self.name}: CLI exited {proc.returncode}: {err}")
        return self.parse_response(stdout.decode(errors="replace"), oai_req)

    async def chat_stream(self, acc: Account, oai_req: dict) -> AsyncIterator[dict]:
        try:
            proc = await self._spawn(acc, oai_req)
        except FileNotFoundError as e:
            raise UpstreamError(503, f"{self.name}: cannot launch CLI: {e}")
        cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        model = oai_req["model"]
        stdout = proc.stdout
        try:
            assert stdout is not None
            while True:
                try:
                    line = await asyncio.wait_for(stdout.readline(), self.timeout)
                except asyncio.TimeoutError:
                    raise UpstreamError(
                        504, f"{self.name}: CLI stream timed out", retryable=True, cooldown=10.0
                    )
                if not line:
                    break
                text = _strip_ansi(line.decode(errors="replace"))
                if text:
                    yield _chunk(cid, model, text)
            # flush any trailing bytes emitted without a final newline
            rest = await stdout.read()
            if rest:
                text = _strip_ansi(rest.decode(errors="replace"))
                if text:
                    yield _chunk(cid, model, text)
            try:
                await asyncio.wait_for(proc.wait(), 5.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        finally:
            if proc.returncode is None:
                with_proc = True
                try:
                    proc.kill()
                except Exception:
                    with_proc = False
                if with_proc:
                    await proc.wait()
        yield _chunk(cid, model, None, finish="stop")
