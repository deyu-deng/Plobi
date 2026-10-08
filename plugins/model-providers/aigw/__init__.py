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
* No new env vars: the endpoint and key names are declared once by the agents
  layer (``plobi.agents.registry.AIGW_KEY_ENV_VARS`` / ``AIGW_URL_ENV_VARS``) and
  imported here.
* One credential order (ruling 81): ``resolve_api_key_provider_credentials``
  asks the user's explicit sources first (auth.json pool, ``~/.plobi/.env``, shell
  export) and, when none of them carries a value, calls ``fallback_api_key()``
  below.  That hook is this profile's whole job on the credential question — it
  delegates to ``plobi.agents.registry.aigw_api_key()``, the single definition
  point for the gateway's env names, the key its own config declares and the
  shipped development default.  Nothing on this side re-reads the environment.
* The gateway currently only fronts ``mock/echo`` channels (R-047 defers the
  real quota capture), so nothing here claims a live commercial channel.
"""

from typing import Any
from urllib.parse import urlparse

from providers import register_provider
from providers.base import ProviderProfile

from plobi.agents.registry import (
    AIGW_KEY_ENV_VARS,
    AIGW_URL_ENV_VARS,
    aigw_api_key,
    aigw_base_url,
)

# Names owned by the agents layer; declared on the profile so the generic api-key
# credential path knows which vars to look for *before* it asks this profile for
# its fallback.
_KEY_VARS = AIGW_KEY_ENV_VARS
_URL_VARS = AIGW_URL_ENV_VARS

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

    def fallback_api_key(self) -> str:
        """The gateway's own answer when the user set no explicit credential.

        This is the declared fallback that
        ``plobi_cli.auth.resolve_api_key_provider_credentials`` reaches *last*, so
        the chat path and the model-probing path resolve through the same entry.
        The value comes from the agents layer; the resolution order and its labels
        live in ``plobi.agents.registry.aigw_api_key`` / ``aigw_credential_source``.
        """
        return aigw_api_key()


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
