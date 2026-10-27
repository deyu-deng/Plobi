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




class ClineACPProfile(ProviderProfile):
    """The CLI names its own models; this profile never invents them.

    A seed list is how a wrong model reaches the picker: the CLI upgrades, the copy
    here does not, and the user picks a name that no longer exists. So the only
    source is the CLI's own ACP `session/new` answer, and an unsigned-in CLI returns
    nothing at all — the provider then also fails the configured gate, so it does not
    show up as a row of models that cannot be used.
    """

    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 8.0,
    ) -> list[str]:
        from plobi_cli.auth import external_process_login_state

        state = external_process_login_state("cline-acp", timeout=max(float(timeout), 20.0))
        if not state.get("logged_in"):
            return []

        return [str(model) for model in (state.get("models") or [])]


cline_acp = ClineACPProfile(
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
    # No seed list on purpose: see the profile class above.
    fallback_models=(),
)

register_provider(cline_acp)
