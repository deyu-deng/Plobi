"""ai-gateway provider profile (ported from upstream Hermes; data only)."""

from providers import register_provider
from providers.base import ProviderProfile

ai_gateway = ProviderProfile(
    name='ai-gateway',
    aliases=('vercel', 'vercel-ai-gateway', 'ai_gateway', 'aigateway'),
    env_vars=('AI_GATEWAY_API_KEY',),
    base_url='https://ai-gateway.vercel.sh/v1',)
register_provider(ai_gateway)
