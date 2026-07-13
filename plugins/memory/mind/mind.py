"""Mind memory provider — Vaelis ↔ Mind second-brain vault adapter.

============================================================================
STATUS: FRAMEWORK SCAFFOLD ONLY.
============================================================================
This file wires ``MindProvider`` to the ``MemoryProvider`` ABC so the plugin
is discoverable, loadable, and activatable by Vaelis — but **no real disk I/O
happens yet**. Every method is a stub with a docstring describing the intended
real logic and the compliance boundary it must respect. Review the structure
and boundaries here, then implement.

Full spec + evidence:  docs/MIND_ADAPTER_PLAN.md  (repo root)
Mind repo (Mac):       /Users/ciel/Mind
Mind verifier:         /Users/ciel/Mind/Loom/scripts/verifier.py

Why a plugin and not core changes?
  Vaelis memory is provider-pluginized (AGENTS.md: "capability at the edges").
  Adding ``plugins/memory/mind/`` + setting ``memory.provider: mind`` in
  config.yaml is the *only* integration step — agent/memory_provider.py,
  memory_manager.py, and run_agent.py are NOT touched.  (Verified 2026-07-13.)

Compliance boundary (CRITICAL — see plan §3/§5):
  Mind's git pre-commit verifier BLOCKS commits where:
    (1) Vault/projects top-level dir names != AGENTS.md §1 declaration, OR
    (2) Loom/skills skill count != AGENTS.md declaration.
  All REAL writes must stay inside SAFE_PREFIXES below. Writing elsewhere
  (new top-level project dir, new skill dir, AGENTS.md edits) risks BLOCKING
  Mind's commits or polluting the second brain. When implementing, assert the
  resolved path is under a SAFE_PREFIX before any write.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider

logger = logging.getLogger(__name__)

# --- Compliance boundary -------------------------------------------------
# Real writes MUST resolve under one of these relative prefixes (relative to
# MIND_ROOT). These are the zones Mind's verifier does NOT block. Do not add
# Vault/projects/<new> or Loom/skills/<new> here — those require editing Mind's
# own AGENTS.md declarations, which this plugin must never do automatically.
DEFAULT_MIND_ROOT = "/Users/ciel/Mind"
SAFE_PREFIXES = (
    "Vault/projects/vaelis",       # Vaelis-specific knowledge (already exists)
    "Vault/meta",
    "Vault/notes",
    "Vault/journal",
    "Vault/inbox",
    "Loom/wiki/concepts",
    "Loom/wiki/entities",
    "Loom/wiki/sources",
    "Loom/wiki/comparisons",
    "Loom/raw/chat-logs/exports",   # chat-log exports (already exists)
    "Loom/raw/chat-logs/digested",  # per-day digest archive
)


def _resolve_root() -> Path:
    """Resolve the Mind root from env (override) or platform default."""
    raw = os.environ.get("MIND_ROOT")
    if raw:
        return Path(raw)
    # Platform default. Windows: D:\Mind. Extend via config.yaml if needed.
    if os.name == "nt":
        return Path("D:/Mind")
    return Path(DEFAULT_MIND_ROOT)


def _is_safe(target: Path, root: Path) -> bool:
    """Return True if ``target`` lies inside SAFE_PREFIXES.

    TODO(implement): call this BEFORE every real write in sync_turn /
    on_session_end / on_memory_write. Reject + logger.warning otherwise.
    """
    try:
        rel = target.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return False
    return any(rel == p or rel.startswith(p + "/") for p in SAFE_PREFIXES)


class MindProvider(MemoryProvider):
    """Vaelis memory provider that bridges to the Mind vault.

    STUB: methods are present and correctly typed but do no real work yet.
    """

    @property
    def name(self) -> str:
        return "mind"

    # -- required abstract methods ---------------------------------------

    def is_available(self) -> bool:
        """STUB: report availability if MIND_ROOT exists. No writes.

        Real impl: also validate path whitelist / config; still no network.
        """
        return _resolve_root().is_dir()

    def initialize(self, session_id: str, **kwargs) -> None:
        """STUB: resolve root + session. Real init may build a path cache."""
        self._session_id = session_id
        self._mind_root = _resolve_root()
        logger.info(
            "[mind] initialized for session %s (root=%s) [STUB — no I/O]",
            session_id, self._mind_root,
        )

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """P0: no model-facing tools. P1 will expose mind_read/write/search."""
        return []

    # -- recall (per-turn, injected as context) --------------------------

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """STUB: real impl greps the Mind tree inside SAFE_PREFIXES (via
        retrieval.search) and returns formatted markdown snippets to inject
        into the system prompt. Returns empty for now (no context injected).
        """
        return ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """STUB: real impl queues background recall for the next turn."""
        pass

    # -- persist (per-turn) ----------------------------------------------

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """STUB: real impl writes a distilled note to
        Loom/raw/chat-logs/exports/<kebab>.md (assert _is_safe first).
        No disk I/O yet (framework review stage)."""
        pass

    # -- session boundary ------------------------------------------------

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """STUB: real impl digests the session into
        Loom/raw/chat-logs/digested/<YYYY-MM-DD>/ or Vault/projects/vaelis/.
        No disk I/O yet."""
        pass

    # -- mirror built-in memory writes -----------------------------------

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """STUB: real impl mirrors Vaelis MEMORY.md/USER.md writes into
        Vault/projects/vaelis/ (assert _is_safe first). No disk I/O yet.

        Triggered by MemoryManager.notify_memory_tool_write (verified caller
        at tool_executor.py:1301 / agent_runtime_helpers.py:2298).
        """
        pass

    # -- backup integration ---------------------------------------------

    def backup_paths(self) -> List[str]:
        """Declare Mind root so `hermes backup` includes it (outside HERMES_HOME)."""
        root = _resolve_root()
        return [str(root)] if root.is_dir() else []
