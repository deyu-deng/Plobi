"""Mind memory provider plugin entrypoint.

Discovery contract (plugins/memory/__init__.py): the loader first looks for a
``register(collector)`` function and calls ``collector.register_memory_provider(
instance)``. Fallback: it instantiates a ``MemoryProvider`` subclass found in
the module. We use the ``register`` pattern so the single ``MindProvider``
instance is created deterministically.
"""
from .mind import MindProvider


def register(collector) -> None:
    """Register the Mind provider with the Vaelis memory plugin loader."""
    collector.register_memory_provider(MindProvider())
