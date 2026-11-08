"""Tencent TokenPlan provider profile (ported from upstream Hermes; data only).

TokenHub and TokenPlan are two Tencent products with two consoles; TokenHub is already a
card here, this adds the plan endpoint upstream ships.
"""

from providers import register_provider
from providers.base import ProviderProfile

tencent_tokenplan = ProviderProfile(
    name="tencent-tokenplan",
    aliases=(),
    env_vars=("TOKENPLAN_API_KEY", "TOKENPLAN_BASE_URL"),
    display_name="Tencent TokenPlan",
    description="Tencent TokenPlan (Anthropic-shaped plan endpoint)",
    signup_url="https://cloud.tencent.com/",
    fallback_models=(),
    base_url="https://api.lkeap.cloud.tencent.com/plan/anthropic",
    auth_type="api_key",
)

register_provider(tencent_tokenplan)
