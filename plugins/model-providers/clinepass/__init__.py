"""ClinePass provider profile.

ClinePass is Cline's flat-rate subscription for open-weight coding models,
served through an OpenAI-compatible endpoint. The key comes from Cline's
Settings → API Keys (or the Cline CLI's own login); the account also needs an
active ClinePass subscription for these models to answer.

Endpoint : https://api.cline.bot/api/v1
Docs     : https://docs.cline.bot/ (ClinePass)
"""

from __future__ import annotations

from providers import register_provider
from providers.base import ProviderProfile


clinepass = ProviderProfile(
    name="clinepass",
    aliases=("cline", "cline-pass", "clinepass-sub"),
    api_mode="chat_completions",
    display_name="ClinePass",
    description="ClinePass subscription (open-weight coding models, OpenAI-compatible)",
    signup_url="https://cline.bot/",
    env_vars=("CLINE_API_KEY", "CLINEPASS_API_KEY"),
    base_url="https://api.cline.bot/api/v1",
    auth_type="api_key",
    # Seed only; the live /models endpoint replaces it once a key is present.
    # Keep the ids that ClinePass documents and OpenCode Codex Router publishes.
    fallback_models=(
        "glm-5.2",
        "kimi-k3",
        "kimi-k2.7-code",
        "kimi-k2.6",
        "deepseek-v4-pro",
        "deepseek-v4-flash",
        "minimax-m3",
        "qwen3.8-max",
        "gpt-5.6-luna",
    ),
    default_aux_model="deepseek-v4-flash",
)

register_provider(clinepass)
