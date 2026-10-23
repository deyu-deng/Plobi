"""L1 — nothing reaches the brain without a plan, a hash, and a decision.

The contract this module exists to enforce (Docs/specs/memory-adapter.md §10.2):
L0 working memory and L2 canon are separated by an L1 that carries *计划 + 哈希 + 点头*.
Two properties matter more than the feature itself:

- **an approval is bound to the object it was granted against**. The approval hash
  covers the resolved roots (realpath + their own git toplevel + their HEAD), so a
  plan approved for one brain cannot be replayed onto a renamed or re-pointed
  ``MIND_ROOT``/``VAULT_ROOT``. Re-checked at apply time, not just at approve time.
- **each change carries the old bytes it expects to replace**. If the user edited
  the file meanwhile, the plan is stale and the write is refused rather than
  silently winning over a human.

Two things are never automatic, whatever the trust ledger says: deleting anything,
and rewriting the layers that hold the user's own record (diary, persona). Those
are decided by the ask path only — see :func:`never_auto`.

The ledger deliberately lives outside both brain repos (it is runtime state, not
canon): under ``PLOBI_HOME`` so nothing of it can leak into the user's notes or
get pushed anywhere.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

ABSENT = "absent"
MODES = ("overwrite", "append", "create")

# TTL of a pending plan: an expired plan is void, never applied late.
PLAN_TTL_SECONDS = 6 * 3600

# Trust ramp: adopted this many times in a row (with no reject/edit) before a
# category stops asking. Deliberately boring and inspectable -- the ramp must not
# become a score a model assigns to itself.
AUTO_THRESHOLD = 8


@dataclass(frozen=True)
class Change:
    """One proposed write, addressed by its LOGICAL path (``Vault/…`` / ``Loom/…``)."""

    logical_path: str
    content: str
    mode: str = "overwrite"

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"unknown write mode {self.mode!r}")


@dataclass(frozen=True)
class Denied:
    reason: str
    logical_path: str = ""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def root_identity(roots) -> dict[str, str]:
    """What the approval is bound to: path, repo toplevel, current head.

    ``toplevel`` and ``head`` make a re-pointed or re-cloned root a different
    object, which is what stops an approval from following a plan across machines
    or onto a directory that merely has the same name.
    """
    out: dict[str, str] = {}
    for label, base in (("mind", roots.mind), ("vault", roots.vault)):
        if base is None:
            out[label] = "none"
            continue
        top = _git(base, "rev-parse", "--show-toplevel")
        head = _git(base, "rev-parse", "HEAD")
        try:
            resolved = str(base.resolve())
        except OSError:
            resolved = str(base)
        out[label] = "|".join((resolved, top or "not-a-repo", head or "no-head"))
    return out


def _git(root: Path, *args: str) -> str:
    try:
        done = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def _target(roots, logical: str) -> Path:
    from plobi.mind.paths import real_target

    return Path(real_target(logical, roots))


def _current(roots, logical: str) -> Optional[str]:
    path = _target(roots, logical)
    try:
        return path.read_text(encoding="utf-8") if path.is_file() else None
    except OSError:
        return None


def never_auto(logical: str, mode: str) -> str:
    """The operations that are never automatic. Empty string means "could be".

    Keep this list short and literal: every entry here is a case where being
    wrong is not recoverable by the user, so a wrong automation is worse than an
    annoying question.
    """
    low = logical.lower()
    if mode not in MODES:
        return "unknown-mode"
    if low.endswith(".gitkeep"):
        return ""
    if "/diary/" in low or low.startswith("vault/diary"):
        return "rewrites-the-user's-own-record"
    if "/profile/" in low or "persona" in low:
        return "rewrites-the-persona"
    if low.startswith("vault/projects/mind") or low == "vault/projects/mind":
        return "deleted-project"
    return ""


@dataclass
class Plan:
    """A pending set of writes plus everything needed to re-check it later."""

    changes: list[Change]
    identity: dict[str, str]
    created_at: float = field(default_factory=time.time)
    old_shas: dict[str, str] = field(default_factory=dict)
    new_shas: dict[str, str] = field(default_factory=dict)
    categories: dict[str, str] = field(default_factory=dict)

    @property
    def digest(self) -> str:
        payload = json.dumps(
            {"identity": self.identity,
             "changes": [[c.logical_path, c.mode, self.old_shas[c.logical_path],
                          self.new_shas[c.logical_path]] for c in self.changes]},
            sort_keys=True, ensure_ascii=False)
        return _sha(payload)

    def to_json(self) -> str:
        return json.dumps({
            "digest": self.digest,
            "identity": self.identity,
            "created_at": self.created_at,
            "changes": [[c.logical_path, c.mode, c.content] for c in self.changes],
            "old_shas": self.old_shas, "new_shas": self.new_shas,
            "categories": self.categories,
        }, ensure_ascii=False)

    @classmethod
    def from_json(cls, blob: str) -> "Plan":
        raw = json.loads(blob)
        plan = cls(changes=[Change(p, c, m) for p, m, c in raw["changes"]],
                   identity=raw["identity"], created_at=raw["created_at"])
        plan.old_shas = raw["old_shas"]
        plan.new_shas = raw["new_shas"]
        plan.categories = raw.get("categories", {})
        return plan

    def human_summary(self) -> str:
        head = f"计划 {len(self.changes)} 项 · 摘要 {self.digest[:12]} · 对象 {self.identity.get('mind', '?')[:24]}"
        lines = [head] + [f"  {c.mode:<9} {c.logical_path}" for c in self.changes]
        return "\n".join(lines)


def build_plan(changes: Iterable[Change], *, roots, categories: Optional[dict] = None) -> Plan:
    """Snapshot current bytes so a later apply can detect that a human moved first."""
    from plobi.mind.paths import resolve_roots

    roots = roots if roots is not None else resolve_roots()
    plan = Plan(changes=list(changes), identity=root_identity(roots),
                categories=dict(categories or {}))
    for change in plan.changes:
        current = _current(roots, change.logical_path)
        plan.old_shas[change.logical_path] = ABSENT if current is None else _sha(current)
        plan.new_shas[change.logical_path] = _sha(change.content)
        plan.categories.setdefault(change.logical_path, category_of(change.logical_path))
    return plan


def category_of(logical: str) -> str:
    """Coarse class used by the trust ledger — derived from the path, never model-guessed."""
    low = logical.lower()
    for marker, name in (("vault/notes", "notes"), ("vault/diary", "diary"),
                         ("vault/meta/profile", "persona"), ("vault/projects", "project-docs"),
                         ("loom/raw", "digests"), ("loom/wiki", "wiki")):
        if marker in low or low.startswith(marker):
            return name
    return "other"


def approve(plan: Plan) -> str:
    """What the user actually said yes to. Pass this back to :func:`apply`."""
    return plan.digest


def is_expired(plan: Plan, *, now: Optional[float] = None, ttl: int = PLAN_TTL_SECONDS) -> bool:
    return (now if now is not None else time.time()) - plan.created_at > ttl


@dataclass
class ApplyReport:
    written: list[str] = field(default_factory=list)
    denied: list[Denied] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.denied and bool(self.written)


def apply_plan(plan: Plan, *, approved: str, roots, writer=None,
               now: Optional[float] = None) -> ApplyReport:
    """Write only what was approved, against the object it was approved for."""
    report = ApplyReport()
    if approved != plan.digest:
        report.denied.append(Denied("approval-mismatch: 批过的计划与要执行的不是同一份"))
        return report
    if is_expired(plan, now=now):
        report.denied.append(Denied("plan-expired: 计划过期作废，不静默补写"))
        return report
    current_identity = root_identity(roots)
    if current_identity != plan.identity:
        report.denied.append(Denied(
            "root-identity-changed: 批给的那个根已经变了（改名/重指向/新克隆都算），"
            "审批不跟着搬"))
        return report

    stale = [c.logical_path for c in plan.changes
             if _sha(_current(roots, c.logical_path) or "") != plan.old_shas[c.logical_path]
             and not (plan.old_shas[c.logical_path] == ABSENT and _current(roots, c.logical_path) is None)]
    if stale:
        report.denied.append(Denied("content-changed-by-a-human", ",".join(stale)))
        return report

    # never_auto() is not enforced here on purpose: by this point the user has
    # approved *this* plan by digest, which is exactly the explicit yes those paths
    # require. The rule lives on the automatic route (should_ask), where there is
    # no yes to point at.
    for change in plan.changes:
        result = _write(roots, change, writer)
        if getattr(result, "ok", True):
            report.written.append(change.logical_path)
        else:
            report.denied.append(Denied(getattr(result, "detail", "write-refused"),
                                        change.logical_path))
    return report


def _write(roots, change: Change, writer):
    if writer is not None:
        return writer.write_one(change.logical_path, change.content, mode=_mode_for(change))
    from plobi.mind import get_writer

    return get_writer().write_one(change.logical_path, change.content, mode=_mode_for(change))


def _mode_for(change: Change) -> str:
    return "append" if change.mode == "append" else "overwrite"


def dirty_repos(roots) -> list[str]:
    """Zero-pollution self-proof: a dry-run must leave both brain repos untouched."""
    dirty = []
    for base in (roots.mind, roots.vault):
        if base is None or not base.is_dir():
            continue
        if _git(base, "status", "--porcelain"):
            dirty.append(str(base))
    return dirty


@dataclass
class TrustEntry:
    category: str
    source: str
    adopted: int = 0
    edited: int = 0
    rejected: int = 0
    streak: int = 0
    last_reason: str = ""

    @property
    def key(self) -> str:
        return f"{self.category}/{self.source}"


class TrustLedger:
    """The moving threshold: asks a lot at first, less as it earns it, instantly more on any reject.

    Deliberately arithmetic over counters -- "the model decided it understands me"
    is not evidence. Every downgrade records a reason, so the user can read why a
    category went back to asking.
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = path or default_ledger_path()
        self.entries: dict[str, TrustEntry] = {}
        if self.path.is_file():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raw = {}
            for blob in raw.get("entries", []):
                blob.pop("key", None)      # tolerate a row written with the redundant key
                entry = TrustEntry(**blob)
                self.entries[entry.key] = entry

    def entry(self, category: str, source: str) -> TrustEntry:
        return self.entries.setdefault(f"{category}/{source}", TrustEntry(category, source))

    def should_ask(self, category: str, source: str, *, always_ask: Sequence[str] = ()) -> bool:
        if category in always_ask:
            return True
        entry = self.entry(category, source)
        return entry.streak < AUTO_THRESHOLD

    def record(self, kind: str, category: str, source: str, reason: str = "") -> TrustEntry:
        if kind not in ("adopted", "edited", "rejected"):
            raise ValueError(f"unknown outcome {kind!r}")
        entry = self.entry(category, source)
        setattr(entry, kind, getattr(entry, kind) + 1)
        if kind == "adopted":
            entry.streak += 1
            entry.last_reason = reason or entry.last_reason
        else:
            # the rollback half: one edit or rejection is enough to start asking again
            entry.streak = 0
            entry.last_reason = reason or f"{kind} by the user"
        self.save()
        return entry

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"version": 1, "auto_threshold": AUTO_THRESHOLD,
                              "entries": [dict(self.entries[k].__dict__)
                                          for k in sorted(self.entries)]},
                             ensure_ascii=False, sort_keys=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self.path)

    def as_rows(self) -> list[dict]:
        return [self.entries[k].__dict__ for k in sorted(self.entries)]


