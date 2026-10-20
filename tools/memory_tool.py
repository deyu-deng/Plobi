#!/usr/bin/env python3
"""
Memory Tool Module - Persistent Curated Memory

Bounded, file-backed memory that persists across sessions, laid out per
裁定 181 as **three scopes plus one digest** under the shared root (never
inside a 分身 home — the key is the Agent's identity, not its directory):

  memory/GLOBAL.md               cross-agent facts; writing this scope goes through
                                 the approval layer (target="global" stages it)
  memory/agents/<slug>/MEMORY.md this agent's own notes — where writes land by default
  memory/agents/<slug>/STATUS.md a few lines of "where I am with things", updated by
                                 the agent itself; only L1 reads these
  memory/USER.md                 the user profile — still exactly one copy

Injection = GLOBAL + this agent's own + USER.  L1 additionally gets the
per-agent STATUS digest, and **never** another agent's full MEMORY.md — 2200
chars x 24 agents would be paid on every turn and would break the prefix cache.
Each entry carries a ``source`` pointer (a relative ``vault/…`` or ``mind/…``
path plus a date) so memory stays an index rather than a warehouse; the
direction is memory -> vault (that's the page a human can read), never the
other way around (裁定 182 forbids de-duplicating the human-facing side).

Both blocks are injected into the system prompt as a frozen snapshot at session
start.  Mid-session writes update files on disk immediately (durable) but do NOT
change the system prompt -- this preserves the prefix cache for the entire
session.  The snapshot refreshes on the next session start.

Entry delimiter: § (section sign). Entries can be multiline.
Character limits (not tokens) because char counts are model-independent.

Design:
- Single `memory` tool, one atomic verb (write into a scope); actions are
  add / replace / remove.  Reaching ``global`` is a property of that location
  (plan + hash + a human nod), not an action the model has to guess (裁定 185).
- replace/remove use short unique substring matching (not full text or IDs)
- Behavioral guidance lives in the tool schema description
- Frozen snapshot pattern: system prompt is stable, tool responses show live state
"""

import hashlib
import json
import logging
import os
import re
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from plobi_constants import get_shared_plobi_root
from typing import Dict, Any, List, Optional

from utils import atomic_replace

# fcntl is Unix-only; on Windows use msvcrt for file locking
msvcrt = None
try:
    import fcntl
except ImportError:
    fcntl = None
    try:
        import msvcrt
    except ImportError:
        pass

logger = logging.getLogger(__name__)

# Where the memory files live — resolved dynamically so profile overrides
# (PLOBI_HOME env var changes) are always respected.  The old module-level
# constant was cached at import time and could go stale if a profile switch
# happened after the first import.
#
# 裁定 181: one shared root, split by **Agent identity** (a roster row), never by
# profile home.  裁定 44 already proved that per-profile ledger copies were unread
# residue — every profile resolved to the same file anyway.
L1_SLUG = "l1"
_SLUG_UNSAFE_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff_-]+")

# Scope -> (filename, is it per-agent).  The per-agent half is what 裁定 179/181
# asked for: one identity, one ledger — keyed by the roster row, never by a home.
MEMORY_SCOPES: Dict[str, tuple[str, bool]] = {
    "global": ("GLOBAL.md", False),
    "memory": ("MEMORY.md", True),
    "status": ("STATUS.md", True),
    "user": ("USER.md", False),
}


def resolve_agent_slug(slug: Optional[str] = None) -> str:
    """Return the slug of the agent whose memory this is.

    Identity is the roster row (裁定 178 乙案), surfaced to the process as
    ``PLOBI_AGENT_ID`` by the spawner (see ``agent_ask`` in the north-star
    plugin); an empty identity is L1 — the same convention 裁定 180 uses for
    ``sessions.agent_id``.
    """
    raw = (os.environ.get("PLOBI_AGENT_ID", "") if slug is None else slug) or ""
    name = _SLUG_UNSAFE_RE.sub("-", raw.strip().lower()).strip("-")
    return name or L1_SLUG


def is_l1_slug(slug: str) -> bool:
    return slug == L1_SLUG


def get_memory_dir() -> Path:
    """Return the shared memory root — ``<共享根>/memory``.

    Every profile resolves to the same root, because long-term memory attaches
    to the person and to an Agent identity, not to a home directory
    (裁定 44 / 181).  Which file inside it you want is a scope question —
    see :func:`memory_path` and :data:`MEMORY_SCOPES`.
    """
    return get_shared_plobi_root() / "memory"


def agent_memory_dir(slug: Optional[str] = None) -> Path:
    """``<共享根>/memory/agents/<slug>`` — one identity's own directory."""
    return get_memory_dir() / "agents" / resolve_agent_slug(slug)


def memory_path(scope: str, *, slug: Optional[str] = None) -> Path:
    """Resolve one scope's ledger file (``MEMORY_SCOPES`` is the only map)."""
    entry = MEMORY_SCOPES.get(scope)
    if entry is None:
        raise ValueError(f"Unknown memory scope {scope!r}. Use global, memory, status or user.")
    filename, per_agent = entry
    base = agent_memory_dir(slug) if per_agent else get_memory_dir()
    return base / filename


def entry_with_source(content: str, source: Optional[str]) -> str:
    """Append the provenance pointer that makes a bounded index possible.

    ``memory`` is a table of contents, not a warehouse — the char budget only
    works because each entry says where the human-readable version of it lives
    (``vault/…`` / ``mind/…`` + date).  The pointer travels *inside* the entry
    text so the §-delimited format, the drift guard and every existing caller
    keep working unchanged.  Direction matters: memory points at the vault page
    a person can read; the vault is never rewritten to point back at memory to
    save space (裁定 182).
    """
    text = (source or "").strip()
    if not text:
        return content
    if text.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", text) or ".." in text.split("/"):
        return content  # not a relative pointer — leave the entry as written
    date = time.strftime("%Y-%m-%d")
    if date not in text:
        text = f"{text} · {date}"
    return f"{content}\n〔出处 {text}〕"


