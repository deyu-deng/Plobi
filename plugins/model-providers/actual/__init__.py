"""Actual Computer provider profile (ported from upstream Hermes; data only)."""

from providers import register_provider
from providers.base import ProviderProfile

actual = ProviderProfile(
    name='actual',
    aliases=('actual-computer', 'actualcomputer', 'aci'),
    display_name='Actual Computer',
    description='Actual Computer - hosted inference via api.actual.inc, or local offline inference via model.base_url in config.yaml',
    signup_url='https://actual.inc',
    env_vars=('ACTUAL_API_KEY',),
    auth_type='api_key',
    api_mode='chat_completions',)
register_provider(actual)
