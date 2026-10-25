"""Qwen Code free OAuth tier via ACP.

The Qwen Code CLI offers a free daily OAuth quota and supports ACP with
`--acp`. Driving the installed CLI reuses the account signed in by `qwen`
itself; Plobi never reads the token.

Install : npm i -g @qwen-code/qwen-code
Verify  : qwen --version ; qwen --acp
"""

from __future__ import annotations

from providers import register_provider
from providers.base import ProviderProfile


qwen_acp = ProviderProfile(
    name="qwen-acp",
    aliases=("qwen-free", "qwen-cli"),
    api_mode="chat_completions",
    display_name="Qwen Code",
    description="Qwen Code free OAuth tier, driven through the local ACP process",
    signup_url="https://github.com/QwenLM/qwen-code",
    env_vars=(),
    base_url="acp://qwen",
    auth_type="external_process",
    supports_health_check=False,
    fallback_models=(
        "qwen3.7-plus",
        "qwen3.7-max",
        "qwen3.6-plus",
        "qwen3.5-plus",
        "qwen3-coder-plus",
    ),
)

register_provider(qwen_acp)
