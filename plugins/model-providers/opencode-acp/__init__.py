"""OpenCode free tier via the OpenCode CLI's ACP server.

Why this is a process provider and not an HTTP key:

OpenCode Zen's free models are gated to the OpenCode client itself. A bare
OpenAI-compatible POST gets `403 FreeTierError: OpenCode's free tier can only be
used from within OpenCode` unless the request carries OpenCode's own session
headers and its full tool schema. Copying those would be impersonation, and it
would break whenever OpenCode changes its internal protocol. Driving the real
`opencode acp` process is the honest route: OpenCode owns the free-tier
admission check, and Plobi only speaks ACP to it.

The ACP client already used for GitHub Copilot is generic enough for this
(initialize → session/new → session/prompt). Requirements:
  - `opencode` installed on PATH (or PLOBI_OPENCODE_ACP_COMMAND)
  - at least one free model selected; the free pool rotates, so this list is a
    seed and the live `/models` catalog can replace it when available.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from providers import register_provider
from providers.base import ProviderProfile


class OpenCodeACPProfile(ProviderProfile):
    """OpenCode free models; ask the installed CLI for its own catalog."""

    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 15.0,
    ) -> list[str] | None:
        command = os.environ.get("PLOBI_OPENCODE_ACP_COMMAND", "").strip()
        if not command:
            command = shutil.which("opencode") or ""
        if not command:
            try:
                from plobi_cli.auth import _opencode_desktop_cli

                command = _opencode_desktop_cli()
            except Exception:
                command = ""
        if not command:
            return None
        try:
            done = subprocess.run(
                [command, "models"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=max(2.0, float(timeout)),
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if done.returncode != 0:
            return None
        ids: list[str] = []
        for line in (done.stdout or "").splitlines():
            model = line.strip()
            if not model:
                continue
            if model.startswith("opencode/"):
                model = model[len("opencode/") :]
            # Free-tier admission belongs to OpenCode; only advertise the
            # channels that are actually free, never the paid ones.
            if model.endswith("-free") or model == "big-pickle":
                if model not in ids:
                    ids.append(model)
        return ids or None


opencode_acp = OpenCodeACPProfile(
    name="opencode-acp",
    aliases=("opencode-free", "opencode-cli", "zen-free"),
    api_mode="chat_completions",
    display_name="OpenCode Free",
    description="OpenCode free models, driven through the local OpenCode ACP process",
    signup_url="https://opencode.ai/",
    env_vars=(),
    base_url="acp://opencode",
    auth_type="external_process",
    supports_health_check=False,
    fallback_models=(
        "beam-free",
        "big-pickle",
        "deepseek-v4.1-flash-free",
        "flint-40b-free",
        "glm-5.3-flash-free",
        "mimo-v2.6-flash-free",
        "nemotron-3-ultra-free",
        "nemotron-3.5-lightning-free",
        "nemotron-3.5-super-vl-free",
    ),
)

register_provider(opencode_acp)
