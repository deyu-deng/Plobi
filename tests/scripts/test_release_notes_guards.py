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


def _house_body() -> str:
    return (
        "## Plobi v0.3.0\n\n"
        "[中文](https://github.com/deyu-deng/Plobi/releases/tag/v0.3.0) | "
        "[English](https://github.com/deyu-deng/Plobi/releases/download/"
        "v0.3.0/Plobi-0.3.0-release-notes.en.md)\n\n"
        "### 新增\n\n"
        "- **知识库** — 左栏新增知识库入口。笔记支持检索与就地编辑。\n"
        "- **日记** — 左栏新增日记入口，按日撰写与翻阅。\n\n"
        "### 修复\n\n"
        "- **首启重装循环** — 探测超时此前被判定为环境故障，现按响应缓慢处理。\n"
    )


def test_register_guard_accepts_the_house_shape():
    module = _load_release_module()
    assert module.notes_register_problems(_house_body(), language="zh") == []


def test_register_guard_catches_colloquial_wording():
    """The wording that made earlier notes read like a chat message."""
    module = _load_release_module()
    body = _house_body().replace("现按响应缓慢处理", "当场就装好了")
    problems = module.notes_register_problems(body, language="zh")
    assert any("当场" in problem for problem in problems)


def test_register_guard_catches_marketing_adjectives():
    module = _load_release_module()
    body = _house_body().replace("笔记支持检索与就地编辑", "带来强大的检索体验")
    problems = module.notes_register_problems(body, language="zh")
    assert any("强大" in problem for problem in problems)


def test_register_guard_catches_the_reader_being_addressed():
    module = _load_release_module()
    body = _house_body().replace("按日撰写与翻阅", "你可以按日撰写与翻阅")
    problems = module.notes_register_problems(body, language="zh")
    assert any("你可以" in problem for problem in problems)


def test_register_guard_catches_an_item_that_is_not_in_the_house_shape():
    """An item without a noun phrase in bold is a sentence, not a release item."""
    module = _load_release_module()
    body = _house_body().replace(
        "- **日记** — 左栏新增日记入口，按日撰写与翻阅。",
        "- 左栏新增日记入口，按日撰写与翻阅。",
    )
    problems = module.notes_register_problems(body, language="zh")
    assert any("house shape" in problem for problem in problems)


def test_register_guard_catches_internal_wording():
    module = _load_release_module()
    body = _house_body() + "\n按裁定 209 的口径改的。\n"
    problems = module.notes_register_problems(body, language="zh")
    assert any("裁定" in problem for problem in problems)


def test_register_guard_catches_the_same_things_in_english():
    module = _load_release_module()
    body = (
        "### New\n\n"
        "- **Knowledge** — The left rail gains a Knowledge entry.\n\n"
        "You can now search every note.\n"
    )
    problems = module.notes_register_problems(body, language="en")
    assert any("you can now" in problem for problem in problems)


def test_every_release_note_in_the_repo_survives_the_register_guard():
    """The repo's own notes have to pass the rule they are checked by.

    A release page is written once and kept. If the register drifts, it drifts
    in the notes that are already published, and nothing catches it there.
    """
    module = _load_release_module()
    releases_dir = REPO_ROOT / "docs" / "plobi" / "releases"
    notes = [p for p in sorted(releases_dir.glob("*.md")) if p.name != "README.md"]
    assert notes, "no release notes found in docs/plobi/releases"

    for notes_path in notes:
        language = "en" if ".en." in notes_path.name else "zh"
        problems = module.notes_register_problems(
            notes_path.read_text(encoding="utf-8"), language=language
        )
        assert not problems, f"{notes_path.name} fails the register guard: {problems}"


def test_the_two_languages_of_a_release_cover_the_same_items():
    """One Chinese body, one English attachment -- the same facts twice.

    The Chinese body and the English attachment are written separately, so
    they drift unless something checks that they still say the same number of
    things in the same order.
    """
    module = _load_release_module()
    releases_dir = REPO_ROOT / "docs" / "plobi" / "releases"

    for chinese_path in sorted(releases_dir.glob("v*.md")):
        version = chinese_path.stem.lstrip("v")
        english_path = releases_dir / f"Plobi-{version}-release-notes.en.md"
        assert english_path.is_file(), f"missing English notes for v{version}"

        chinese_items = module._change_section_bullets(
            chinese_path.read_text(encoding="utf-8"), module.REGISTER_SECTIONS_ZH
        )
        english_items = module._change_section_bullets(
            english_path.read_text(encoding="utf-8"), module.REGISTER_SECTIONS_EN
        )
        assert chinese_items, f"{chinese_path.name} lists no items"
        assert len(chinese_items) == len(english_items), (
            f"v{version}: {len(chinese_items)} items in the Chinese body, "
            f"{len(english_items)} in the English attachment"
        )
