"""Z.AI / GLM (China) provider profile — BigModel is a separate account system.

`zai` fronts api.z.ai (global). The mainland product is BigModel: its own console, its own
keys, its own endpoint. Two cards, not one toggle (user 2026-11-08).
"""

from providers import register_provider
from providers.base import ProviderProfile

zai_cn = ProviderProfile(
    name="zai-cn",
    aliases=("bigmodel", "glm-cn", "zhipu-cn"),
    env_vars=("GLM_CN_API_KEY", "GLM_CN_BASE_URL"),
    display_name="Z.AI / GLM (China)",
    description="BigModel (GLM-4.6 / GLM-5) mainland account",
    signup_url="https://open.bigmodel.cn/",
    fallback_models=(),
    base_url="https://open.bigmodel.cn/api/paas/v4",
    auth_type="api_key",
)

register_provider(zai_cn)
