"""Mind memory provider registration shell.

**本体在内核**（裁定 198，用户 10-23「不要再是插件形态了」）：provider 在
``plobi/mind/provider.py``、检索在 ``plobi/mind/retrieval.py``。这个目录只剩两件事——
按 ``plugins/memory/__init__.py`` 的发现契约把官方脑注册进去，以及 ``cli.py`` 那个
``plobi mind`` 入口（它的注册链是插件发现，挪进内核要动 ``main.py`` 的 argparse 装配，
单独一笔做，别和这次搬家混在一起）。
"""
from plobi.mind.provider import MindProvider

__all__ = ["MindProvider", "register"]


def register(collector) -> None:
    """Register the Mind provider with the Plobi memory plugin loader."""
    collector.register_memory_provider(MindProvider())
