"""Mind memory provider — Plobi ↔ Mind second-brain vault adapter.

============================================================================
STATUS: IMPLEMENTED (P0).
============================================================================
This file wires ``MindProvider`` to the ``MemoryProvider`` ABC so the plugin
is discoverable, loadable, and activatable by Plobi. P0 implements the real
disk I/O for every lifecycle method while respecting the compliance boundary
below. All writes funnel through :class:`plobi.mind.writer.MindWriter`
(serialized + verifier + commit switch) and are pre-checked with ``_is_safe``.

Full spec + evidence:  Docs/specs/memory-adapter.md  (repo root)
Mind repo (official):  ``mind/`` (lowercase, inside the repository;
                       env ``MIND_ROOT`` overrides — never hardcode drive letters)
Mind verifier:         Loom/scripts/verifier.py (inside the Mind repo)

Why a plugin and not core changes?
  Plobi memory is provider-pluginized (AGENTS.md: "capability at the edges").
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
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider
from plobi.mind.paths import SAFE_PREFIXES
from plobi.mind.writer import MindWriter

logger = logging.getLogger(__name__)

# --- Compliance boundary -------------------------------------------------
# Real writes MUST resolve under one of these relative prefixes (relative to
# MIND_ROOT). These are the zones Mind's verifier does NOT block. Do not add
# Vault/projects/<new> or Loom/skills/<new> here — those require editing Mind's
# own AGENTS.md declarations, which this plugin must never do automatically.
# NOTE: the Plobi project zone is the official capitalised ``Plobi`` dir —
# the lowercase ``plobi`` dir is a legacy Plobi archive and must never be
# created by accident (writing there would pollute the knowledge base).
# SAFE_PREFIXES is imported from plobi.mind.paths — the single source of
# truth. Do NOT redefine it here; updating one without the other causes
# security checks to diverge.

MAX_DIGEST_TURNS = 40
MAX_DIGEST_CHARS_PER_MSG = 1200

# WP-MIND 读接缝：画像/项目状态窄切进上下文（MindReader 已按 4000 字符/文件
# 截断；这里再按块收紧，保证召回注入永不淹没对话上下文）。
PROFILE_MAX_CHARS_PER_SECTION = 800
PROFILE_PROJECT_LIMIT = 3


def _profile_block(root: Path) -> str:
    """画像/项目状态的窄切读取（无模型、纯文件）。

    来源：``Vault/meta/Persona.md`` + ``Vault/projects/<项目>/plan.md``/
    ``progress.md``（MindReader，大写 Plobi 是当前项目写入位）。为空返回
    ``""``——调用方据此跳过注入，不产生空块。
    """
    from plobi.mind.reader import MindReader

    ctx = MindReader(root).context(project_limit=PROFILE_PROJECT_LIMIT)
    if ctx.is_empty:
        return ""

    sections: List[str] = []
    if ctx.persona:
        persona = ctx.persona.strip()
        if len(persona) > PROFILE_MAX_CHARS_PER_SECTION:
            persona = persona[:PROFILE_MAX_CHARS_PER_SECTION].rstrip() + "…"
        sections.append(f"### 用户画像\n{persona}")
    for brief in ctx.projects:
        piece = (brief.progress_excerpt or brief.plan_excerpt or "").strip()
        if not piece:
            continue
        if len(piece) > PROFILE_MAX_CHARS_PER_SECTION:
            piece = piece[:PROFILE_MAX_CHARS_PER_SECTION].rstrip() + "…"
        sections.append(f"### 项目 {brief.name}\n{piece}")
    if not sections:
        return ""
    return "[Mind 画像]\n" + "\n\n".join(sections)


def _resolve_root() -> Path:
    """Resolve the Mind root.

    Delegates to :mod:`plobi.mind.paths` so there is one resolver and no
    hardcoded drive letters (the North Star contract requires ``MIND_ROOT``).
    Returns a non-existent placeholder when nothing is configured, so callers
    can keep using ``.is_dir()`` as the availability check.
    """
    from plobi.mind.paths import resolve_root as _shared_resolve

    root = _shared_resolve()
    if root is not None:
        return root
    return Path(os.environ.get("MIND_ROOT") or "mind-root-not-configured")


def _is_safe(target: Path, root: Path) -> bool:
    """Return True if ``target`` lies inside SAFE_PREFIXES.

    Call this BEFORE every real write; reject + ``logger.warning`` otherwise.
    """
    try:
        rel = target.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return False
    return any(rel == p or rel.startswith(p + "/") for p in SAFE_PREFIXES)


def _kebab(text: str) -> str:
    """Normalise an arbitrary string to kebab-case (Mind convention)."""
    slug = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "-", text.strip()).strip("-").lower()
    return re.sub(r"-{2,}", "-", slug)


def _render_digest(messages: List[Dict[str, Any]]) -> str:
    """Render a bounded session digest from the OpenAI-style message list."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines: List[str] = [f"# Session Digest — {stamp}", ""]
    count = 0
    for msg in messages or []:
        role = msg.get("role", "")
        content = msg.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str) or not content.strip():
            continue
        text = content.strip()
        if len(text) > MAX_DIGEST_CHARS_PER_MSG:
            text = text[:MAX_DIGEST_CHARS_PER_MSG].rstrip() + "\n…"
        lines.append(f"## {role}")
        lines.append(text)
        lines.append("")
        count += 1
        if count >= MAX_DIGEST_TURNS:
            lines.append("… (truncated)")
            break
    return "\n".join(lines)


