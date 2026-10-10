"""Regression tests for the _find_all_skills discovery cache (#58985 salvage).

Covers the cache-signature fix layered on the cherry-picked contributor
commit: the original keyed the cache on the max mtime of only the TOP-LEVEL
scan dirs, so adding/removing a skill inside a category subdir (which bumps
the category dir's mtime, not the root's) served a stale list indefinitely.
The signature now covers roots + immediate children (mirroring
plobi_cli/profiles.py::_count_skills) plus the disabled-set, plus (mtime_ns,
size) of the SKILL.md files the previous scan read — which is what catches an
in-place edit. The TTL is only the backstop for the edit no fingerprint can
see (same size AND same mtime_ns).

Measured on this Windows box (WP-SKILLS-INVALIDATION-WIN), and why each shape
below is written the way it is:
  - a directory re-stamps itself a few milliseconds after it is created, so a
    signature taken right after a scan can differ from the next one with
    nobody having changed anything (12 of 40 runs, then 17 of 40) — that is
    what made the old "stale within TTL" assertion flip, not a real race;
  - back-to-back writes into one directory can report an *identical* mtime
    (6 writes -> 4 distinct values), so directory mtimes alone miss changes;
  - os.utime with float seconds lands off the value it was handed (135 of 200
    requests, +/-100-300 ns; float64 quantum at this epoch = 238 ns), while
    ns= round-trips exactly (0 of 200) — so pinning and comparing here is done
    in integer nanoseconds.
"""

import os
import time

import pytest

import tools.skills_tool as st


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch, tmp_path):
    """Isolate every test: clear the module cache and point the scan at
    an empty external-dirs list + a tmp skills root."""
    st._SKILLS_CACHE.clear()
    monkeypatch.setattr(st, "_skills_dir", lambda: tmp_path / "skills")
    monkeypatch.setattr(
        "agent.skill_utils.get_external_skills_dirs", lambda: []
    )
    monkeypatch.setattr(st, "_get_disabled_skill_names", lambda: set())
    yield
    st._SKILLS_CACHE.clear()


def _write_skill(root, category, name, description="a skill"):
    d = root / "skills" / category / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n",
        encoding="utf-8",
    )
    return d


def test_cache_hit_serves_copies_not_cache_objects(tmp_path):
    """Callers mutate the returned dicts (web_server annotates
    s['enabled']/s['usage']) — the cache must hand out per-call copies."""
    _write_skill(tmp_path, "cat-a", "skill-one")
    first = st._find_all_skills()
    assert [s["name"] for s in first] == ["skill-one"]

    # Mutate what the first caller got; the next (cached) call must be clean.
    first[0]["enabled"] = False
    first.append({"name": "junk"})

    second = st._find_all_skills()
    assert [s["name"] for s in second] == ["skill-one"]
    assert "enabled" not in second[0], "cache poisoned by caller mutation"
    assert second is not first


def test_nested_category_skill_add_invalidates(tmp_path):
    """THE bug in the original PR: a new skill inside an existing category
    bumps the category dir's mtime only — the root-mtime key missed it."""
    _write_skill(tmp_path, "cat-a", "skill-one")
    first = st._find_all_skills()
    assert [s["name"] for s in first] == ["skill-one"]

    # Freeze the ROOT dir's mtime so only the category-child signature moves
    # (guards against filesystems bumping the parent too). ns= is load-bearing:
    # os.utime with float seconds lands ~100ns off the value it was handed on
    # NTFS (measured here: asked ...258800, got ...258700) and that drift is
    # itself a signature change, which would make this pass for the wrong reason.
    root = tmp_path / "skills"
    cat = root / "cat-a"
    root_stat = root.stat()
    _write_skill(tmp_path, "cat-a", "skill-two")
    import os
    os.utime(root, ns=(root_stat.st_atime_ns, root_stat.st_mtime_ns))

    # The category's own bump is not something this machine guarantees to
    # *record*: two writes issued into the same directory back-to-back stat back
    # with an identical mtime here (a 50ms gap is what makes it move). The claim
    # under test is that a change visible only on an immediate child
    # invalidates, so drive that signal instead of betting on filesystem timing.
    cat_stat = cat.stat()
    os.utime(cat, ns=(cat_stat.st_atime_ns, cat_stat.st_mtime_ns + 1_000_000_000))

    names = sorted(s["name"] for s in st._find_all_skills())
    assert names == ["skill-one", "skill-two"], (
        "category-nested skill add must invalidate the cache"
    )


