"""Ramp Router provider profile (ported from upstream Hermes; data only)."""

from providers import register_provider
from providers.base import ProviderProfile

router = ProviderProfile(
    name='router',
    aliases=('ramp-router', 'ramp', 'router.com'),
    api_mode='codex_responses',
    display_name='Ramp Router',
    description='Ramp Router (router.com) — routes each request to the cheapest model that clears your quality bar',
    signup_url='https://app.router.com/keys',
    env_vars=('RAMP_ROUTER_API_KEY', 'ROUTER_API_KEY', 'RAMP_ROUTER_BASE_URL'),
    auth_type='api_key',
    fallback_models=(),)
register_provider(router)