def _file_sha(path: Path) -> str:
    """Hash of a memory file's current bytes — what an approval is bound to."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError, IOError):
        return hashlib.sha256(b"").hexdigest()


ENTRY_DELIMITER = "\n§\n"


# ---------------------------------------------------------------------------
# Memory content scanning — lightweight check for injection/exfiltration
# in content that gets injected into the system prompt.
#
# Patterns live in ``tools/threat_patterns.py`` — the single source of truth
# shared with the context-file scanner and the tool-result delimiter system.
# Memory uses the "strict" scope (broadest pattern set) because:
#  - memory entries are user-curated; the user can rewrite a flagged entry
#  - memory enters the system prompt as a FROZEN snapshot, so a poisoned
#    entry persists for the entire session and across sessions until
#    explicitly removed.
# ---------------------------------------------------------------------------

from tools.threat_patterns import first_threat_message as _first_threat_message


def _scan_memory_content(content: str) -> Optional[str]:
    """Scan memory content for injection/exfil patterns. Returns error string if blocked."""
    return _first_threat_message(content, scope="strict")


def _drift_error(path: "Path", bak_path: str) -> Dict[str, Any]:
    """Build the error dict returned when external drift is detected.

    The on-disk memory file contains content that wouldn't round-trip
    through the tool's parser/serializer — flushing would discard the
    appended/edited content from a patch tool, shell append, manual edit,
    or sister-session write. We refuse the mutation, point the operator at
    the .bak.<ts> snapshot we took, and tell them what to do next.
    """
    return {
        "success": False,
        "error": (
            f"Refusing to write {path.name}: file on disk has content that "
            f"wouldn't round-trip through the memory tool (likely added by "
            f"the patch tool, a shell append, a manual edit, or a "
            f"concurrent session). A snapshot was saved to {bak_path}. "
            f"Resolve the drift first — either rewrite the file as a clean "
            f"§-delimited list of entries, or move the extra content out — "
            f"then retry. This guard exists to prevent silent data loss "
            f"(issue #26045)."
        ),
        "drift_backup": bak_path,
        "remediation": (
            "Open the .bak file, integrate the missing entries into the "
            "memory tool one at a time via memory(action=add, content=...), "
            "then remove or rewrite the original file to a clean state."
        ),
    }


class MemoryStore:
    """
    Bounded curated memory with file persistence. One instance per AIAgent.

    Three scopes plus one digest, keyed by Agent identity (裁定 181):
      global  -> ``memory/GLOBAL.md``        (approval-written only; never a direct tool target)
      memory  -> ``memory/agents/<slug>/MEMORY.md``   (this agent's own notes)
      status  -> ``memory/agents/<slug>/STATUS.md``   (a few lines of recent state)
      user    -> ``memory/USER.md``          (the person's profile — one copy)

    Maintains two parallel states:
      - _system_prompt_snapshot: frozen at load time, used for system prompt injection.
        Never mutated mid-session. Keeps prefix cache stable.  The ``memory`` block is
        GLOBAL + this agent's own (+ every other agent's STATUS digest when this agent
        IS L1) — injection never stacks another agent's MEMORY.md.
      - <scope>_entries: live state, mutated by tool calls, persisted to disk.
        Tool responses always reflect this live state.
    """

    # After this many failed consolidation attempts (overflow / zero-match) in
    # ONE turn, stop instructing the model to "retry in this turn" and return a
    # terminal "save skipped" result so a fragile replace/add can't loop the
    # turn to budget exhaustion and suppress the user's reply (issue #42405).
    _MAX_CONSOLIDATION_FAILURES_PER_TURN = 3

    def __init__(self, memory_char_limit: int = 2200, user_char_limit: int = 1375,
                 *, global_char_limit: int = 2200, status_char_limit: int = 200,
                 l1_status_char_limit: int = 1500, squeeze_pressure: float = 0.9,
                 slug: Optional[str] = None):
        self.slug = resolve_agent_slug(slug)
        self.global_entries: List[str] = []
        self.memory_entries: List[str] = []
        self.status_entries: List[str] = []
        self.user_entries: List[str] = []
        # 三份上限各自可配（memory.global_char_limit / agent_char_limit /
        # user_char_limit）；STATUS 另给两格：每家一份的上限，与 L1 拼摘要的上限。
        self.global_char_limit = global_char_limit
        self.memory_char_limit = memory_char_limit
        self.status_char_limit = status_char_limit
        self.l1_status_char_limit = l1_status_char_limit
        # 下沉压线（见 :meth:`_at_capacity`）—— 也是配置流进来的，不是魔数。
        try:
            self.squeeze_pressure = min(1.0, max(0.0, float(squeeze_pressure)))
        except (TypeError, ValueError):
            self.squeeze_pressure = 0.9
        self.user_char_limit = user_char_limit
        # Frozen snapshot for system prompt -- set once at load_from_disk()
        self._system_prompt_snapshot: Dict[str, str] = {"memory": "", "user": ""}
        # Per-turn counter of failed at-capacity consolidation attempts; reset
        # at each turn boundary by reset_consolidation_failures() (#42405).
        self._consolidation_failures = 0

    def reset_consolidation_failures(self) -> None:
        """Reset the per-turn consolidation-failure counter (call at turn start)."""
        self._consolidation_failures = 0

    def _consolidation_failure(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """Count an at-capacity consolidation failure and degrade gracefully.

        Under the per-turn cap, return ``response`` unchanged (it already tells
        the model how to self-correct + retry in this turn). Once the cap is
        exceeded, drop the retry instruction and return a TERMINAL result so the
        model stops looping memory calls and proceeds to answer the user — a
        failed memory side effect must never block the turn's reply (#42405).
        """
        self._consolidation_failures += 1
        if self._consolidation_failures <= self._MAX_CONSOLIDATION_FAILURES_PER_TURN:
            return response
        return {
            "success": False,
            "done": True,
            "error": (
                f"Memory consolidation failed {self._consolidation_failures} times "
                "this turn. Stop retrying memory calls — leave memory unchanged for "
                "now and continue with your reply to the user. The fact can be saved "
                "in a later turn."
            ),
        }

    def load_from_disk(self):
        """Load the three scopes (plus this agent's STATUS) and capture the snapshot.

        The frozen snapshot is what enters the system prompt. We scan each
        entry for injection/promptware patterns at snapshot-build time —
        ANY hit replaces the entry text in the snapshot with a placeholder
        like ``[BLOCKED: …]``, so a poisoned-on-disk memory file (supply
        chain, compromised tool, sister-session write) cannot inject into
        the system prompt.

        The live ``*_entries`` lists keep the original text so the user can
        still SEE poisoned entries by inspecting the source files directly, and
        remove them — silently dropping them would hide the attack from the user.

        Injection shape (裁定 181): ``GLOBAL.md`` + this agent's own
        ``MEMORY.md`` + ``USER.md``; L1 additionally gets the STATUS digest of
        every other agent, and **never** another agent's full ``MEMORY.md``.
        The three parts are joined into the single ``memory`` block so every
        existing consumer (system prompt, context breakdown, prompt size) keeps
        reading one block and the prefix-cache invariant is untouched.

        Scanning is deterministic from disk bytes, so the snapshot remains
        stable for the entire session (prefix-cache invariant holds).
        """
        get_memory_dir().mkdir(parents=True, exist_ok=True)

        self.global_entries = self._read_file(memory_path("global"))
        self.memory_entries = self._read_file(memory_path("memory", slug=self.slug))
        self.status_entries = self._read_file(memory_path("status", slug=self.slug))
        self.user_entries = self._read_file(memory_path("user"))

        # Deduplicate entries (preserves order, keeps first occurrence)
        self.global_entries = list(dict.fromkeys(self.global_entries))
        self.memory_entries = list(dict.fromkeys(self.memory_entries))
        self.status_entries = list(dict.fromkeys(self.status_entries))
        self.user_entries = list(dict.fromkeys(self.user_entries))

        # Sanitize entries for the system-prompt snapshot only.  Live state
        # (memory_entries / user_entries) keeps the raw text so the user
        # can see + remove poisoned entries via the memory tool.
        sanitized_global = self._sanitize_entries_for_snapshot(self.global_entries, "GLOBAL.md")
        sanitized_memory = self._sanitize_entries_for_snapshot(self.memory_entries, "MEMORY.md")
        sanitized_user = self._sanitize_entries_for_snapshot(self.user_entries, "USER.md")

        global_block = self._render_block("global", sanitized_global)
        own_block = self._render_block("memory", sanitized_memory)
        digest_block = self._build_l1_status_digest() if is_l1_slug(self.slug) else ""

        # Capture frozen snapshot for system prompt injection.  ``memory`` is what
        # gets injected; the per-scope keys exist for diagnostics (prompt size,
        # context breakdown, the 判据③ reading that the injected total equals the
        # three parts) and are never injected as separate blocks.
        self._system_prompt_snapshot = {
            "memory": "\n\n".join(b for b in (global_block, own_block, digest_block) if b),
            "user": self._render_block("user", sanitized_user),
            "global": global_block,
            "agent": own_block,
            "status_digest": digest_block,
        }

    def _build_l1_status_digest(self) -> str:
        """Render the STATUS digest L1 sees: a few lines per *other* agent.

        Only ``STATUS.md`` is read here — never another agent's ``MEMORY.md``.
        Slug order is sorted so the block is byte-stable for the session, and the
        aggregate is capped by ``memory.l1_status_char_limit`` (2200 x N agents
        would be paid on every turn and would shatter the cached prefix).
        """
        agents_root = get_memory_dir() / "agents"
        if not agents_root.is_dir():
            return ""
        sections: List[str] = []
        used = 0
        for slug_dir in sorted(agents_root.iterdir(), key=lambda p: p.name):
            slug = slug_dir.name
            if slug == self.slug:
                continue  # L1 does not read its own status file back
            path = slug_dir / "STATUS.md"
            if not path.is_file():
                continue
            entries = list(dict.fromkeys(self._read_file(path)))
            safe = self._sanitize_entries_for_snapshot(entries, f"agents/{slug}/STATUS.md")
            body = "\n".join(f"- {line}" for entry in safe for line in entry.splitlines() if line)
            if not body:
                continue
            room = self.l1_status_char_limit - used
            if room <= 0:
                sections.append("…（其余 Agent 的近况这次没列，超出 L1 摘要上限）")
                break
            if len(body) > room:
                body = body[:room] + "…"
            sections.append(f"{slug}: {body}")
            used += len(body)
        if not sections:
            return ""
        header = (
            f"AGENT STATUS DIGEST ({len(sections)} 项，L1 只看摘要，不读各 Agent 全文) "
            f"[{used:,}/{self.l1_status_char_limit:,} chars]"
        )
        separator = "═" * 46
        return f"{separator}\n{header}\n{separator}\n" + "\n".join(sections)

    @staticmethod
    def _sanitize_entries_for_snapshot(entries: List[str], filename: str) -> List[str]:
        """Return ``entries`` with any threat-matching entry replaced by a placeholder.

        Each entry is scanned with the shared threat-pattern library at the
        ``"strict"`` scope (same as memory writes).  On match, the entry is
        replaced in the returned list with ``"[BLOCKED: <filename> entry
        contained threat pattern: <ids>. Removed from system prompt.]"`` —
        the placeholder enters the snapshot, the original entry stays in
        live state for the user to inspect and delete.

        Empty or already-block-marker entries pass through unchanged.
        """
        from tools.threat_patterns import scan_for_threats

        sanitized: List[str] = []
        for entry in entries:
            if not entry or entry.startswith("[BLOCKED:"):
                sanitized.append(entry)
                continue
            findings = scan_for_threats(entry, scope="strict")
            if findings:
                logger.warning(
                    "Memory entry from %s blocked at load time: %s",
                    filename, ", ".join(findings),
                )
                sanitized.append(
                    f"[BLOCKED: {filename} entry contained threat pattern(s): "
                    f"{', '.join(findings)}. Removed from system prompt; "
                    f"use memory(action=remove) "
                    f"to delete the original.]"
                )
            else:
                sanitized.append(entry)
        return sanitized

    @staticmethod
    @contextmanager
    def _file_lock(path: Path):
        """Acquire an exclusive file lock for read-modify-write safety.

        Uses a separate .lock file so the memory file itself can still be
        atomically replaced via os.replace().
        """
        lock_path = path.with_suffix(path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        if fcntl is None and msvcrt is None:
            yield
            return

        fd = open(lock_path, "a+", encoding="utf-8")
        try:
            if fcntl:
                fcntl.flock(fd, fcntl.LOCK_EX)
            else:
                fd.seek(0)
                msvcrt.locking(fd.fileno(), msvcrt.LK_LOCK, 1)
            yield
        finally:
            if fcntl:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except (OSError, IOError):
                    pass
            elif msvcrt:
                try:
                    fd.seek(0)
                    msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
                except (OSError, IOError):
                    pass
            fd.close()

    def _path_for(self, target: str) -> Path:
        """Resolve a scope's ledger file — 三份形状住的地方（裁定 181）.

        ``memory`` and ``status`` are this agent's own files, so the path moves
        with the identity; ``global`` and ``user`` are shared and therefore
        identical for every agent.
        """
        return memory_path(target if target in MEMORY_SCOPES else "memory",
                           slug=self.slug)

    def _reload_target(self, target: str, *, skip_drift: bool = False) -> Optional[str]:
        """Re-read entries from disk into in-memory state.

        Called under file lock to get the latest state before mutating.
        Returns the backup path if external drift was detected (the on-disk
        file contains content that wouldn't round-trip through our
        parser/serializer, OR an entry larger than the store's char limit).
        When drift is detected the caller must abort the mutation —
        flushing would discard the un-roundtrippable content.
        Returns None on clean reload.

        When *skip_drift* is True the round-trip / entry-size check is
        bypassed.  Used by the ``add`` action which appends without
        rewriting, so existing content is never clobbered.
        """
        path = self._path_for(target)
        bak = None if skip_drift else self._detect_external_drift(target)
        fresh = self._read_file(path)
        fresh = list(dict.fromkeys(fresh))  # deduplicate
        self._set_entries(target, fresh)
        return bak

    def save_to_disk(self, target: str):
        """Persist entries to the appropriate file. Called after every mutation."""
        path = self._path_for(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_file(path, self._entries_for(target))

    def _entries_for(self, target: str) -> List[str]:
        if target == "user":
            return self.user_entries
        if target == "global":
            return self.global_entries
        if target == "status":
            return self.status_entries
        return self.memory_entries

    def _set_entries(self, target: str, entries: List[str]):
        if target == "user":
            self.user_entries = entries
        elif target == "global":
            self.global_entries = entries
        elif target == "status":
            self.status_entries = entries
        else:
            self.memory_entries = entries

    def _char_count(self, target: str) -> int:
        entries = self._entries_for(target)
        if not entries:
            return 0
        return len(ENTRY_DELIMITER.join(entries))

    def _char_limit(self, target: str) -> int:
        """The per-scope budget — three configurable numbers, nothing hard-coded.

        ``memory.agent_char_limit`` / ``memory.global_char_limit`` /
        ``memory.user_char_limit`` (plus ``memory.status_char_limit`` for the
        per-agent STATUS file and ``memory.l1_status_char_limit`` for the digest
        L1 reads).  Defaults come from ``DEFAULT_CONFIG`` in
        :mod:`plobi_cli.config`; this class only carries the numbers.
        """
        if target == "user":
            return self.user_char_limit
        if target == "global":
            return self.global_char_limit
        if target == "status":
            return self.status_char_limit
        return self.memory_char_limit

    def _at_capacity(self, target: str) -> bool:
        """The written-down 下沉条件 (裁定 156「定死」的那一句)：这一格已经挤到压线上。

        过了压线 ⇒ 为了让位而离开 L0 的内容是**被挤出去的**，升 L1 等人批；
        没到压线的 remove 是「这条错了，删掉」，那是正常编辑，不许灌进待确认层
        （否则「人点头」那道闸会被日常改写淹没，判据②的反面就在这儿）。

        压线是配置（``memory.squeeze_pressure``），不是代码里的魔数。
        """
        limit = self._char_limit(target)
        if limit <= 0:
            return False
        return self._char_count(target) >= limit * self.squeeze_pressure

    def sink_displaced(self, target: str, entries: List[str], *, reason: str) -> int:
        """Hand what left L0 to the approval layer instead of losing it (裁定 156).

        L0 is 工作记忆 — bounded by design, and 装不下就合并或降级 (§10.2). The
        downgrade has to land somewhere, and that somewhere is the existing
        待确认层 (``tools/write_approval`` pending store, reviewed by
        ``/memory pending`` / approve) — not a second queue invented here. The
        staged payload is a write into ``global``, carrying the sha256 of
        GLOBAL.md as it stands so approval stays bound to real bytes.

        Scopes that are already shared (``global``) or disposable (``status``)
        do not sink: the first has nowhere above it, the second is a digest.
        Returns how many entries were staged (the reading 判据② asks for).
        """
        if target in ("global", "status"):
            return 0
        fresh = [entry for entry in entries if entry.strip()]
        if not fresh:
            return 0
        try:
            from tools import write_approval as wa
        except Exception:
            logger.warning("memory: L1 sink unavailable, %d displaced entry(ies) lost", len(fresh))
            return 0
        base_sha = _file_sha(self._path_for("global"))
        staged = 0
        for entry in fresh:
            record = wa.stage_write(
                wa.MEMORY,
                {"action": "add", "target": "global", "content": entry,
                 "base_sha256": base_sha, "squeezed_from": f"{target}:{reason}"},
                summary=f"squeezed out of {target}: {entry[:120]}",
                origin=wa.current_origin(),
            )
            staged += 1 if record.get("id") else 0
        if staged:
            logger.info(
                "memory: staged %d displaced entry(ies) from %s for approval (L0 -> L1)",
                staged, target,
            )
        return staged

    def rewrite(self, target: str, entries: List[str]) -> Dict[str, Any]:
        """Make one scope hold **exactly** these entries (memory_write's whole-location write).

        Whole-location semantics are what let the tool surface stay atomic: there is no
        add/replace/remove selector for the model to guess right (裁定 185), just
        "read it, edit it, write it back". Consolidation and adding therefore happen in
        the same call, and the final char budget is the only gate.

        Guards, in order: an empty rewrite of the user profile is refused (裁 156 的
        硬边界「不改历史画像」——清空画像必须是显式删除，不是写一个空表); every incoming
        entry is threat-scanned before anything is written; the batch is all-or-nothing
        against the final size, and an over-budget refusal shows the live inventory so
        the next call can trim and add together.
        """
        clean = [str(entry).strip() for entry in (entries or []) if str(entry).strip()]
        clean = list(dict.fromkeys(clean))          # order-preserving dedupe

        if target == "user" and not clean and self.user_entries:
            return {
                "success": False,
                "error": (
                    "Refusing to empty the user profile in one write. Removing a fact "
                    "about the person is an explicit remove, not a side effect of "
                    "rewriting the list."
                ),
            }
        for entry in clean:
            scan_error = _scan_memory_content(entry)
            if scan_error:
                return {"success": False, "error": f"Write rejected before touching disk: {scan_error}"}

        limit = self._char_limit(target)
        final_total = len(ENTRY_DELIMITER.join(clean)) if clean else 0
        if final_total > limit:
            current = self._char_count(target)
            return self._consolidation_failure({
                "success": False,
                "error": (
                    f"{target} would hold {final_total:,}/{limit:,} chars — over the cap, "
                    f"so nothing was written (current {current:,} chars). Merge or drop "
                    "entries in the list you send and write again; the cap is checked only "
                    "on the final content."
                ),
                "current_entries": self._entries_for(target),
                "usage": f"{current:,}/{limit:,}",
            })

        with self._file_lock(self._path_for(target)):
            self._reload_target(target)
            displaced = [e for e in self._entries_for(target) if e not in clean]
            self._set_entries(target, clean)
            self.save_to_disk(target)

        result = self._success_response(target, f"{target} now holds {len(clean)} entries.")
        result["scope"] = target
        result["dropped"] = displaced
        return result

    def add(self, target: str, content: str) -> Dict[str, Any]:
        """Append a new entry. Returns error if it would exceed the char limit."""
        content = content.strip()
        if not content:
            return {"success": False, "error": "Content cannot be empty."}

        # Scan for injection/exfiltration before accepting
        scan_error = _scan_memory_content(content)
        if scan_error:
            return {"success": False, "error": scan_error}

        with self._file_lock(self._path_for(target)):
            # Re-read from disk under lock to pick up writes from other sessions.
            # For add (append-only), we skip the drift guard — appending never
            # clobbers existing content, so round-trip mismatches from prior
            # tool-written entries in the same session are harmless.  The drift
            # guard remains active for replace/remove where full-file rewrite
            # would discard un-roundtrippable content (issue #26045).
            self._reload_target(target, skip_drift=True)

            entries = self._entries_for(target)
            limit = self._char_limit(target)

            # Reject exact duplicates
            if content in entries:
                return self._success_response(target, "Entry already exists (no duplicate added).")

            # Calculate what the new total would be
            new_entries = entries + [content]
            new_total = len(ENTRY_DELIMITER.join(new_entries))

            if new_total > limit:
                current = self._char_count(target)
                return self._consolidation_failure({
                    "success": False,
                    "error": (
                        f"Memory at {current:,}/{limit:,} chars. "
                        f"Adding this entry ({len(content)} chars) would exceed the limit. "
                        f"Consolidate now: use 'replace' to merge overlapping entries into "
                        f"shorter ones or 'remove' stale or less important entries (see "
                        f"current_entries below), then retry this add — all in this turn."
                    ),
                    "current_entries": entries,
                    "usage": f"{current:,}/{limit:,}",
                })

            entries.append(content)
            self._set_entries(target, entries)
            self.save_to_disk(target)

        return self._success_response(target, "Entry added.")

    def replace(self, target: str, old_text: str, new_content: str) -> Dict[str, Any]:
        """Find entry containing old_text substring, replace it with new_content."""
        old_text = old_text.strip()
        new_content = new_content.strip()
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty."}
        if not new_content:
            return {"success": False, "error": "new_content cannot be empty. Use 'remove' to delete entries."}

        # Scan replacement content for injection/exfiltration
        scan_error = _scan_memory_content(new_content)
        if scan_error:
            return {"success": False, "error": scan_error}

        with self._file_lock(self._path_for(target)):
            bak = self._reload_target(target)
            if bak:
                return _drift_error(self._path_for(target), bak)

            entries = self._entries_for(target)
            squeeze = self._at_capacity(target)
            matches = [(i, e) for i, e in enumerate(entries) if old_text in e]

            if not matches:
                return self._consolidation_failure({
                    "success": False,
                    "error": f"No entry matched '{old_text}'. Check current_entries below and retry with the exact text of the entry you want to replace.",
                    "current_entries": entries,
                })

            if len(matches) > 1:
                # If all matches are identical (exact duplicates), operate on the first one
                unique_texts = {e for _, e in matches}
                if len(unique_texts) > 1:
                    previews = self._previews([e for _, e in matches])
                    return {
                        "success": False,
                        "error": f"Multiple entries matched '{old_text}'. Be more specific.",
                        "matches": previews,
                    }
                # All identical -- safe to replace just the first

            idx = matches[0][0]
            limit = self._char_limit(target)

            # Check that replacement doesn't blow the budget
            test_entries = entries.copy()
            test_entries[idx] = new_content
            new_total = len(ENTRY_DELIMITER.join(test_entries))

            if new_total > limit:
                current = self._char_count(target)
                return self._consolidation_failure({
                    "success": False,
                    "error": (
                        f"Replacement would put memory at {new_total:,}/{limit:,} chars. "
                        f"Shorten the new content, or 'remove' other stale or less important "
                        f"entries to make room (see current_entries below), then retry — all "
                        f"in this turn."
                    ),
                    "current_entries": entries,
                    "usage": f"{current:,}/{limit:,}",
                })

            displaced = entries[idx]
            entries[idx] = new_content
            self._set_entries(target, entries)
            self.save_to_disk(target)
            if squeeze and len(new_content) < len(displaced):
                # Consolidation under pressure threw information away; that is the
                # squeeze 裁定 156 says must not be a silent loss. A rewording that
                # keeps or grows the text lost nothing, so it does not sink.
                self.sink_displaced(target, [displaced], reason="replace")

        return self._success_response(target, "Entry replaced.")

    def remove(self, target: str, old_text: str) -> Dict[str, Any]:
        """Remove the entry containing old_text substring."""
        old_text = old_text.strip()
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty."}

        with self._file_lock(self._path_for(target)):
            bak = self._reload_target(target)
            if bak:
                return _drift_error(self._path_for(target), bak)

            entries = self._entries_for(target)
            # Snapshot capacity BEFORE this mutation: an entry that leaves a full
            # scope was squeezed out (裁定 156), one that leaves a roomy scope was
            # deliberately deleted. Only the first sinks to L1.
            squeeze = self._at_capacity(target)
            matches = [(i, e) for i, e in enumerate(entries) if old_text in e]

            if not matches:
                return self._consolidation_failure({
                    "success": False,
                    "error": f"No entry matched '{old_text}'. Check current_entries below and retry with the exact text of the entry you want to remove.",
                    "current_entries": entries,
                })

            if len(matches) > 1:
                # If all matches are identical (exact duplicates), remove the first one
                unique_texts = {e for _, e in matches}
                if len(unique_texts) > 1:
                    previews = self._previews([e for _, e in matches])
                    return {
                        "success": False,
                        "error": f"Multiple entries matched '{old_text}'. Be more specific.",
                        "matches": previews,
                    }
                # All identical -- safe to remove just the first

            idx = matches[0][0]
            displaced = entries.pop(idx)
            self._set_entries(target, entries)
            self.save_to_disk(target)
            if squeeze:
                self.sink_displaced(target, [displaced], reason="remove")

        return self._success_response(target, "Entry removed.")

    def apply_batch(self, target: str, operations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Apply a sequence of add/replace/remove ops to one target atomically.

        All operations are validated and applied against the FINAL budget --
        intermediate overflow is irrelevant. This lets the model free space
        (remove/replace) and add new entries in a SINGLE tool call instead of
        the multi-turn consolidate-then-retry dance that re-sends the whole
        conversation context several times.

        Semantics: all-or-nothing. If any op is malformed, doesn't match, or
        the net result would exceed the char limit, NOTHING is written and an
        error is returned describing the first failure plus the live state.
        """
        if not operations:
            return {"success": False, "error": "operations list is empty."}

        # Scan every add/replace content for injection/exfil BEFORE touching
        # disk -- a single poisoned op rejects the whole batch.
        for i, op in enumerate(operations):
            act = (op or {}).get("action")
            new_content = (op or {}).get("content")
            if act in {"add", "replace"} and new_content:
                scan_error = _scan_memory_content(new_content)
                if scan_error:
                    return {"success": False, "error": f"Operation {i + 1}: {scan_error}"}

        with self._file_lock(self._path_for(target)):
            bak = self._reload_target(target)
            if bak:
                return _drift_error(self._path_for(target), bak)

            # Work on a copy; only commit if the whole batch validates.
            working: List[str] = list(self._entries_for(target))
            before_entries: List[str] = list(working)
            squeeze = self._at_capacity(target)
            limit = self._char_limit(target)

            for i, op in enumerate(operations):
                op = op or {}
                act = op.get("action")
                content = (op.get("content") or "").strip()
                old_text = (op.get("old_text") or "").strip()
                pos = f"Operation {i + 1} ({act or 'unknown'})"

                if act == "add":
                    if not content:
                        return self._batch_error(target, f"{pos}: content is required.")
                    if content in working:
                        continue  # idempotent -- skip duplicate, don't fail the batch
                    working.append(content)

                elif act == "replace":
                    if not old_text:
                        return self._batch_error(target, f"{pos}: old_text is required.")
                    if not content:
                        return self._batch_error(
                            target,
                            f"{pos}: content is required (use action='remove' to delete).",
                        )
                    matches = [j for j, e in enumerate(working) if old_text in e]
                    if not matches:
                        return self._batch_error(target, f"{pos}: no entry matched '{old_text}'.")
                    if len({working[j] for j in matches}) > 1:
                        return self._batch_error(
                            target,
                            f"{pos}: '{old_text}' matched multiple distinct entries -- be more specific.",
                        )
                    working[matches[0]] = content

                elif act == "remove":
                    if not old_text:
                        return self._batch_error(target, f"{pos}: old_text is required.")
                    matches = [j for j, e in enumerate(working) if old_text in e]
                    if not matches:
                        return self._batch_error(target, f"{pos}: no entry matched '{old_text}'.")
                    if len({working[j] for j in matches}) > 1:
                        return self._batch_error(
                            target,
                            f"{pos}: '{old_text}' matched multiple distinct entries -- be more specific.",
                        )
                    working.pop(matches[0])

                else:
                    return self._batch_error(
                        target,
                        f"{pos}: unknown action. Use add, replace, or remove.",
                    )

            # Budget check against the FINAL state only.
            new_total = len(ENTRY_DELIMITER.join(working)) if working else 0
            if new_total > limit:
                current = self._char_count(target)
                return self._consolidation_failure({
                    "success": False,
                    "error": (
                        f"After applying all {len(operations)} operations, memory would be at "
                        f"{new_total:,}/{limit:,} chars -- over the limit. Remove or shorten more "
                        f"entries in the same batch (see current_entries below), then retry."
                    ),
                    "current_entries": self._entries_for(target),
                    "usage": f"{current:,}/{limit:,}",
                })

            # Displacement ledger: what left the scope between the pre-image and
            # the final state. Under capacity pressure that is the squeeze
            # (裁定 156 — 被挤出去的条目升 L1), and it must not be a silent loss.
            displaced = [entry for entry in before_entries if entry not in working]
            squeezed_here = squeeze and bool(displaced)

            # Commit.
            self._set_entries(target, working)
            self.save_to_disk(target)
            if squeezed_here:
                self.sink_displaced(target, displaced, reason="batch")

        result = self._success_response(target, f"Applied {len(operations)} operation(s).")
        if squeezed_here:
            result["squeezed_to_l1"] = len(displaced)
        return result

    def _batch_error(self, target: str, message: str) -> Dict[str, Any]:
        """Build a batch-abort error that reports live (uncommitted) state."""
        current = self._char_count(target)
        limit = self._char_limit(target)
        return self._consolidation_failure({
            "success": False,
            "error": message + " No operations were applied (batch is all-or-nothing).",
            "current_entries": self._entries_for(target),
            "usage": f"{current:,}/{limit:,}",
        })

    def format_for_system_prompt(self, target: str) -> Optional[str]:
        """
        Return the frozen snapshot for system prompt injection.

        This returns the state captured at load_from_disk() time, NOT the live
        state. Mid-session writes do not affect this. This keeps the system
        prompt stable across all turns, preserving the prefix cache.

        Returns None if the snapshot is empty (no entries at load time).
        """
        block = self._system_prompt_snapshot.get(target, "")
        return block if block else None

    # -- Internal helpers --

    @staticmethod
    def _previews(entries: List[str], width: int = 80) -> List[str]:
        """Truncated one-line previews of entries for error feedback."""
        return [e[:width] + ("..." if len(e) > width else "") for e in entries]

    def _success_response(self, target: str, message: str = None) -> Dict[str, Any]:
        # A successful write means the consolidation loop made progress, so the
        # per-turn failure budget resets (the cap counts consecutive failures,
        # not lifetime ones within a turn) (#42405).
        self._consolidation_failures = 0
        entries = self._entries_for(target)
        current = self._char_count(target)
        limit = self._char_limit(target)
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0

        # The success response is intentionally TERMINAL: it confirms the write
        # landed and tells the model to stop. We do NOT echo the full entries
        # list here -- dumping it invites the model to "find more to fix" and
        # re-issue the same operations (observed thrash: the correct batch on
        # call 1, then 5 redundant repeats). Entries are only shown on the
        # error/over-budget paths, where the model genuinely needs them to
        # decide what to consolidate.
        resp = {
            "success": True,
            "done": True,
            "target": target,
            "usage": f"{pct}% — {current:,}/{limit:,} chars",
            "entry_count": len(entries),
        }
        if message:
            resp["message"] = message
        resp["note"] = "Write saved. This update is complete — do not repeat it."
        return resp

    def _render_block(self, target: str, entries: List[str]) -> str:
        """Render a system prompt block with header and usage indicator."""
        if not entries:
            return ""

        limit = self._char_limit(target)
        content = ENTRY_DELIMITER.join(entries)
        current = len(content)
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0

        if target == "user":
            title = "USER PROFILE (who the user is)"
        elif target == "global":
            title = ("GLOBAL MEMORY (facts that hold for every agent — written only "
                     "through the approval layer)")
        elif target == "status":
            title = f"MY STATUS ({self.slug})"
        else:
            title = f"AGENT MEMORY ({self.slug} — your own notes)"
        header = f"{title} [{pct}% — {current:,}/{limit:,} chars]"

        separator = "═" * 46
        return f"{separator}\n{header}\n{separator}\n{content}"

    @staticmethod
    def _read_file(path: Path) -> List[str]:
        """Read a memory file and split into entries.

        No file locking needed: _write_file uses atomic rename, so readers
        always see either the previous complete file or the new complete file.
        """
        if not path.exists():
            return []
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, IOError):
            return []

        if not raw.strip():
            return []

        # Use ENTRY_DELIMITER for consistency with _write_file. Splitting by "§"
        # alone would incorrectly split entries that contain "§" in their content.
        entries = [e.strip() for e in raw.split(ENTRY_DELIMITER)]
        return [e for e in entries if e]

    def _detect_external_drift(self, target: str) -> Optional[str]:
        """Return a backup-path string if on-disk content shows external drift.

        The memory file is supposed to be a list of small entries the tool
        wrote, joined by §. Detect drift via two signals:

        1. Round-trip mismatch — re-parsing and re-serializing the file
           doesn't produce identical bytes (rare; would catch oddly-encoded
           delimiters).
        2. Entry-size overflow — any single parsed entry exceeds the
           store's whole-file char limit. The tool budgets the ENTIRE store
           against that limit; no single tool-written entry can exceed it.
           When we see one entry larger than the limit, an external writer
           (patch tool, shell append, manual edit, sister session) appended
           free-form content into what the tool will treat as one entry.
           Flushing would then truncate that entry to the model's new
           content, discarding the appended bytes — issue #26045.

        Returns the absolute path of the .bak file when drift was found and
        backed up; returns None when the file looks tool-shaped.

        Note: this is an INSTANCE method (not static) because we need the
        per-target char_limit for signal #2.
        """
        path = self._path_for(target)
        if not path.exists():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, IOError):
            return None
        if not raw.strip():
            return None

        parsed = [e.strip() for e in raw.split(ENTRY_DELIMITER) if e.strip()]
        roundtrip = ENTRY_DELIMITER.join(parsed)

        char_limit = self._char_limit(target)
        max_entry_len = max((len(e) for e in parsed), default=0)

        drift_detected = (raw.strip() != roundtrip) or (max_entry_len > char_limit)
        if not drift_detected:
            return None

        # Drift confirmed — snapshot the file so the operator can recover
        # whatever the external writer added, then return the .bak path so
        # the caller can refuse the mutation.
        ts = int(time.time())
        bak_path = path.with_suffix(path.suffix + f".bak.{ts}")
        try:
            bak_path.write_text(raw, encoding="utf-8")
        except (OSError, IOError):
            return str(bak_path) + " (BACKUP FAILED — file unchanged on disk)"
        return str(bak_path)

    @staticmethod
    def _write_file(path: Path, entries: List[str]):
        """Write entries to a memory file using atomic temp-file + rename.

        Previous implementation used open("w") + flock, but "w" truncates the
        file *before* the lock is acquired, creating a race window where
        concurrent readers see an empty file. Atomic rename avoids this:
        readers always see either the old complete file or the new one.
        """
        content = ENTRY_DELIMITER.join(entries) if entries else ""
        try:
            # Write to temp file in same directory (same filesystem for atomic rename)
            fd, tmp_path = tempfile.mkstemp(
                dir=str(path.parent), suffix=".tmp", prefix=".mem_"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
                atomic_replace(tmp_path, path)
            except BaseException:
                # Clean up temp file on any failure
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except (OSError, IOError) as e:
            raise RuntimeError(f"Failed to write memory file {path}: {e}")


#: The one place the four memory budgets are read from config.  ``agent_char_limit``
#: is this agent's own ledger (it used to be called ``memory_char_limit``, before
#: memory had a scope dimension); ``global_char_limit`` is the shared layer;
#: ``user_char_limit`` is the person's profile.  Nothing here is hard-coded at a
#: call site — 任务书第 2 步「三份各自可配、不许硬编」.
_MEMORY_LIMIT_KEYS = {
    "memory_char_limit": "agent_char_limit",
    "global_char_limit": "global_char_limit",
    "user_char_limit": "user_char_limit",
    "status_char_limit": "status_char_limit",
    "l1_status_char_limit": "l1_status_char_limit",
    "squeeze_pressure": "squeeze_pressure",
}
_MEMORY_LIMIT_DEFAULTS = {
    "memory_char_limit": 2200,
    "global_char_limit": 2200,
    "user_char_limit": 1375,
    "status_char_limit": 200,
    "l1_status_char_limit": 1500,
    "squeeze_pressure": 0.9,
}


def memory_limits_from_config(mem_config: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    """Resolve the per-scope char limits from a ``memory:`` config section.

    Pass the already-loaded section (the live agent has it from ``agent_init``);
    with nothing passed it loads the config itself.  Bad values fall back to the
    shipped defaults so a hand-edited config can never take memory down.
    """
    if mem_config is None:
        try:
            from plobi_cli.config import load_config

            mem_config = (load_config() or {}).get("memory", {}) or {}
        except Exception:
            mem_config = {}
    limits: Dict[str, int] = {}
    for arg_name, config_key in _MEMORY_LIMIT_KEYS.items():
        default = _MEMORY_LIMIT_DEFAULTS[arg_name]
        try:
            raw = (mem_config or {}).get(config_key, default)
            limits[arg_name] = float(raw) if arg_name == "squeeze_pressure" else int(raw)
        except (TypeError, ValueError):
            limits[arg_name] = default
    return limits


def load_on_disk_store(slug: Optional[str] = None) -> "MemoryStore":
    """Build a fresh on-disk :class:`MemoryStore`, honoring configured char limits.

    Use this from any context that has no live agent (the messaging gateway, the
    Desktop GUI, the bare CLI ``/memory`` handler) but still needs to read or
    apply approved memory writes. Mirrors how the live agent constructs its store
    in ``agent/agent_init.py`` — both go through :func:`memory_limits_from_config`,
    so an approval applied without a live agent enforces the SAME caps as one
    applied with one.

    Falls back to the built-in defaults if config can't be loaded, so this can
    never raise on a missing/unreadable config.
    """
    store = MemoryStore(slug=slug, **memory_limits_from_config())
    store.load_from_disk()
    return store


def _scope_label(target: str) -> str:
    """Human-readable name of a memory scope, used in approval summaries."""
    return {
        "user": "user profile",
        "global": "GLOBAL memory (shared by every agent)",
        "status": "agent STATUS digest",
    }.get(target, "memory")


def _apply_write_gate(action: str, target: str, content: Optional[str],
                      old_text: Optional[str]) -> Optional[str]:
    """Evaluate the memory write gate. Returns a JSON tool-result string when
    the write should NOT proceed normally (blocked or staged), or None when the
    caller should perform the real write.

    Only the mutating actions (add/replace/remove) are gated.
    """
    if action not in {"add", "replace", "remove"}:
        return None

    try:
        from tools import write_approval as wa
    except Exception:
        # If the gate module can't load, fail open (current behaviour) rather
        # than blocking all memory writes.
        return None

    # Build a small inline summary/detail for the foreground approval prompt.
    label = _scope_label(target)
    if action == "add":
        summary = f"add to {label}"
        detail = content or ""
    elif action == "replace":
        summary = f"replace in {label}"
        detail = f"old: {old_text}\nnew: {content}"
    else:  # remove
        summary = f"remove from {label}"
        detail = old_text or ""

    decision = wa.evaluate_gate(wa.MEMORY, inline_summary=summary, inline_detail=detail)

    if decision.allow:
        return None

    if decision.blocked:
        return tool_error(decision.message, success=False)

    # stage
    payload = {
        "action": action,
        "target": target,
        "content": content,
        "old_text": old_text,
    }
    record = wa.stage_write(
        wa.MEMORY, payload,
        summary=f"{summary}: {detail[:120]}",
        origin=wa.current_origin(),
    )
    return json.dumps(
        {"success": True, "staged": True, "pending_id": record["id"],
         "message": decision.message},
        ensure_ascii=False,
    )


def _apply_batch_write_gate(target: str, operations: List[Dict[str, Any]]) -> Optional[str]:
    """Evaluate the write gate for a batch of memory operations.

    Returns a JSON tool-result string when the batch should NOT proceed
    (blocked or staged), or None when the caller should perform the real
    batch write. The whole batch is gated as a single unit.
    """
    try:
        from tools import write_approval as wa
    except Exception:
        return None

    label = "user profile" if target == "user" else "memory"
    summary = f"apply {len(operations)} op(s) to {label}"
    detail_lines = []
    for op in operations:
        op = op or {}
        act = op.get("action", "?")
        if act == "remove":
            detail_lines.append(f"- remove: {op.get('old_text', '')}")
        elif act == "replace":
            detail_lines.append(f"- replace: {op.get('old_text', '')} -> {op.get('content', '')}")
        else:
            detail_lines.append(f"- {act}: {op.get('content', '')}")
    detail = "\n".join(detail_lines)

    decision = wa.evaluate_gate(wa.MEMORY, inline_summary=summary, inline_detail=detail)

    if decision.allow:
        return None

    if decision.blocked:
        return tool_error(decision.message, success=False)

    payload = {"action": "batch", "target": target, "operations": operations}
    record = wa.stage_write(
        wa.MEMORY, payload,
        summary=f"{summary}: {detail[:120]}",
        origin=wa.current_origin(),
    )
    return json.dumps(
        {"success": True, "staged": True, "pending_id": record["id"],
         "message": decision.message},
        ensure_ascii=False,
    )


def _missing_old_text_error(store: "MemoryStore", target: str, action: str) -> str:
    """Build a recoverable error for a replace/remove call that arrived without
    ``old_text``.

    ``replace``/``remove`` are inherently targeted -- without ``old_text`` there
    is no entry to act on, so we cannot fulfil the call. But returning a bare
    "old_text is required" is a dead-end: some structured-output clients omit the
    optional ``old_text`` field (it isn't, and can't be, schema-required without
    a top-level combinator the Codex backend rejects -- see
    tests/tools/test_memory_tool_schema.py). So instead we return the current
    entry inventory plus an explicit retry instruction, letting the model reissue
    the call with ``old_text`` set to a unique substring of the entry it means.
    Mirrors the batch path's ``_batch_error`` shape. (issues #43412, #49466)
    """
    entries = store._entries_for(target)
    current = store._char_count(target)
    limit = store._char_limit(target)
    return json.dumps(
        {
            "success": False,
            "error": (
                f"'{action}' needs old_text -- a short unique substring of the entry "
                f"to {action}. None was provided. Reissue the {action} with old_text "
                f"set to part of one of the current_entries below."
            ),
            "current_entries": entries,
            "usage": f"{current:,}/{limit:,}",
        },
        ensure_ascii=False,
    )


def _stage_global_write(action: str, content: Optional[str], old_text: Optional[str],
                        operations: Optional[List[Dict[str, Any]]],
                        store: "MemoryStore") -> str:
    """A write aimed at ``GLOBAL.md`` always lands in the approval layer first.

    裁定 181: 默认写自己那份，只有「跨 Agent 都成立的事实」才进全局，而且**走已有的
    待确认层**（``tools/write_approval`` 的 pending store + ``/memory approve``），
    不新造机制。裁定 185 的尺子在这儿的落法：模型递的还是同一件原子事「把这条写进
    这个作用域」，没多一个要它猜对的分支参数——**要人点头是这个位置的规则**，与
    ``memory.write_approval`` 那道全局闸无关，也不管那道闸开不开。

    The response is the dry-run product: what will be written, the sha256 of
    ``GLOBAL.md`` as it stands (what the approval is bound to —
    :func:`apply_memory_pending` refuses if those bytes moved), and the pending id.
    """
    if action == "batch":
        gist = "; ".join(
            f"{(op or {}).get('action', '?')}: "
            f"{(op or {}).get('content') or (op or {}).get('old_text') or ''}"
            for op in (operations or [])
        )
    else:
        gist = (content or "") if action != "remove" else (old_text or "")
        if action in {"add", "replace"}:
            scan_error = _scan_memory_content(content or "")
            if scan_error:
                return tool_error(scan_error, success=False)
    if not gist.strip():
        return tool_error("Nothing to stage — content is required for this write.", success=False)

    try:
        from tools import write_approval as wa
    except Exception:
        return tool_error("Approval layer unavailable — the write was not staged.", success=False)

    payload: Dict[str, Any] = {"action": action, "target": "global"}
    if action == "batch":
        payload["operations"] = operations or []
    else:
        payload["content"] = content or ""
        payload["old_text"] = old_text or ""
    payload["base_sha256"] = _file_sha(store._path_for("global"))

    record = wa.stage_write(
        wa.MEMORY, payload,
        summary=f"write to GLOBAL.md ({action}): {gist[:120]}",
        origin=wa.current_origin(),
    )
    return json.dumps(
        {
            "success": True,
            "staged": True,
            "done": True,
            "pending_id": record["id"],
            "base_sha256": payload["base_sha256"],
            "plan_sha256": hashlib.sha256(gist.encode("utf-8")).hexdigest(),
            "message": (
                "GLOBAL.md is the layer every agent reads: this write is staged for "
                "approval and changed nothing on disk. Review with /memory pending, "
                f"then /memory approve {record['id']}."
            ),
            "note": "Staged is terminal for this call — do not repeat the write.",
        },
        ensure_ascii=False,
    )


def memory_tool(
    action: str = None,
    target: str = "memory",
    content: str = None,
    old_text: str = None,
    source: str = None,
    operations: Optional[List[Dict[str, Any]]] = None,
    store: Optional[MemoryStore] = None,
) -> str:
    """
    Single entry point for the memory tool. Dispatches to MemoryStore methods.

    Three shapes:
      - Single op: action + (content / old_text), optional source pointer.
      - Batch:     operations=[{action, content?, old_text?}, ...] applied
                   atomically against the final char budget in ONE call.
      - Global:    target="global" — the same verb, but that location's rule is
                   plan + hash + a human nod, so it stages instead of writing.

    Returns JSON string with results.
    """
    if store is None:
        return tool_error("Memory is not available. It may be disabled in config or this environment.", success=False)

    # Some strict providers fill optional schema fields with JSON null rather
    # than omitting them.  Treat ``target: null`` as omitted so memory writes
    # still use the documented default store instead of failing validation.
    if target is None:
        target = "memory"

    # ``target`` is a data location, not a business branch (裁定 185): the four
    # scopes are the four files 裁定 181 laid out, and one verb covers them all.
    if target not in MEMORY_SCOPES:
        return tool_error(
            f"Invalid target {target!r}. Use 'memory', 'user', 'status' or 'global'.",
            success=False,
        )

    # The provenance pointer travels inside the entry text, so every downstream
    # path (gate, staging, store) keeps seeing one opaque content string.
    if source:
        if action in {"add", "replace"} and content:
            content = entry_with_source(content.strip(), source)
        if operations:
            operations = [
                {**op, "content": entry_with_source((op.get("content") or "").strip(), source)}
                if op and op.get("action") in {"add", "replace"} and op.get("content")
                else op
                for op in operations
            ]

    # --- Batch path -------------------------------------------------------
    if operations:
        if not isinstance(operations, list):
            return tool_error("operations must be a list of {action, content?, old_text?} objects.", success=False)
        if not operations:
            return tool_error("operations list is empty.", success=False)
        if target == "global":
            return _stage_global_write("batch", content, old_text, operations, store)
        gate_result = _apply_batch_write_gate(target, operations)
        if gate_result is not None:
            return gate_result
        result = store.apply_batch(target, operations)
        return json.dumps(result, ensure_ascii=False)

    # --- Single-op path ---------------------------------------------------
    # Validate required params BEFORE any gate or staging so an invalid write is
    # rejected immediately instead of being staged and only failing at approve time.
    if action == "add" and not content:
        return tool_error("Content is required for 'add' action.", success=False)
    if action == "replace" and (not old_text or not content):
        missing = "old_text" if not old_text else "content"
        if not old_text:
            # The client/model omitted old_text. Replace is inherently targeted
            # -- we can't guess which entry. Return the current inventory plus a
            # retry instruction so the model can reissue with old_text set,
            # instead of hitting a dead-end error. (issues #43412, #49466)
            return _missing_old_text_error(store, target, "replace")
        return tool_error(f"{missing} is required for 'replace' action.", success=False)
    if action == "remove" and not old_text:
        return _missing_old_text_error(store, target, "remove")

    # Writing the shared layer is approval-only wherever it comes from (裁定 181).
    if target == "global":
        return _stage_global_write(action, content, old_text, None, store)

    # Approval gate: when on, stages the write (background/gateway) or prompts
    # inline (interactive CLI); when off (default) passes straight through.
    gate_result = _apply_write_gate(action, target, content, old_text)
    if gate_result is not None:
        return gate_result

    if action == "add":
        result = store.add(target, content)

    elif action == "replace":
        result = store.replace(target, old_text, content)

    elif action == "remove":
        result = store.remove(target, old_text)

    else:
        return tool_error(f"Unknown action '{action}'. Use: add, replace, remove", success=False)

    return json.dumps(result, ensure_ascii=False)


def check_memory_requirements() -> bool:
    """Memory tool has no external requirements -- always available."""
    return True


def apply_memory_pending(payload: Dict[str, Any], store: "MemoryStore") -> Dict[str, Any]:
    """Replay a staged memory write directly against the store, bypassing the
    write gate. Called by the /memory approve handler.

    Returns the store's result dict.

    A promotion carries the sha256 of the GLOBAL.md bytes it was approved
    against; if that file moved since, the approval no longer describes what is
    about to happen, so the write is refused instead of silently overwriting
    (same binding rule the brain's approval layer uses).
    """
    action = payload.get("action")
    target = payload.get("target", "memory")
    content = payload.get("content") or ""
    old_text = payload.get("old_text") or ""
    base_sha = payload.get("base_sha256")
    if base_sha and target in MEMORY_SCOPES and _file_sha(store._path_for(target)) != base_sha:
        return {
            "success": False,
            "error": (
                f"{store._path_for(target).name} changed since this write was staged "
                "(the approval was granted against a different version). Discard this "
                "pending record and stage it again."
            ),
        }
    if action == "batch":
        return store.apply_batch(target, payload.get("operations") or [])
    if action == "add":
        return store.add(target, content)
    if action == "replace":
        return store.replace(target, old_text, content)
    if action == "remove":
        return store.remove(target, old_text)
    return {"success": False, "error": f"Unknown staged action '{action}'."}
# ===========================================================================
# The two product-side atoms (裁定 185, generalizing 裁 183)
#
#   memory_read(scope)                      -> what that location holds right now
#   memory_write(scope, parts, source)      -> that location now holds exactly these
#
# `scope` is a data location (self / global / user / status), which 裁定 185 allows;
# there is no action/intent/mode selector, because every extra enum value is one more
# place the model must guess right to end up on the right branch — the 10-17 P0
# (``intent=plan_day`` taking another code path when ``action`` was omitted) is the
# evidence. Consolidation is the model composing read + write, not a business flow we
# encode in parameters. The legacy `memory` tool below stays registered (it is an
# upstream hand, and the approval replay path), but it is NOT the product surface.
# ===========================================================================

#: product-facing scope name -> the scope the store speaks
ATOMIC_SCOPES = {
    "self": "memory",
    "global": "global",
    "user": "user",
    "status": "status",
}


def _resolve_atomic_scope(scope: Optional[str]) -> str:
    """Normalize a requested location. Unknown words are refused, never guessed."""
    key = (scope or "self").strip().lower()
    if key == "memory":          # the store's own word for the agent's own scope
        key = "self"
    if key not in ATOMIC_SCOPES:
        raise ValueError(
            f"Unknown memory scope {scope!r}. Use 'self', 'global', 'user' or 'status'."
        )
    return ATOMIC_SCOPES[key]


def memory_read(scope: str = "self", store: Optional[MemoryStore] = None) -> str:
    """Read one memory location: its entries, usage against its own cap, and its path.

    What comes back is exactly what a later ``memory_write`` must send to keep it —
    the ``source`` pointer lives inside the entry text (裁定 181: memory is a bounded
    index, and the pointer is what makes 2200 chars enough).
    """
    try:
        target = _resolve_atomic_scope(scope)
    except ValueError as exc:
        return tool_error(str(exc), success=False)
    live = store or load_on_disk_store()
    return json.dumps(
        {
            "success": True,
            "scope": target,
            "agent": live.slug,
            "path": str(live._path_for(target)),
            "entries": list(live._entries_for(target)),
            "usage": f"{live._char_count(target):,}/{live._char_limit(target):,} chars",
        },
        ensure_ascii=False,
    )


def memory_write(scope: str = "self", parts: Optional[List[str]] = None,
                 source: str = None, store: Optional[MemoryStore] = None) -> str:
    """Make one memory location hold exactly ``parts``.

    Whole-location write on purpose: one verb, no selector to get wrong. Send back
    what you read, edited — including every entry you mean to keep. The char cap is
    checked against the FINAL content only, so trimming and adding are one call.
    ``source`` (a relative ``vault/…`` / ``mind/…`` path plus its date) is appended to
    each entry that has no pointer yet; the direction is memory -> the page a human
    reads, never the page -> memory to save space (裁定 182).

    ``global`` is the same verb, but that location's own rule is plan + hash + a human
    nod, so it stages into the pending layer and writes nothing until approved.
    """
    try:
        target = _resolve_atomic_scope(scope)
    except ValueError as exc:
        return tool_error(str(exc), success=False)

    entries = [entry_with_source(str(part).strip(), source)
               for part in (parts or []) if str(part).strip()]
    entries = list(dict.fromkeys(entries))
    live = store or load_on_disk_store()

    if target == "global":
        current = list(live._entries_for("global"))
        operations: List[Dict[str, Any]] = [
            {"action": "remove", "old_text": gone} for gone in current if gone not in entries
        ] + [
            {"action": "add", "content": new} for new in entries if new not in current
        ]
        if not operations:
            return json.dumps(
                {"success": True, "unchanged": True, "scope": "global",
                 "message": "GLOBAL.md already holds exactly this."},
                ensure_ascii=False,
            )
        return _stage_global_write("batch", None, None, operations, live)

    return json.dumps(live.rewrite(target, entries), ensure_ascii=False)


MEMORY_READ_SCHEMA = {
    "name": "memory_read",
    "description": (
        "Read one memory location: its exact entries, its own char usage, and the file it "
        "lives in. Four locations: 'self' = this agent's own notes; 'global' = the layer "
        "every agent reads; 'user' = who the person is (one copy); 'status' = your few "
        "lines of current state, which only the L1 secretary reads. Call this before "
        "memory_write — the write replaces the location's whole content, so anything you "
        "want to keep has to be sent back."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": ["self", "global", "user", "status"],
                "description": "Which memory location to read. Default 'self'.",
            },
        },
    },
}

MEMORY_WRITE_SCHEMA = {
    "name": "memory_write",
    "description": (
        "Write a memory location: after this call it holds exactly `parts`, in order. This "
        "is a whole-location write, not an append — read it first, then send the edited "
        "list, so one call can merge stale entries, drop what's wrong, and add the new fact "
        "together. Each location has its own char cap and the limit is checked on the FINAL "
        "content; an over-budget write is refused with the current entries shown, and "
        "nothing is dropped silently.\n\n"
        "'source' is how memory stays small: the relative path of the page a person can "
        "read (under vault/ or mind/) plus its date, e.g. "
        "'vault/projects/Plobi/plan.md 2026-10-19'. It is appended to each entry that has "
        "no pointer yet. Never paste the page's content or base64 into memory, and never "
        "rewrite the vault to point back at memory — memory points at the human-facing "
        "page, that direction is the design.\n\n"
        "Write 'self' by default. A fact that holds for every agent goes to 'global': same "
        "verb, but that location stages the change for approval and changes nothing on disk "
        "until someone nods, so 'staged' in the response is success, not a failure."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": ["self", "global", "user", "status"],
                "description": "Which memory location to write. Default 'self'.",
            },
            "parts": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "The complete set of entries this location should hold, in order. "
                    "Required by shape, so send [] deliberately: an empty list clears the "
                    "location, except 'user', where it is refused — deleting a fact about "
                    "the person is an explicit act, not a side effect of a rewrite."
                ),
            },
            "source": {
                "type": "string",
                "description": (
                    "Provenance pointer for these entries: a relative path under vault/ or "
                    "mind/ that a human can read, with its date. Appended to each entry "
                    "that has none."
                ),
            },
        },
        "required": ["parts"],
    },
}




