"""Cline via its Agent Client Protocol process.

Cline is a VS Code/JetBrains extension, a CLI and an SDK sharing one agent core.
Its free/provider pool lives behind the Cline account, not a public key-only
HTTP gateway; the honest way to expose it to Plobi is the CLI's ACP mode:

    cline --acp

`cline auth` keeps the account credentials; Plobi never reads them. If the CLI
is absent the provider auto-disables. The paid ClinePass subscription is a
separate provider (`clinepass`) with a normal OpenAI-compatible API key.

Install : npm i -g cline
Verify  : cline --version ; cline --acp
"""

from __future__ import annotations

from providers import register_provider
from providers.base import ProviderProfile


cline_acp = ProviderProfile(
    name="cline-acp",
    aliases=("cline-cli", "cline-free"),
    api_mode="chat_completions",
    display_name="Cline",
    description="Cline CLI (ACP) — uses the account signed in through `cline auth`",
    signup_url="https://cline.bot/",
    env_vars=(),
    base_url="acp://cline",
    auth_type="external_process",
    supports_health_check=False,
    # Free channels Cline documents; the CLI's account decides what it can serve.
    fallback_models=(
        "deepseek/deepseek-v4-flash",
        "minimax/minimax-m3",
    ),
)

register_provider(cline_acp)
