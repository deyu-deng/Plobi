"""Meta Model API provider profile (ported from upstream Hermes; data only)."""

from providers import register_provider
from providers.base import ProviderProfile

meta_ai = ProviderProfile(
    name='meta-ai',
    aliases=('meta', 'muse', 'muse-spark', 'model-api', 'msl'),
    display_name='Meta Model API',
    description='Meta Muse Spark family (Meta Superintelligence Labs)',
    signup_url='https://developer.meta.com/ai/',
    env_vars=('MODEL_API_KEY', 'META_API_KEY', 'META_MODEL_API_KEY', 'META_BASE_URL'),
    auth_type='api_key',
    api_mode='codex_responses',
    default_max_tokens=16384,
    fallback_models=('muse-spark-1.2',),)
register_provider(meta_ai)
