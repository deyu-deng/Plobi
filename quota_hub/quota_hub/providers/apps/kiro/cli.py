"""Kiro CLI (AWS) — spawn-based chat; model catalog from the CLI itself.

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
import os
import re

from ...base import Account
from ...cli import CliProvider, _strip_ansi

_HELP_TIMEOUT = 20.0

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


class KiroCliProvider(CliProvider):
    name = "kiro_cli"
    binary = "kiro-cli"
    prompt_flag = ""  # prompt is positional
    args_template = [
        "chat",
        "--no-interactive",
        "--trust-all-tools",
        "{model_flag}",
        "{prompt}",
    ]
    model_flag = "--model"
    timeout = 300.0
    isolate_workspace = True
    stream_from_chat = True
    served_models = ("kiro/default",)
    model_map = {"kiro/default": ""}

    def parse_response(self, stdout: str, oai_req: dict) -> dict:
        text = _clean_kiro_stdout(stdout)
        return super().parse_response(text, oai_req)

    async def refresh_models(self, acc: Account) -> list[str] | None:
        """Ask the CLI for its own model list (``--list-models --format json``)."""
        if not self.binary_path:
            return None
        argv = [self.binary_path, "chat", "--list-models", "--format", "json"]
        env = dict(os.environ)
        env.update(self.env_extra)
        if self.cwd:
            os.makedirs(self.cwd, exist_ok=True)
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=self.cwd,
                env=env,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_HELP_TIMEOUT)
        except (OSError, ValueError, asyncio.TimeoutError):
            return None
        raw = _strip_ansi(stdout.decode(errors="replace")).strip()
        start = raw.find("[") if "[" in raw else raw.find("{")
        if start < 0:
            return None
        try:
            data = json.loads(raw[start:])
        except json.JSONDecodeError:
            return None
        short = _models_from_payload(data)
        if not short:
            return None
        prefix = self.model_prefix or self.name
        self.served_models = tuple(f"{prefix}/{m}" for m in short)
        self.model_map = {f"{prefix}/{m}": m for m in short}
        self.models_refreshed = True
        return short
