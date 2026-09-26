"""B2: L2 resident agent registry.

每项目一个常驻 profile（底座 profiles 机制）；角色→模型路由配置化；
独立会话；L2 直读 Mind 项目子树。
"""

from .registry import (
    AgentEntry,
    AgentRegistry,
    default_path,
    load_registry,
    run_secretary_ask,
)

__all__ = [
    "AgentEntry",
    "AgentRegistry",
    "default_path",
    "load_registry",
    "run_secretary_ask",
]
