"""WP-MEMORY-LAYERING — the L1 gate: plan + sha256 + a decision bound to an object.

Six cases, each one a rule that must not be re-decided by whoever implements the
next layer on top:

1. an approval is bound to the *object* it was granted against — a renamed or
   re-pointed ``MIND_ROOT`` must not accept it (the replay half of §12.6's rule 2);
2. if a human edited the file meanwhile, the plan is stale and refuses instead of
   silently winning over the user;
3. an expired plan is void, never applied late;
4. the operations that can't be undone (deleting, rewriting the user's own record)
   are classified as never-automatic regardless of the trust level;
5. the ramp is arithmetic over recorded outcomes and it goes **both** ways: enough
   adoptions stop the asking, one rejection restarts it with a reason, and the
   state survives a reload;
6. building a plan touches neither repo (the zero-pollution self-proof).

Everything runs against throwaway git repos in the tempdir — the real brain is
never the fixture.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from plobi.mind import paths as mind_paths
from plobi.mind import proposal
from plobi.mind.proposal import (
    ABSENT,
    Change,
    TrustLedger,
    apply_plan,
    approve,
    build_plan,
    category_of,
    dirty_repos,
    is_expired,
    never_auto,
    root_identity,
)


def _repo(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    root.mkdir(parents=True)
    run = lambda *a: subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True,
                                    check=True)  # noqa: E731
    run("init", "-q", "-b", "main")
    (root / "AGENTS.md").write_text("# brain\n", encoding="utf-8")
    run("add", "-A")
    run("-c", "user.email=t@invalid", "-c", "user.name=t", "commit", "-q", "-m", "seed")
    return root


@pytest.fixture()
def roots(tmp_path):
    mind = _repo(tmp_path, "mind")
    vault = _repo(tmp_path, "vault")
    (vault / "notes").mkdir()
    return mind_paths.Roots(mind=mind, vault=vault)


class _FakeWriter:
    """Stands in for MindWriter so the test proves the gate, not the disk layer."""

    def __init__(self):
        self.calls = []

    def write_one(self, relative_path, content, *, mode="overwrite"):
        self.calls.append(relative_path)
        return type("R", (), {"ok": True, "detail": ""})()


def test_approval_is_bound_to_the_root_it_was_granted_against(roots):
    plan = build_plan([Change("Vault/notes/rust.md", "所有权那一套")], roots=roots)
    writer = _FakeWriter()

    assert apply_plan(plan, approved=approve(plan), roots=roots, writer=writer).ok
    assert writer.calls == ["Vault/notes/rust.md"]

    # same plan, same digest, different object: the brain moved somewhere else
    moved = _repo(roots.mind.parent, "elsewhere-mind")
    moved_roots = mind_paths.Roots(mind=moved, vault=roots.vault)
    report = apply_plan(plan, approved=approve(plan), roots=moved_roots, writer=writer)
    assert not report.ok
    assert report.denied and "root-identity-changed" in report.denied[0].reason
    assert writer.calls == ["Vault/notes/rust.md"], "被拒之后一字节都不该再写"


def test_a_human_edit_makes_the_plan_stale_instead_of_beaten(roots):
    target = roots.vault / "notes" / "mech.md"
    plan = build_plan([Change("Vault/notes/mech.md", "牛顿第二定律")], roots=roots)
    assert plan.old_shas["Vault/notes/mech.md"] == ABSENT

    target.write_text("我自己先写了一版\n", encoding="utf-8")
    report = apply_plan(plan, approved=approve(plan), roots=roots, writer=_FakeWriter())
    assert report.denied and "content-changed-by-a-human" in report.denied[0].reason


def test_expired_plan_is_void_not_late(roots):
    plan = build_plan([Change("Vault/notes/ttl.md", "x")], roots=roots)
    frozen = plan.digest
    assert is_expired(plan, now=plan.created_at + 10) is False
    assert is_expired(plan, now=plan.created_at + proposal.PLAN_TTL_SECONDS + 10) is True
    report = apply_plan(plan, approved=frozen, roots=roots, writer=_FakeWriter(),
                        now=plan.created_at + proposal.PLAN_TTL_SECONDS + 10)
    assert report.denied and "plan-expired" in report.denied[0].reason


def test_the_unrecoverable_kinds_are_flagged_never_auto():
    assert never_auto("Vault/diary/2026-10-15.md", "overwrite") == "rewrites-the-user's-own-record"
    assert never_auto("Vault/meta/profile/profile.md", "overwrite") == "rewrites-the-persona"
    assert never_auto("Vault/notes/new.md", "create") == ""
    assert never_auto("Vault/notes/x.md", "squash") == "unknown-mode"
    # classification is derived from the path, never from what the model believes
    assert category_of("Vault/projects/SRTP/plan.md") == "project-docs"
    assert category_of("Loom/raw/chat-logs/digested/x.md") == "digests"


def test_the_ramp_moves_both_ways_and_survives_a_reload(tmp_path):
    ledger = TrustLedger(tmp_path / "l1-trust.json")
    assert ledger.should_ask("notes", "desktop") is True          # nothing earned yet

    for _ in range(proposal.AUTO_THRESHOLD):
        ledger.record("adopted", "notes", "desktop")
    assert ledger.should_ask("notes", "desktop") is False         # earned it

    ledger.record("rejected", "notes", "desktop", reason="课名写错了")
    assert ledger.should_ask("notes", "desktop") is True          # one strike, back to asking
    assert ledger.entry("notes", "desktop").last_reason == "课名写错了"

    # diary never rides the ramp at all
    assert ledger.should_ask("diary", "agenda-sync",
                             always_ask=("diary", "persona")) is True

    reloaded = TrustLedger(tmp_path / "l1-trust.json")
    assert reloaded.should_ask("notes", "desktop") is True
    assert reloaded.entry("notes", "desktop").rejected == 1


def test_building_a_plan_touches_neither_repo(roots):
    before = dirty_repos(roots)
    build_plan([Change("Vault/notes/ghost.md", "只读不写")], roots=roots)
    build_plan([Change("Loom/wiki/concepts/ghost.md", "也只读")], roots=roots)
    assert dirty_repos(roots) == before == [], "dry-run 留下脏工作树就是污染"


def test_the_persona_pointer_resolves_into_the_human_surface(roots, tmp_path, monkeypatch):
    """§10.1 第 6 项：画像那条死路径要长在判据里，不能只靠人记得。

    The failure this pins was real: the brain's own AGENTS.md pointed at a persona
    file that never existed, so an agent following the docs read nothing and there
    was no signal telling it so. Present must load, absent must say so — and both
    answers have to come from the human-facing root, not a path inside the brain.
    """
    from plobi.mind import reader as mind_reader

    persona = mind_paths.real_target(mind_reader.PERSONA_RELATIVE, roots)
    assert persona == roots.vault / "meta" / "profile" / "profile.md"

    monkeypatch.setenv("VAULT_ROOT", str(roots.vault))
    reader = mind_reader.MindReader(roots.mind)

    persona.parent.mkdir(parents=True, exist_ok=True)
    persona.write_text("# 用户画像\n\n- 姓名：小龟\n", encoding="utf-8")
    assert "小龟" in reader.persona()

    persona.unlink()
    assert reader.persona() == "", "缺失时静默返回空，就是当初写出第二份画像的原因"
