"""Upstage Solar provider profile (ported from upstream Hermes; data only)."""

from providers import register_provider
from providers.base import ProviderProfile

upstage = ProviderProfile(
    name='upstage',
    aliases=('solar',),
    display_name='Upstage Solar',
    description='Upstage (Solar API)',
    signup_url='https://console.upstage.ai/api-keys',
    env_vars=('UPSTAGE_API_KEY', 'UPSTAGE_BASE_URL'),
    base_url='https://api.upstage.ai/v1',
    auth_type='api_key',
    fallback_models=('solar-pro3',),)
register_provider(upstage)