def test_disabled_set_change_invalidates(tmp_path, monkeypatch):
    """Disabling a skill is a config change with NO filesystem mtime bump —
    it must still invalidate."""
    _write_skill(tmp_path, "cat-a", "skill-one")
    _write_skill(tmp_path, "cat-a", "skill-two")
    names = sorted(s["name"] for s in st._find_all_skills())
    assert names == ["skill-one", "skill-two"]

    monkeypatch.setattr(st, "_get_disabled_skill_names", lambda: {"skill-two"})
    names = sorted(s["name"] for s in st._find_all_skills())
    assert names == ["skill-one"], "disabled-set change must invalidate the cache"


def _pin(paths_and_stats):
    """Put every captured directory back on its exact nanosecond, so the only
    thing that can move the signature is the change under test."""
    for p, s in paths_and_stats.items():
        os.utime(p, ns=(s.st_atime_ns, s.st_mtime_ns))


def test_in_place_edit_of_skill_md_invalidates_on_the_next_call(tmp_path):
    """WP-SKILLS-INVALIDATION-WIN — the user-visible defect, and the reason this
    knife exists. An in-place edit bumps that file's mtime and no directory at
    all, while the signature looked only at directories, so this box served the
    old description until the 30s TTL expired (measured: signature byte-identical
    while the file went 70 -> 75 bytes; second call returned 'old description').

    Must be caught on the very next call, with the TTL nowhere near expiring.
    """
    skill_dir = _write_skill(tmp_path, "cat-a", "skill-one", "old description")
    skill_md = skill_dir / "SKILL.md"
    root = tmp_path / "skills"

    # Warm the cache, then call twice more: a freshly created directory re-stamps
    # itself a few milliseconds later, so this settles that noise before the edit
    # and leaves the edit as the only possible signature change.
    st._find_all_skills()
    st._find_all_skills()
    assert st._find_all_skills()[0]["description"] == "old description"

    dirs = {p: p.stat() for p in (root, root / "cat-a", skill_dir)}
    skill_md.write_text(
        "---\nname: skill-one\ndescription: much longer new description\n---\n# skill-one\n",
        encoding="utf-8",
    )
    _pin(dirs)

    assert st._SKILLS_CACHE_TTL_SECONDS == 30.0  # the entry is ~0s old: a hit is stale
    assert st._find_all_skills()[0]["description"] == "much longer new description"


def test_the_file_fingerprint_is_what_catches_the_edit(tmp_path, monkeypatch):
    """The negative case for the test above, so it cannot pass by accident.

    Same edit with the file fingerprint stubbed out of the signature and the TTL
    raised to an hour: the stale description comes back. If this test ever goes
    red, something *other* than the fingerprint now catches in-place edits — name
    it in the commit rather than deleting the assertion.
    """
    skill_dir = _write_skill(tmp_path, "cat-a", "skill-one", "old description")
    skill_md = skill_dir / "SKILL.md"
    root = tmp_path / "skills"
    monkeypatch.setattr(st, "_SKILLS_CACHE_TTL_SECONDS", 3600.0)
    monkeypatch.setattr(st, "_skill_file_fingerprints", lambda paths: ())

    st._find_all_skills()
    st._find_all_skills()
    dirs = {p: p.stat() for p in (root, root / "cat-a", skill_dir)}
    skill_md.write_text(
        "---\nname: skill-one\ndescription: much longer new description\n---\n# skill-one\n",
        encoding="utf-8",
    )
    _pin(dirs)

    assert st._find_all_skills()[0]["description"] == "old description"


