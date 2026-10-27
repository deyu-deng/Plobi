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




class GeminiACPProfile(ProviderProfile):
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

        state = external_process_login_state("gemini-acp", timeout=max(float(timeout), 20.0))
        if not state.get("logged_in"):
            return []

        return [str(model) for model in (state.get("models") or [])]


gemini_acp = GeminiACPProfile(
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
    # No seed list on purpose: see the profile class above.
    fallback_models=(),
)

register_provider(gemini_acp)
