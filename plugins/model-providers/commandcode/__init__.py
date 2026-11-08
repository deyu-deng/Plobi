"""CommandCode provider profile (ported from upstream Hermes; data only)."""

from providers import register_provider
from providers.base import ProviderProfile

commandcode = ProviderProfile(
    name='commandcode',
    aliases=('commandcode-chat',),
    api_mode='chat_completions',
    env_vars=('COMMANDCODE_API_KEY', 'COMMANDCODE_BASE_URL'),
    display_name='CommandCode',
    description='CommandCode — 20+ models via OpenAI-compatible API',
    signup_url='https://commandcode.ai/',
    fallback_models=('deepseek/deepseek-v4-pro', 'deepseek/deepseek-v4-flash', 'Qwen/Qwen3.7-Max', 'Qwen/Qwen3.6-Plus', 'moonshotai/Kimi-K2.6', 'zai-org/GLM-5.1', 'MiniMaxAI/MiniMax-M2.7', 'stepfun/Step-3.5-Flash', 'xiaomi/mimo-v2.5-pro', 'google/gemini-3.5-flash', 'gpt-5.5'),)
register_provider(commandcode)
