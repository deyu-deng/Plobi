"""Serialized, model-free writer for the Mind vault.

Why it exists: L2 agents read Mind directly (cheap) but must not write it
concurrently — parallel commits into one git repo trip Mind's verifier. Routing
writes through L1 would serialize them too, but at flagship-model token prices;
this daemon does the same job for the cost of a lock (ADR-0011).

Deliberately dumb: it takes text and a relative path. It never calls a model,
never invents content, and refuses paths outside the tolerated prefixes.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .lock import mind_write_lock
from .paths import (
    MindUnavailable,
    UnsafeMindPath,
    git_toplevel,
    real_target,
    require_root,
    resolve_root,
    resolve_roots,
    safe_target,
)

logger = logging.getLogger(__name__)

VERIFIER_RELATIVE = "Loom/scripts/verifier.py"


@dataclass
class WriteRequest:
    relative_path: str
    content: str
    # Append is the right default for logs/digests; overwrite for snapshots.
    mode: str = "overwrite"


@dataclass
class WriteResult:
    ok: bool
    written: list[str]
    skipped: list[str]
    detail: str = ""
    # 非空 = 这一批**没有落盘**：已打包成计划等用户点头（裁定 156／198）。
    staged: str = ""
    pending_id: str = ""


class MindWriter:
    def __init__(
        self,
        root: Path | str | None = None,
        *,
        commit: bool = True,
        run_verifier: bool = True,
        lock_timeout: float = 30.0,
        require_approval: bool | None = None,
        auto_approve: bool | None = None,
    ):
        self._explicit_root = root
        self.commit = commit
        self.run_verifier = run_verifier
        self.lock_timeout = lock_timeout
        # None = 按配置；True = 一定先问（测试与「人就是要看」用）；False = 一定不拦。
        self.require_approval = require_approval
        # None = 读 memory.mind_write_auto_approve（出厂**关**：一律先问）。
        self.auto_approve = auto_approve

    @property
    def available(self) -> bool:
        return resolve_root(self._explicit_root) is not None

    def _lock_path(self, root: Path) -> Path:
        return root / ".plobi-mind.lock"

    def _auto_allowed(self) -> bool:
        """自动档总开关（裁定 199）：没开就一律先点头，ramp 不参与判断。"""
        if self.auto_approve is not None:
            return bool(self.auto_approve)
        try:
            from plobi_cli.config import load_config

            mem = (load_config() or {}).get("memory", {}) or {}
            return bool(mem.get("mind_write_auto_approve", False))
        except Exception:  # noqa: BLE001 - 读不到配置就照「先问」办
            return False

    def _gate_refusals(self, batch: list[WriteRequest]) -> list[str]:
        """这批里哪些**不许自动落**（返回原因；空表 = 整批都可以自动写）。

        三条并起来问，缺一条就会出现「看起来自动了、其实没人批准过」：
        ① 路径本身是改写过去那一类（:func:`proposal.never_auto`）；
        ② 类别在永不自动那张表里（用户 10-23 拍：日记＋画像＋删除）；
        ③ 信任账没攒够（连续采纳 < AUTO_THRESHOLD，或被改／被驳回而归零）。
        外加总开关：自动档没开时，全部按「先问」办。
        """
        from plobi.mind import proposal as L1

        if self.require_approval is True:
            return ["显式要求先点头"]
        if self.require_approval is False or not batch:
            return []
        auto = self._auto_allowed()
        ledger = L1.TrustLedger()
        refusals: list[str] = []
        for request in batch:
            why = L1.never_auto(request.relative_path, request.mode)
            if why:
                refusals.append(f"{request.relative_path}: 永不自动（{why}）")
                continue
            category = L1.category_of(request.relative_path)
            if category in L1.NEVER_AUTO_CATEGORIES:
                refusals.append(f"{request.relative_path}: 类别 {category} 永不自动")
                continue
            if not auto:
                refusals.append(
                    f"{request.relative_path}: 自动档没开（memory.mind_write_auto_approve），先点头"
                )
                continue
            if ledger.should_ask(category, "l1", always_ask=L1.NEVER_AUTO_CATEGORIES):
                streak = ledger.entry(category, "l1").streak
                refusals.append(
                    f"{request.relative_path}: 类别 {category} 连续采纳 {streak}/{L1.AUTO_THRESHOLD}，还没攒够"
                )
        return refusals

    def _stage_for_approval(self, batch: list[WriteRequest]):
        """把这一批改成计划，放进**内核**的待确认层（``/memory pending`` 看得到）。

        计划里带每档新旧 sha256、绑两根目录对象身份的审批哈希与 TTL；点头时 ``apply_plan``
        会重新比这三样，所以同一份审批 replay 到人先改过的档、或改名后的根上都会被拒。
        """
        from plobi.mind import proposal as L1
        from plobi.mind.paths import resolve_roots
        from tools import write_approval as wa

        plan = L1.build_plan(
            [L1.Change(r.relative_path, r.content, r.mode) for r in batch],
            roots=resolve_roots(),
        )
        record = wa.stage_write(
            wa.MEMORY,
            {"kind": "mind_plan", "plan": plan.to_json()},
            summary=plan.human_summary().splitlines()[0],
            origin="foreground",
        )
        logger.info("mind: 未落盘，计划 %s 已进待确认 pending=%s", plan.digest[:12], record.get("id"))
        return plan, record

    def write(self, requests: Iterable[WriteRequest]) -> WriteResult:
        """Apply a batch under one lock. Unsafe paths are skipped, not fatal."""
        batch = list(requests)
        if not batch:
            return WriteResult(ok=True, written=[], skipped=[])

        try:
            root = require_root(self._explicit_root)
        except MindUnavailable as exc:
            return WriteResult(ok=False, written=[], skipped=[r.relative_path for r in batch], detail=str(exc))

        written: list[str] = []
        skipped: list[str] = []

        if batch and self._gate_refusals(batch):
            plan, record = self._stage_for_approval(batch)
            return WriteResult(
                ok=False,
                written=[],
                skipped=[r.relative_path for r in batch],
                staged=plan.digest,
                pending_id=str(record.get("id") or ""),
                detail=(
                    "这一批没有落盘：计划 %s（%d 项）已进待确认。"
                    " 看：/memory pending · 点头：/memory approve %s · 驳回：/memory reject %s"
                    % (plan.digest[:12], len(plan.changes), record.get("id"), record.get("id"))
                ),
            )

        with mind_write_lock(self._lock_path(root), timeout=self.lock_timeout):
            for request in batch:
                try:
                    target = safe_target(root, request.relative_path, creating=True)
                except UnsafeMindPath as exc:
                    logger.warning("mind: refused write to %s (%s)", request.relative_path, exc)
                    skipped.append(request.relative_path)
                    continue

                target.parent.mkdir(parents=True, exist_ok=True)
                if request.mode == "append" and target.exists():
                    with open(target, "a", encoding="utf-8") as handle:
                        handle.write(request.content)
                else:
                    target.write_text(request.content, encoding="utf-8")
                written.append(request.relative_path)

            if not written:
                return WriteResult(ok=False, written=[], skipped=skipped, detail="nothing written")

            if self.run_verifier:
                ok, detail = self._verify(root)
                if not ok:
                    # Files stay on disk (Mind is a working copy, not a
                    # transaction); we just refuse to commit a tree the
                    # verifier rejects and surface why.
                    return WriteResult(ok=False, written=written, skipped=skipped, detail=detail)

            if self.commit:
                ok, detail = self._commit(root, written)
                return WriteResult(ok=ok, written=written, skipped=skipped, detail=detail)

        return WriteResult(ok=True, written=written, skipped=skipped)

    def write_one(self, relative_path: str, content: str, *, mode: str = "overwrite") -> WriteResult:
        return self.write([WriteRequest(relative_path=relative_path, content=content, mode=mode)])

    # --- guards -------------------------------------------------------------

    def _verify(self, root: Path) -> tuple[bool, str]:
        script = real_target(VERIFIER_RELATIVE, root)
        if not script.is_file():
            return True, "verifier not present"

        import sys

        try:
            completed = subprocess.run(
                [sys.executable, str(script), "--strict"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            # A broken verifier must not block the secretary; log and continue.
            logger.warning("mind: verifier could not run: %s", exc)
            return True, f"verifier skipped: {exc}"

        if completed.returncode != 0:
            tail = (completed.stdout or completed.stderr or "").strip()[-400:]
            return False, f"verifier blocked the write: {tail}"
        return True, "verifier clean"

    def _git_toplevel(self, root: Path) -> Optional[str]:
        """Return the enclosing git top-level for ``root``, or ``None``.

        ``None`` means ``root`` is not inside any git repository (rev-parse
        failed or git is unavailable). This is used purely as a guard: we only
        ever commit when ``root`` *is* the repository top-level. Single
        implementation lives in :func:`plobi.mind.paths.git_toplevel` — the
        declaration sync path has to ask the identical question.
        """
        return git_toplevel(root)

    def _commit(self, root: Path, paths: list[str]) -> tuple[bool, str]:
        """Commit each written file into the repository that actually owns it.

        Since the two-root split a logical ``Vault/…`` file lives under ``VAULT_ROOT``
        — a different repository than the brain — so ``git add Vault/…`` run inside
        ``MIND_ROOT`` matched nothing and every AI write stayed untracked
        (``WP-MIND-LIVE``, found live on 2026-10-16). Group by the file's own
        top-level and hand git a path relative to *that* repo.
        """
        roots = resolve_roots(root)
        groups: dict[Path, list[str]] = {}
        orphans: list[str] = []
        for logical in paths:
            physical = real_target(logical, roots)
            repo = self._owned_repo(physical.parent, roots)
            if repo is None:
                orphans.append(logical)
                continue
            try:
                inner = physical.resolve().relative_to(repo.resolve())
            except OSError as exc:
                logger.warning("mind: cannot relativise %s: %s", physical, exc)
                orphans.append(logical)
                continue
            groups.setdefault(repo, []).append(inner.as_posix())

        details: list[str] = []
        ok = True
        for repo, inner_paths in groups.items():
            repo_ok, repo_detail = self._commit_into(repo, inner_paths)
            ok = ok and repo_ok
            details.append(f"{repo.name}: {repo_detail}")
        if orphans:
            details.append(
                "commit skipped: "
                + ", ".join(orphans)
                + " 不在脑自己的仓里（MIND_ROOT/VAULT_ROOT 之外）"
            )
        if len(details) == 1 and not orphans:
            # One repo owned the whole batch: say exactly what that repo said.
            return ok, details[0].split(": ", 1)[1]
        return ok, " · ".join(details) if details else "nothing to commit"

    def _owned_repo(self, directory: Path, roots) -> Optional[Path]:
        """The brain repository owning ``directory``, or ``None`` to refuse.

        Two refusals, both load-bearing: no repository at all, and a repository that
        merely *encloses* the brain (the shape that grew ``chore(plobi)`` commits into
        the product checkout — 裁定 72).
        """
        toplevel = self._git_toplevel(directory)
        if toplevel is None:
            logger.warning("mind: commit skipped: %s is not a git repository", directory)
            return None
        try:
            resolved_toplevel = Path(toplevel).resolve()
        except OSError as exc:
            logger.warning("mind: commit skipped: cannot resolve %s: %s", toplevel, exc)
            return None
        for candidate in (roots.mind, roots.vault):
            if candidate is None:
                continue
            try:
                same = resolved_toplevel == candidate.resolve()
            except OSError:
                same = False
            if same:
                return candidate
        logger.warning(
            "mind: commit skipped: %s is not a brain top-level (enclosing repo: %s)",
            directory,
            toplevel,
        )
        return None

    def _commit_into(self, root: Path, paths: list[str]) -> tuple[bool, str]:
        # Guard: only commit when ``root`` is itself the git top-level. If it is
        # merely nested inside an enclosing repo (e.g. a pytest basetemp under
        # the product checkout), committing would leak test output into that
        # repo's history — exactly the ``chore(plobi)`` noise we must never
        # produce. The write already landed on disk; we just skip the commit.
        toplevel = self._git_toplevel(root)
        if toplevel is None:
            logger.warning(
                "mind: commit skipped: %s is not a git top-level "
                "(not a git repository)",
                root,
            )
            return True, (
                f"commit skipped: {root} is not a git top-level "
                "(not a git repository)"
            )

        try:
            resolved_root = root.resolve()
            resolved_toplevel = Path(toplevel).resolve()
        except OSError as exc:
            logger.warning("mind: commit skipped: cannot resolve %s: %s", root, exc)
            return True, f"commit skipped: {root} could not be resolved ({exc})"

        if resolved_toplevel != resolved_root:
            logger.warning(
                "mind: commit skipped: %s is not a git top-level "
                "(enclosing repo: %s)",
                root,
                toplevel,
            )
            return True, (
                f"commit skipped: {root} is not a git top-level "
                f"(enclosing repo: {toplevel})"
            )

        try:
            add_completed = subprocess.run(
                ["git", "add", *paths],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning(
                "mind: commit skipped (git add raised): %s", exc
            )
            return True, f"commit skipped: git add raised: {exc}"

        if add_completed.returncode != 0:
            # git add 非零通常意味着仓库被锁 / 路径不在仓内——文件已经在盘上，
            # 不该因此让主回合去撞 Plobi memory 补课。
            output = (add_completed.stdout or "") + (add_completed.stderr or "")
            logger.warning(
                "mind: commit skipped (git add failed): %s",
                output.strip()[-200:],
            )
            return True, f"commit skipped: git add failed: {output.strip()[-200:]}"

        try:
            completed = subprocess.run(
                ["git", "commit", "-m", f"chore(plobi): update {len(paths)} file(s)"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning(
                "mind: commit skipped (git commit raised): %s", exc
            )
            return True, f"commit skipped: git commit raised: {exc}"

        if completed.returncode != 0:
            output = (completed.stdout or "") + (completed.stderr or "")
            if "nothing to commit" in output:
                return True, "nothing to commit"
            # WP-MIND-HOT（裁定 33.2）：脏工作区 / 锁 / untracked / 冲突是日常
            # 运行环境噪音，git 本身可用、只是 commit 这一笔不收——文件已经写
            # 盘，主回合不该因此再去撞 Plobi memory 补课。下面这些标记命中即
            # 视作环境层面的 skip 而非 git 不可用，``ok=True``、``detail`` 留
            # 现场供运维追查。
            skip_markers = (
                "another git process seems to be running",
                "index.lock",
                "Unable to create",
                "Your local changes",
                "your local changes",
                "your untracked files",
                "Untracked working tree files",
                "Please commit your changes or stash them",
                "Please move or remove them",
                "Permission denied",
                "could not lock",
                "already exists on disk",
            )
            if any(marker in output for marker in skip_markers):
                logger.warning(
                    "mind: commit skipped (dirty/lock/untracked): %s",
                    output.strip()[-200:],
                )
                return True, f"commit skipped: {output.strip()[-200:]}"
            # 真不可用 / 真错误仍回 ok=False，但绝不抛——主回合已由外层兜底。
            return False, output.strip()[-400:]
        return True, "committed"


_DEFAULT: Optional[MindWriter] = None
_DEFAULT_LOCK = threading.Lock()


def get_writer() -> MindWriter:
    global _DEFAULT
    if _DEFAULT is None:
        with _DEFAULT_LOCK:
            if _DEFAULT is None:
                _DEFAULT = MindWriter()
    return _DEFAULT


def set_writer(writer: Optional[MindWriter]) -> None:
    global _DEFAULT
    _DEFAULT = writer
