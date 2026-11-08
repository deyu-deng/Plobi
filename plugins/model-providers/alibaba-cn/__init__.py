"""Qwen Cloud (China) provider profile — the DashScope mainland account system.

International Qwen Cloud (`alibaba`) and the mainland one are **different accounts** with
different keys and endpoints, so they are two cards, not one with a region switch
(user 2026-11-08 拍: CN 与国际两张卡). Env names follow this repo's `-cn` convention
(`KIMI_CN_API_KEY`, `MINIMAX_CN_API_KEY`).
"""

from providers import register_provider
from providers.base import ProviderProfile

alibaba_cn = ProviderProfile(
    name="alibaba-cn",
    aliases=("dashscope-cn", "qwen-cn"),
    env_vars=("DASHSCOPE_CN_API_KEY", "DASHSCOPE_CN_BASE_URL"),
    display_name="Qwen Cloud (China)",
    description="DashScope mainland (Qwen + multi-provider)",
    signup_url="https://bailian.console.aliyun.com/",
    fallback_models=(),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    auth_type="api_key",
)

register_provider(alibaba_cn)
