"""aigw — the local quota gateway (本地额度网关 / Local Quota Hub).

``aigw`` is a user-run, OpenAI-compatible gateway that aggregates quota from
desktop quota apps (Antigravity / Cursor / Workbuddy) and serves it under one
local endpoint.  Plobi's L2 default routes, the agenda-secretary template and
13 agent profiles already carry ``provider: aigw``; before this profile existed
that name was unresolvable and ``plobi_cli.auth.resolve_provider()`` raised
``Unknown provider 'aigw'.``  Registering the profile here makes the name a
first-class runtime provider without touching the resolver.

Boundaries (ARCH ruling 45):

* The machine-readable identifier stays ``aigw`` — logs, config, ``doctor.py``
  row ids and route values all keep it.  Only *display* text uses the outward
  name (``display_name`` below); the UI-layer renaming/regrouping is a separate
  cut, so the slug still reaches picker rows via ``plobi_cli/models.py``.
* No new env vars: the endpoint and key come from the same source of truth the
  agents layer already uses (``plobi.agents.registry.aigw_base_url()`` /
  ``aigw_api_key()``, reading ``PLOBI_AIGW_URL`` / ``AIGW_API_KEY`` and their
  legacy quota aliases).  We deliberately do not re-implement that chain here.
* The gateway currently only fronts ``mock/echo`` channels (R-047 defers the
  real quota capture), so nothing here claims a live commercial channel.
"""

from typing import Any
from urllib.parse import urlparse

from providers import register_provider
from providers.base import ProviderProfile

from plobi.agents.registry import aigw_api_key, aigw_base_url

# Env var *names* already defined by the agents layer — declared here so the
# generic api-key credential path (auth.json pool, ~/.plobi/.env, shell export)
# can find them.  Order mirrors aigw_base_url()/aigw_api_key() precedence.
_KEY_VARS = ("AIGW_API_KEY", "PLOBI_QUOTA_AIGW_KEY")
_URL_VARS = ("PLOBI_AIGW_URL", "PLOBI_QUOTA_AIGW_URL")

# Resolved once at plugin-import time (discovery is lazy, so the process env is
# already final by then). Trailing slash stripped by aigw_base_url().
_BASE_URL = aigw_base_url()


def _host_and_port(base_url: str) -> str:
    """Return ``host:port`` for *base_url*, or "" when it has no netloc.

    Declaring this as ``hostname`` instead of letting ``ProviderProfile``
    derive the bare host keeps the URL→provider reverse map in
    ``agent/model_metadata.py`` port-accurate.  A bare ``127.0.0.1`` key would
    be substring-matched against *any* local endpoint, so a user's Ollama on
    ``127.0.0.1:11434`` would suddenly infer as ``aigw`` — registering this
    profile must not change what other providers resolve to.
    """
    parsed = urlparse(base_url)
    return parsed.netloc.lower()


class AigwProfile(ProviderProfile):
    """Local quota gateway — OpenAI-compatible endpoint on loopback."""

    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 8.0,
    ) -> list[str] | None:
        """List gateway models, filling in the gateway's own endpoint/key.

        The gateway is a local service with a fixed development key rather than
        a per-user secret, so callers that pass nothing still get the canonical
        values from the agents layer (single source of truth — no second copy
        of the env parsing here).
        """
        return super().fetch_models(
            api_key=api_key or aigw_api_key(),
            base_url=base_url or self.base_url,
            timeout=timeout,
        )


aigw = AigwProfile(
    name="aigw",
    aliases=(),  # keep the alias table untouched; the name is already `aigw`
    api_mode="chat_completions",
    display_name="Local Quota Hub",
    description=(
        "Local quota gateway — desktop quota apps (Antigravity / Cursor / "
        "Workbuddy) behind one OpenAI-compatible endpoint"
    ),
    env_vars=_KEY_VARS + _URL_VARS,
    base_url=_BASE_URL,
    hostname=_host_and_port(_BASE_URL),
    auth_type="api_key",
    # Quota-backed channels are model-selected by the gateway; no curated
    # fallback list (the gateway's mock routes are not agentic models).
    supports_vision=False,
)

register_provider(aigw)
