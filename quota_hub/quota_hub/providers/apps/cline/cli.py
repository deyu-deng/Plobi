"""Cline CLI — spawn-based chat leg.

Chat (compliant): the user's already-authenticated ``cline`` binary runs one task:

    cline --json "<prompt>" -m <model>

``--json`` streams NDJSON. We read the final ``run_result.text`` when present,
otherwise the last ``agent_event`` text. No auto-approve flag is passed: a chat
gateway must not silently let the CLI edit the workspace. It runs in a private
scratch cwd anyway.

Quota: Cline is a client for whatever provider the user configured (Cline
Provider / ClinePass / own key). There is no local unified quota endpoint, so
this leg leaves ``acc.quota`` empty instead of inventing one.

Install : npm i -g cline
Verify  : cline --version ; cline auth ; cline --json "say hi"
"""
from __future__ import annotations

import json
import re

from ...base import Account, UpstreamError
from ...cli import CliProvider

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _parse_cline_events(stdout: str) -> str:
    """Return the final answer text from Cline's NDJSON event stream."""
    answer = ""
    for line in stdout.splitlines():
        line = _ANSI.sub("", line).strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        kind = str(event.get("type") or "")
        if kind == "run_result":
            text = event.get("text")
            if not isinstance(text, str):
                result = event.get("result")
                text = result.get("text") if isinstance(result, dict) else ""
            if isinstance(text, str) and text.strip():
                answer = text.strip()
        elif kind == "agent_event":
            payload = event.get("event")
            if isinstance(payload, dict):
                text = payload.get("text")
                if isinstance(text, str) and text.strip():
                    answer = text.strip()
    return answer


class ClineCliProvider(CliProvider):
    name = "cline_cli"
    display_name = "Cline"
    binary = "cline"
    prompt_flag = ""  # prompt is positional
    args_template = ["--json", "{model_flag}", "{prompt}"]
    model_flag = "-m"
    timeout = 300.0
    isolate_workspace = True
    stream_from_chat = True
    served_models = ("cline/default",)
    model_map = {"cline/default": ""}
    # Verified from `cline auth --help` on this machine 2026-10-26:
    #   Usage: cline auth [options] [provider]
    # Run with no provider argument it starts its own sign-in and prints the URL.
    login_args = ("auth",)

    def parse_response(self, stdout: str, oai_req: dict) -> dict:
        text = _parse_cline_events(stdout)
        if not text:
            raise UpstreamError(502, "cline: --json produced no answer event")
        return super().parse_response(text, oai_req)