def test_signature_separates_two_edits_one_float_quantum_apart(tmp_path):
    """The signature compares integer nanoseconds, and that is load-bearing.

    float seconds cannot express every 100ns tick at this epoch (measured here:
    ulp = 238 ns; os.utime(float) landed off the requested value on 135 of 200
    requests, while ns= was exact on 200 of 200). Two genuinely different stamps
    therefore share one float, and a float signature reads "nothing changed" on
    a real edit. The colliding pair is *measured* off ``stat().st_mtime`` rather
    than computed from the nanoseconds, because CPython's Windows conversion is
    not the same rounding path as ``st_mtime_ns / 1e9`` (that assumption made an
    earlier draft of this test flip).
    """
    skill_dir = _write_skill(tmp_path, "cat-a", "skill-one", "old description")
    skill_md = skill_dir / "SKILL.md"
    root = tmp_path / "skills"

    base_ns = skill_md.stat().st_mtime_ns
    collide = None
    for k in range(1, 12):
        low, high = base_ns + (k - 1) * 100, base_ns + k * 100
        os.utime(skill_md, ns=(low, low))
        at_low = skill_md.stat()
        os.utime(skill_md, ns=(high, high))
        at_high = skill_md.stat()
        if at_low.st_mtime == at_high.st_mtime and at_low.st_mtime_ns != at_high.st_mtime_ns:
            collide = (low, high)
            break
    if collide is None:  # pragma: no cover - proves the premise, not the code
        pytest.fail("no two adjacent 100ns stamps share a float near this clock")

    low, high = collide
    os.utime(skill_md, ns=(low, low))
    sig_before = st._skills_scan_signature([root], set(), [skill_md])
    float_before = skill_md.stat().st_mtime

    os.utime(skill_md, ns=(high, high))
    after = skill_md.stat()

    assert after.st_mtime_ns == high, "ns= round-trips exactly on this box"
    assert after.st_mtime == float_before, "…while float seconds cannot tell them apart"
    assert st._skills_scan_signature([root], set(), [skill_md]) != sig_before, (
        "a sub-quantum mtime change must still move the signature"
    )


def test_ttl_is_only_the_backstop_for_an_unfingerprintable_edit(tmp_path, monkeypatch):
    """The residual blind spot, written down instead of hidden: an edit that keeps
    the file's byte count AND its nanosecond mtime is invisible to every cheap
    signal, so the TTL is what bounds it. Do not raise the TTL to 'fix' anything —
    that only widens this window.
    """
    skill_dir = _write_skill(tmp_path, "cat-a", "skill-one", "old description")
    skill_md = skill_dir / "SKILL.md"
    root = tmp_path / "skills"

    st._find_all_skills()
    st._find_all_skills()

    everything = {p: p.stat() for p in (root, root / "cat-a", skill_dir, skill_md)}
    # 'new description' is byte-for-byte as long as 'old description' -> same size.
    skill_md.write_text(
        "---\nname: skill-one\ndescription: new description\n---\n# skill-one\n",
        encoding="utf-8",
    )
    _pin(everything)

    # Within TTL: stale, by design.
    assert st._find_all_skills()[0]["description"] == "old description"

    # Past TTL: fresh.
    monkeypatch.setattr(st, "_SKILLS_CACHE_TTL_SECONDS", 0.0)
    assert st._find_all_skills()[0]["description"] == "new description"


def test_disabled_and_full_views_cached_separately(tmp_path, monkeypatch):
    _write_skill(tmp_path, "cat-a", "skill-one")
    _write_skill(tmp_path, "cat-a", "skill-two")
    monkeypatch.setattr(st, "_get_disabled_skill_names", lambda: {"skill-two"})

    filtered = sorted(s["name"] for s in st._find_all_skills())
    everything = sorted(s["name"] for s in st._find_all_skills(skip_disabled=True))
    assert filtered == ["skill-one"]
    assert everything == ["skill-one", "skill-two"]