class MindProvider(MemoryProvider):
    """Plobi memory provider that bridges to the Mind vault."""

    def __init__(self) -> None:
        self._session_id: str = ""
        self._mind_root: Optional[Path] = None
        self._prefetch_cache: Dict[str, str] = {}
        self._agent_context: str = "primary"
        self._agenda_published_date: str = ""

    @property
    def name(self) -> str:
        return "mind"

    # -- required abstract methods ---------------------------------------

    def is_available(self) -> bool:
        """Report availability if MIND_ROOT resolves to a real vault."""
        return _resolve_root().is_dir()

    def initialize(self, session_id: str, **kwargs) -> None:
        """Resolve root + session. Builds a writer handle lazily on write."""
        self._session_id = session_id
        self._mind_root = _resolve_root()
        # cron / flush 等非 primary 上下文不得往 Mind 写运行时摘要。
        self._agent_context = str(kwargs.get("agent_context") or "primary")
        logger.info(
            "[mind] initialized for session %s (root=%s, context=%s)",
            session_id, self._mind_root, self._agent_context,
        )

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """P0: no model-facing tools. P1 will expose mind_read/write/search."""
        return []

    # -- recall (per-turn, injected as context) --------------------------

    def _profile_summary(self) -> str:
        """画像摘要（WP-MIND 读接缝）。任何失败都降级为告警 + 空串。"""
        try:
            root = _resolve_root()
            if not root.is_dir():
                return ""
            return _profile_block(root)
        except Exception as exc:  # noqa: BLE001 - recall must never crash a turn
            logger.warning("[mind] profile summary read failed (non-fatal): %s", exc)
            return ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Recall relevant Mind snippets to inject into the system prompt.

        WP-MIND：召回结果前置一份画像/项目状态摘要（``_profile_summary``），
        帮助模型识别「在为谁服务、项目进行到哪」。该读取是窄切（persona +
        最多 3 个项目简报，每段截断），只读、失败降级为告警。

        Checks the warm-up cache first (filled by ``queue_prefetch``), then
        falls back to a live keyword search over SAFE_PREFIXES. Returns an
        empty string when nothing relevant is found.
        """
        parts: List[str] = []
        profile = self._profile_summary()
        if profile:
            parts.append(profile)
            logger.info(
                "[mind] prefetch includes profile summary (%d chars)", len(profile)
            )

        root = _resolve_root()
        if not root.is_dir() or not query:
            return "\n\n".join(parts)

        cached = self._prefetch_cache.pop(query, None)
        if cached is not None:
            parts.append(cached)
            return "\n\n".join(parts)

        try:
            from .retrieval import search as _mind_search

            snippets = _mind_search(root, query, limit=5)
        except Exception as exc:  # noqa: BLE001 - recall must never crash a turn
            logger.warning("[mind] prefetch failed (non-fatal): %s", exc)
            return "\n\n".join(parts)
        parts.extend(snippets)
        return "\n\n".join(parts)

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """Queue background recall for the NEXT turn.

        Lightweight synchronous warm-up: runs the keyword search now and
        caches the formatted result so the next ``prefetch(query)`` hits the
        cache instead of scanning the vault again.
        """
        if not query:
            return
        try:
            root = _resolve_root()
            if not root.is_dir():
                return
            from .retrieval import search as _mind_search

            snippets = _mind_search(root, query, limit=5)
            if snippets:
                self._prefetch_cache[query] = "\n\n".join(snippets)
        except Exception as exc:  # noqa: BLE001 - background, never fatal
            logger.warning("[mind] queue_prefetch failed (non-fatal): %s", exc)

    # -- persist (per-turn) ----------------------------------------------

    def _maybe_publish_daily_agenda(self) -> None:
        """WP-MIND 写接缝：每会话每天一次，把当日日程摘要发进 Mind。

        走既有 ``plobi.agenda.mind_sync`` 通道（MindWriter 串行服务、无模型
        渲染、目标 ``Vault/projects/Plobi/daily/<date>.md``）。不新增 API、
        不并行写。任何失败只降级为日志告警，绝不阻塞主对话流；非 primary
        上下文（cron/flush）直接跳过，运行时状态真源始终是 SQLite（ADR-0007）。
        """
        if self._agent_context != "primary":
            return
        today = datetime.now().strftime("%Y-%m-%d")
        if self._agenda_published_date == today:
            return
        self._agenda_published_date = today  # 当天不再重试，避免失败刷屏/每轮重写
        try:
            from plobi.agenda.mind_sync import publish_project_daily_summary

            result = publish_project_daily_summary()
            if result.ok:
                logger.info(
                    "[mind] daily agenda summary published to Vault/projects/Plobi"
                )
            else:
                logger.warning(
                    "[mind] daily agenda summary not written: %s", result.detail
                )
        except Exception as exc:  # noqa: BLE001 - persistence is non-fatal
            logger.warning("[mind] daily agenda summary failed (non-fatal): %s", exc)

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Write a distilled turn note to Loom/raw/chat-logs/exports/.

        Appends ``<kebab-session>.md`` (one section per turn) via MindWriter.
        Refuses the write unless the resolved path is inside SAFE_PREFIXES.

        同时触发写接缝的每日日程摘要（独立 try 域：turn 笔记失败不拖死它，
        反之亦然）。
        """
        self._maybe_publish_daily_agenda()
        try:
            root = _resolve_root()
            if not root.is_dir():
                return
            kebab = _kebab(session_id) or "chat"
            rel = f"Loom/raw/chat-logs/exports/{kebab}.md"
            if not _is_safe((root / rel).resolve(), root):
                logger.warning("[mind] refused sync_turn write: %s", rel)
                return

            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            body = (
                f"## {stamp}\n\n"
                f"### user\n{user_content.strip()}\n\n"
                f"### assistant\n{assistant_content.strip()}\n\n"
            )
            result = MindWriter(root, run_verifier=True, commit=False).write_one(
                rel, body, mode="append"
            )
            if not result.ok:
                logger.warning("[mind] sync_turn write failed: %s", result.detail)
        except Exception as exc:  # noqa: BLE001 - persistence is non-fatal
            logger.warning("[mind] sync_turn failed (non-fatal): %s", exc)

    # -- session boundary ------------------------------------------------

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """Digest the session into Loom/raw/chat-logs/digested/<YYYY-MM-DD>/.

        One digest per session (kebab session id, fallback ``session``),
        written via MindWriter after an ``_is_safe`` assertion.
        """
        try:
            root = _resolve_root()
            if not root.is_dir() or not messages:
                return
            today = datetime.now().strftime("%Y-%m-%d")
            kebab = _kebab(self._session_id) or "session"
            rel = f"Loom/raw/chat-logs/digested/{today}/{kebab}-digest.md"
            if not _is_safe((root / rel).resolve(), root):
                logger.warning("[mind] refused on_session_end write: %s", rel)
                return

            result = MindWriter(root, run_verifier=True, commit=False).write_one(
                rel, _render_digest(messages), mode="overwrite"
            )
            if not result.ok:
                logger.warning("[mind] on_session_end write failed: %s", result.detail)
        except Exception as exc:  # noqa: BLE001 - session end must not raise
            logger.warning("[mind] on_session_end failed (non-fatal): %s", exc)

    # -- mirror built-in memory writes -----------------------------------

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Mirror Plobi MEMORY.md/USER.md writes into the Mind vault.

        - ``remove`` actions never delete from the second brain (log only).
        - concept/entity/source-kind metadata routes to ``Loom/wiki/concepts/``
          (``plobi-<slug>.md``); everything else lands in
          ``Vault/projects/Plobi/<slug>.md``.
        - Writes go through MindWriter after an ``_is_safe`` assertion.

        Triggered by MemoryManager.notify_memory_tool_write (verified caller
        at tool_executor.py:1301 / agent_runtime_helpers.py:2298).
        """
        try:
            root = _resolve_root()
            if not root.is_dir():
                return
            if action == "remove":
                logger.info("[mind] on_memory_write 'remove' ignored (never delete from Mind): %s", target)
                return
            if not content:
                return

            slug = _kebab(Path(str(target)).stem) or _kebab(target) or "memory-note"
            meta = metadata or {}
            kind = str(meta.get("kind", "")).lower()
            if kind in ("concept", "entity", "source") or "concepts" in str(target).lower():
                rel = f"Loom/wiki/concepts/plobi-{slug}.md"
            else:
                rel = f"Vault/projects/Plobi/{slug}.md"

            if not _is_safe((root / rel).resolve(), root):
                logger.warning("[mind] refused on_memory_write: %s", rel)
                return

            body = f"# {slug}\n\n{content.strip()}\n"
            result = MindWriter(root, run_verifier=True, commit=False).write_one(
                rel, body, mode="overwrite"
            )
            if not result.ok:
                logger.warning("[mind] on_memory_write failed: %s", result.detail)
        except Exception as exc:  # noqa: BLE001 - mirroring is non-fatal
            logger.warning("[mind] on_memory_write failed (non-fatal): %s", exc)

    # -- backup integration ---------------------------------------------

    def backup_paths(self) -> List[str]:
        """Declare Mind root so `plobi backup` includes it (outside PLOBI_HOME)."""
        root = _resolve_root()
        return [str(root)] if root.is_dir() else []
