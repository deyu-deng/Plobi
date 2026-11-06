"""Tests for the release-notes guards in scripts/release.py.

A release page is a one-shot thing: once the release is out, a broken link in
the body looks fine and stays broken. The three guards below are what keep
that from happening, and each of them has bitten this repo at least once:

* the notes file has to be the notes of the version being released
  (`--bump minor` moves the version off the file name);
* the English attachment is published under one exact name, because the
  Chinese body links to it by name and `gh` takes the file name as the asset
  name;
* every link in the body has to resolve on the release page.

The last test pins the naming rule against the files in the repo: the English
body of a release must exist, under the asset name the Chinese body links to.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_release_module():
    spec = importlib.util.spec_from_file_location(
        "_release_under_test",
        REPO_ROOT / "scripts" / "release.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_notes_file_version_reads_the_version_off_the_name():
    module = _load_release_module()
    assert module.notes_file_version(Path("v0.2.0.md")) == "0.2.0"
    assert module.notes_file_version(Path("v0.2.0.en.md")) == "0.2.0"
    assert module.notes_file_version(Path("Plobi-0.2.0-release-notes.en.md")) == "0.2.0"
    # A name that carries no version gives nothing to check against.
    assert module.notes_file_version(Path("notes.md")) is None


def test_english_asset_name_is_the_name_the_body_links_to():
    module = _load_release_module()
    assert module.english_asset_name("0.2.2") == "Plobi-0.2.2-release-notes.en.md"


def test_body_reference_problems_accepts_a_body_that_resolves():
    module = _load_release_module()
    body = (
        "## Plobi v0.2.2\n\n"
        "[中文](https://github.com/deyu-deng/Plobi/releases/tag/v0.2.2) | "
        "[English](https://github.com/deyu-deng/Plobi/releases/download/"
        "v0.2.2/Plobi-0.2.2-release-notes.en.md)\n"
    )
    assert module.body_reference_problems(body, "0.2.2", "Plobi-0.2.2-release-notes.en.md") == []


def test_body_reference_problems_catches_a_link_to_another_version():
    module = _load_release_module()
    body = (
        "[中文](https://github.com/deyu-deng/Plobi/releases/tag/v0.2.1) | "
        "[English](https://github.com/deyu-deng/Plobi/releases/download/"
        "v0.2.1/Plobi-0.2.1-release-notes.en.md)\n"
    )
    problems = module.body_reference_problems(body, "0.2.2", "Plobi-0.2.2-release-notes.en.md")
    assert len(problems) == 2
    assert any("v0.2.1" in problem for problem in problems)


def test_body_reference_problems_catches_an_attachment_that_is_not_uploaded():
    module = _load_release_module()
    body = (
        "[English](https://github.com/deyu-deng/Plobi/releases/download/"
        "v0.2.2/Plobi-0.2.2-release-notes.en.md)\n"
    )
    # No English notes file given -> nothing will carry that name.
    problems = module.body_reference_problems(body, "0.2.2", None)
    assert len(problems) == 1
    assert "--notes-en-file" in problems[0]


def test_body_reference_problems_catches_an_asset_name_mismatch():
    module = _load_release_module()
    body = (
        "[English](https://github.com/deyu-deng/Plobi/releases/download/"
        "v0.2.2/Plobi-0.2.2-release-notes.en.md)\n"
    )
    problems = module.body_reference_problems(body, "0.2.2", "Plobi-0.2.2-release-notes.md")
    assert len(problems) == 1
    assert "Plobi-0.2.2-release-notes.md" in problems[0]


def test_body_reference_problems_catches_a_near_miss_asset_name():
    """A name that is merely close is just as broken as a wrong version.

    "v0.2.2.en.md" is what the repo's files used to be called; the body links
    to the asset name, so a near-miss name 404s exactly like a wrong one.
    """
    module = _load_release_module()
    body = (
        "[English](https://github.com/deyu-deng/Plobi/releases/download/"
        "v0.2.2/v0.2.2.en.md)\n"
    )
    problems = module.body_reference_problems(
        body, "0.2.2", "Plobi-0.2.2-release-notes.en.md"
    )
    assert len(problems) == 1
    assert "v0.2.2.en.md" in problems[0]


def test_body_reference_problems_ignores_ordinary_downloads():
    """An installer link is not the English attachment -- installers are
    attached by the workflow, not by this script."""
    module = _load_release_module()
    body = (
        "[Windows](https://github.com/deyu-deng/Plobi/releases/download/"
        "v0.2.2/Plobi-0.2.2-win-x64.exe)\n"
    )
    assert module.body_reference_problems(body, "0.2.2", None) == []


def test_every_release_in_the_repo_has_a_resolvable_english_attachment():
    """The repo's own notes must survive the rule they are checked by.

    For each Chinese body there is an English file whose name is exactly the
    asset the body links to -- otherwise publishing it produces a 404 that
    only a reader would notice.
    """
    releases_dir = REPO_ROOT / "docs" / "plobi" / "releases"
    chinese = sorted(releases_dir.glob("v*.md"))
    assert chinese, "no release notes found in docs/plobi/releases"

    for notes_path in chinese:
        body = notes_path.read_text(encoding="utf-8")
        version = notes_path.stem.lstrip("v")
        expected_asset = f"Plobi-{version}-release-notes.en.md"

        assert f"/releases/tag/v{version}" in body, (
            f"{notes_path.name} does not link to its own release page"
        )
        assert expected_asset in body, (
            f"{notes_path.name} does not link to {expected_asset}"
        )
        english_path = releases_dir / expected_asset
        assert english_path.is_file(), f"missing English notes: {expected_asset}"
        assert english_path.read_text(encoding="utf-8").strip(), (
            f"{expected_asset} is empty"
        )


def test_release_notes_carry_no_internal_wording():
    """Release pages are for readers, not for the repo's own bookkeeping."""
    releases_dir = REPO_ROOT / "docs" / "plobi" / "releases"
    forbidden = re.compile(
        r"裁定\s*\d+|WP-[A-Z0-9-]+|release\.py|pipeline|流水线",
        re.IGNORECASE,
    )
    for notes_path in sorted(releases_dir.glob("*.md")):
        if notes_path.name == "README.md":
            continue
        hits = forbidden.findall(notes_path.read_text(encoding="utf-8"))
        assert not hits, f"{notes_path.name} leaks internal wording: {hits}"