# OpenAI Function-Calling Schema
# =============================================================================

MEMORY_SCHEMA = {
    "name": "memory",
    "description": (
        "Save durable facts to persistent memory that survive across sessions. Memory is "
        "injected into every future turn, so keep entries compact and high-signal.\n\n"
        "HOW: make ALL your changes in ONE call via an 'operations' array (each item: "
        "{action, content?, old_text?}). The batch applies atomically and the char limit is "
        "checked only on the FINAL result — so a single call can remove/replace stale entries "
        "to free room AND add new ones, even when an add alone would overflow. The response "
        "reports current/limit chars and confirms completion; one batch call finishes the "
        "update, so don't repeat it. Use the bare action/content/old_text fields only for a "
        "single lone change.\n\n"
        "WHEN: save proactively when the user states a preference, correction, or personal "
        "detail, or you learn a stable fact about their environment, conventions, or workflow. "
        "Priority: user preferences & corrections > environment facts > procedures. The best "
        "memory stops the user repeating themselves.\n\n"
        "IF FULL: an add is rejected with the current entries shown. Reissue as ONE batch that "
        "removes or shortens enough stale entries and adds the new one together.\n\n"
        "TARGETS: 'memory' = this agent's own notes (environment, conventions, tool quirks, "
        "lessons) — the default, and where writes land unless a fact holds for every agent. "
        "'user' = who the user is (name, role, preferences, style), one copy shared by every "
        "agent. 'status' = a few lines on where you are right now; only the L1 secretary reads "
        "those, as a digest. 'global' = the one layer every agent reads; writing it stages the "
        "change for approval and leaves the file untouched until a person nods.\n\n"
        "SOURCE: on every add/replace, pass 'source' = the relative path of the page a person "
        "can read (under vault/ or mind/) plus its date. Memory is a bounded index, not a "
        "warehouse; the pointer is what keeps 2200 chars enough.\n\n"
        "SKIP: trivial/obvious info, easily re-discovered facts, raw data dumps, task progress, "
        "completed-work logs, temporary TODO state (use session_search for those). Reusable "
        "procedures belong in a skill, not memory."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "replace", "remove"],
                "description": "The action to perform (single-op shape). Omit when using 'operations'."
            },
            "target": {
                "type": "string",
                "enum": ["memory", "user", "status", "global"],
                "description": "Which memory scope to write: 'memory' this agent's own notes, 'user' the shared user profile, 'status' a few lines of current state that only L1 reads, 'global' the layer every agent reads (that location's rule is approval, so the write is staged rather than applied)."
            },
            "content": {
                "type": "string",
                "description": "The entry content. Required for 'add' and 'replace' (single-op shape)."
            },
            "source": {
                "type": "string",
                "description": "Provenance pointer for add/replace: a relative path under vault/ or mind/ that a human can read, with its date (e.g. 'vault/projects/Plobi/plan.md 2026-10-19'). Memory stays small because it points at the page instead of storing it."
            },
            "old_text": {
                "type": "string",
                "description": "REQUIRED for 'replace' and 'remove' (single-op shape): a short unique substring identifying the existing entry to modify. Omit only for 'add'."
            },
            "operations": {
                "type": "array",
                "description": (
                    "Batch shape: a list of operations applied atomically in one call "
                    "against the final char budget. Preferred when making multiple changes "
                    "or consolidating to make room. Each item is {action, content?, old_text?}."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["add", "replace", "remove"]},
                        "content": {"type": "string", "description": "Entry content for add/replace."},
                        "old_text": {"type": "string", "description": "Substring identifying the entry for replace/remove."},
                    },
                    "required": ["action"],
                },
            },
        },
        "required": ["target"],
    },
}


