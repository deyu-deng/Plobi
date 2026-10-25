"""Gemini CLI free tier via ACP.

The Gemini CLI has a free tier tied to a Google login (`gemini` first run) and
speaks ACP with `--acp`. Driving the installed CLI reuses that login without an
API key and keeps the free-tier admission on Google's side.

Install : npm i -g @google/gemini-cli
Verify  : gemini --version ; gemini --acp
"""

from __future__ import annotations

from providers import register_provider
from providers.base import ProviderProfile


gemini_acp = ProviderProfile(
    name="gemini-acp",
    aliases=("gemini-free", "gemini-cli"),
    api_mode="chat_completions",
    display_name="Gemini CLI",
    description="Gemini CLI free tier, driven through the local ACP process",
    signup_url="https://github.com/google-gemini/gemini-cli",
    env_vars=(),
    base_url="acp://gemini",
    auth_type="external_process",
    supports_health_check=False,
    fallback_models=(
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-3-pro",
        "gemini-3-flash",
    ),
)

register_provider(gemini_acp)