def record_rejection(plan: "Plan", *, source: str = "l1", reason: str = "",
                     ledger: Optional["TrustLedger"] = None) -> None:
    """驳回一笔计划：那一类内容立刻退回「先问」，且这条记录可追溯是谁、哪一类、为什么。

    裁定 156／198：门槛会移动，但**被驳回一次就要回头**——所以驳回不是「丢掉这张纸」，
    是给信任账记一笔 ``rejected``。``TrustLedger.should_ask`` 读的就是这条记录：
    该类下一次不必等到 streak 归零就已经在问。
    """
    cats = sorted(set(plan.categories.values())) or ["other"]
    book = ledger if ledger is not None else TrustLedger()
    for cat in cats:
        book.record("rejected", cat, source, reason or "用户驳回")


def record_rejection(plan: "Plan", *, source: str = "l1", reason: str = "",
                     ledger: Optional["TrustLedger"] = None) -> None:
    """驳回一笔计划：那一类内容立刻退回「先问」，且这条记录可追溯是谁、哪一类、为什么。

    裁定 156／198：门槛会移动，但**被驳回一次就要回头**——所以驳回不是「丢掉这张纸」，
    是给信任账记一笔 ``rejected``。``TrustLedger.should_ask`` 读的就是这条记录：
    该类下一次不必等到 streak 归零就已经在问。
    """
    cats = sorted(set(plan.categories.values())) or ["other"]
    book = ledger if ledger is not None else TrustLedger()
    for cat in cats:
        book.record("rejected", cat, source, reason or "用户驳回")


def record_rejection(plan: "Plan", *, source: str = "l1", reason: str = "",
                     ledger: Optional["TrustLedger"] = None) -> None:
    """驳回一笔计划：那一类内容立刻退回「先问」，且这条记录可追溯是谁、哪一类、为什么。

    裁定 156／198：门槛会移动，但**被驳回一次就要回头**——所以驳回不是「丢掉这张纸」，
    是给信任账记一笔 ``rejected``。``TrustLedger.should_ask`` 读的就是这条记录：
    该类下一次不必等到 streak 归零就已经在问。
    """
    cats = sorted(set(plan.categories.values())) or ["other"]
    book = ledger if ledger is not None else TrustLedger()
    for cat in cats:
        book.record("rejected", cat, source, reason or "用户驳回")


def default_ledger_path() -> Path:
    home = os.environ.get("PLOBI_HOME", "").strip()
    base = Path(home) if home else Path.home() / ".plobi"
    return base / "l1-trust.json"