# --- Registry ---
from tools.registry import registry, tool_error

registry.register(
    name="memory",
    toolset="memory",
    schema=MEMORY_SCHEMA,
    handler=lambda args, **kw: memory_tool(
        action=args.get("action", ""),
        target=args.get("target", "memory"),
        content=args.get("content"),
        old_text=args.get("old_text"),
        source=args.get("source"),
        operations=args.get("operations"),
        store=kw.get("store")),
    check_fn=check_memory_requirements,
    emoji="🧠",
)

# 两枚产品面原子（裁 185）——注册在同一个 registry import 之后，与上游那把 `memory` 手并列
registry.register(
    name="memory_read",
    toolset="memory",
    schema=MEMORY_READ_SCHEMA,
    handler=lambda args, **kw: memory_read(
        scope=args.get("scope", "self"), store=kw.get("store")),
    check_fn=check_memory_requirements,
    description="读一个记忆位置（self / global / user / status）",
    emoji="🧠",
)

registry.register(
    name="memory_write",
    toolset="memory",
    schema=MEMORY_WRITE_SCHEMA,
    handler=lambda args, **kw: memory_write(
        scope=args.get("scope", "self"),
        parts=args.get("parts") or [],
        source=args.get("source"),
        store=kw.get("store")),
    check_fn=check_memory_requirements,
    description="写一个记忆位置：整份替换＋出处指针；global 那一格走已有的待确认层",
    emoji="🧠",
)





