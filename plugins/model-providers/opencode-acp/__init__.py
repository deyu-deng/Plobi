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

from providers import register_provider
from providers.base import ProviderProfile


opencode_acp = ProviderProfile(
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
        "big-pickle",
        "deepseek-v4-flash-free",
        "mimo-v2.5-free",
        "ling-3.0-flash-fin-free",
        "nemotron-3-ultra-free",
        "nemotron-3.5-lightning-free",
        "muse-spark-1.3-contributor-free",
        "muse-spark-1.2-contributor-free",
        "jev-1.13-free",
        "step-5-preview-free",
    ),
)

register_provider(opencode_acp)
