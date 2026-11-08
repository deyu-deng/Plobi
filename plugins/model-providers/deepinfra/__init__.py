"""DeepInfra provider profile (ported from upstream Hermes; data only)."""

from providers import register_provider
from providers.base import ProviderProfile

deepinfra = ProviderProfile(
    name='deepinfra',
    aliases=('deep-infra', 'deepinfra-ai'),
    display_name='DeepInfra',
    description='DeepInfra — 100+ open models, pay-per-use',
    signup_url='https://deepinfra.com/dash/api_keys',
    env_vars=('DEEPINFRA_API_KEY', 'DEEPINFRA_BASE_URL'),
    base_url='https://api.deepinfra.com/v1/openai',
    auth_type='api_key',
    default_max_tokens=None,
    fallback_models=(),)
register_provider(deepinfra)
