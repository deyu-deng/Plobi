"""发版就绪闸与版本号真源的测试。

为什么这些测试值钱：发版这件事以前只有「说明」没有「闸」，而漂是**静默**的——
版本号少改一处、说明少一份、两种语言的条目数对不上，发出去之前没人看得出来，
装到别人机器上才看得见。所以这里钉住的是「闸真的会红」，不是「闸跑得过」。

这些用例全是**离线**的：造一棵临时的假仓，不碰真实仓、不联网。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts" / "plobi"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


readiness = _load("release_readiness")
bump = _load("bump_version")


VERSION = "9.1.2"
DATE = "2026-12-01"

ZH_NOTES = """## Plobi v{version}

[中文](https://github.com/deyu-deng/Plobi/releases/tag/v{version}) | [English](https://github.com/deyu-deng/Plobi/releases/download/v{version}/Plobi-{version}-release-notes.en.md)

上一版：[v9.0.0](https://github.com/deyu-deng/Plobi/releases/tag/v9.0.0)

### 新增

- **派活单** — 对话里说定一件事即成授权。
- **额度面板** — 每家的余量一眼可见。

### 修复

- **首启重装** — 探测超时此前被判定为环境故障。
"""

EN_NOTES = """# Plobi v{version}

[中文](https://github.com/deyu-deng/Plobi/releases/tag/v{version}) | [English](https://github.com/deyu-deng/Plobi/releases/download/v{version}/Plobi-{version}-release-notes.en.md)

Previous release: [v9.0.0](https://github.com/deyu-deng/Plobi/releases/tag/v9.0.0)

### New

- **Dispatch tickets** — A decision stated in conversation becomes an authorization.
- **Quota panel** — Each vendor's remaining allowance is visible at a glance.

### Fixed

- **First-launch reinstall** — A slow probe used to be read as an environment failure.
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_tree(root: Path, version: str = VERSION, date: str = DATE, *, notes: bool = True) -> Path:
    """一棵够闸用的假仓：七处版本号 ＋ 发布日 ＋ 两份说明。"""
    _write(root / "plobi_cli" / "__init__.py", f'__version__ = "{version}"\n__release_date__ = "{date}"\n')
    _write(root / "pyproject.toml", f'name = "plobi-agent"\nversion = "{version}"\n')
    _write(root / "apps" / "desktop" / "package.json", f'{{\n  "name": "plobi",\n  "version": "{version}"\n}}\n')
    _write(
        root / "package-lock.json",
        '{\n  "name": "plobi-agent",\n  "packages": {\n    "apps/desktop": {\n      "name": "plobi",\n'
        f'      "version": "{version}"\n    }}\n  }}\n}}\n',
    )
    _write(
        root / "acp_registry" / "agent.json",
        f'{{\n  "id": "plobi-agent",\n  "version": "{version}",\n'
        f'  "distribution": {{\n    "uvx": {{\n      "package": "plobi-agent[acp]=={version}"\n    }}\n  }}\n}}\n',
    )
    _write(
        root / "uv.lock",
        f'[[package]]\nname = "plobi-agent"\nversion = "{version}"\nsource = {{ editable = "." }}\n',
    )
    if notes:
        releases = root / "docs" / "plobi" / "releases"
        _write(releases / f"v{version}.md", ZH_NOTES.format(version=version))
        _write(releases / f"Plobi-{version}-release-notes.en.md", EN_NOTES.format(version=version))
        # 「上一版」那一行指着 9.0.0 ⇒ 仓里也得有那一版的说明，否则那个链接是 404。
        _write(releases / "v9.0.0.md", ZH_NOTES.format(version="9.0.0"))
    return root


# ── 版本号七处 ─────────────────────────────────────────────────────────────


def test_a_tree_whose_seven_places_agree_has_no_problem(tmp_path: Path):
    root = make_tree(tmp_path / "repo")
    assert readiness.version_problems(root) == []


def test_one_drifted_place_is_named(tmp_path: Path):
    """漂了要能说出是哪一处——「说不清是哪一版」正是过去出事的方式。"""
    root = make_tree(tmp_path / "repo")
    _write(root / "pyproject.toml", 'name = "plobi-agent"\nversion = "0.0.1"\n')
    problems = readiness.version_problems(root)
    assert len(problems) == 1
    assert "pyproject.toml" in problems[0]


def test_a_missing_place_is_a_problem_not_a_pass(tmp_path: Path):
    root = make_tree(tmp_path / "repo")
    (root / "uv.lock").unlink()
    problems = readiness.version_problems(root)
    assert len(problems) == 1
    assert "uv.lock" in problems[0]


def test_the_release_date_must_be_a_real_date_shape(tmp_path: Path):
    root = make_tree(tmp_path / "repo")
    _write(root / "plobi_cli" / "__init__.py", f'__version__ = "{VERSION}"\n__release_date__ = "2026.12.01"\n')
    assert readiness.release_date_problems(root)


# ── 两份说明 ───────────────────────────────────────────────────────────────


def test_both_notes_must_exist(tmp_path: Path):
    root = make_tree(tmp_path / "repo", notes=False)
    problems = readiness.notes_problems(root, VERSION)
    assert any("中文正文" in p for p in problems)
    assert any("英文附件" in p for p in problems)


def test_the_two_languages_must_have_the_same_number_of_items(tmp_path: Path):
    """中英是分别写的，不核就必然漂——漂了读者看到的是两个版本的故事。"""
    root = make_tree(tmp_path / "repo")
    en_path = root / "docs" / "plobi" / "releases" / f"Plobi-{VERSION}-release-notes.en.md"
    en_path.write_text(EN_NOTES.format(version=VERSION).replace("- **Quota panel** — Each vendor's remaining allowance is visible at a glance.\n", ""), encoding="utf-8")
    problems = readiness.notes_problems(root, VERSION)
    assert any("条目数对不上" in p for p in problems)


def test_a_previous_release_link_is_fine_when_that_version_exists(tmp_path: Path):
    """每一版正文都有「上一版」那行——它是给读者回上一版的，不是错。"""
    root = make_tree(tmp_path / "repo")
    assert readiness.notes_problems(root, VERSION) == []


def test_a_link_to_a_version_that_was_never_released_is_a_problem(tmp_path: Path):
    root = make_tree(tmp_path / "repo")
    zh_path = root / "docs" / "plobi" / "releases" / f"v{VERSION}.md"
    zh_path.write_text(
        ZH_NOTES.format(version=VERSION).replace(
            "上一版：[v9.0.0](https://github.com/deyu-deng/Plobi/releases/tag/v9.0.0)",
            "上一版：[v8.0.0](https://github.com/deyu-deng/Plobi/releases/tag/v8.0.0)",
        ),
        encoding="utf-8",
    )
    problems = readiness.notes_problems(root, VERSION)
    assert any("v8.0.0" in p for p in problems)


def test_the_language_line_must_point_at_this_version(tmp_path: Path):
    root = make_tree(tmp_path / "repo")
    zh_path = root / "docs" / "plobi" / "releases" / f"v{VERSION}.md"
    zh_path.write_text(
        ZH_NOTES.format(version=VERSION).replace("releases/tag/v9.1.2", "releases/tag/v9.1.1"),
        encoding="utf-8",
    )
    problems = readiness.notes_problems(root, VERSION)
    assert any("互跳那一行" in p for p in problems)


def test_colloquial_wording_is_rejected(tmp_path: Path):
    """措辞那把尺跟产品文案是同一把（release.py 的 notes_register_problems）。"""
    root = make_tree(tmp_path / "repo")
    zh_path = root / "docs" / "plobi" / "releases" / f"v{VERSION}.md"
    zh_path.write_text(
        ZH_NOTES.format(version=VERSION).replace("对话里说定一件事即成授权。", "你可以当场就弄好了。"),
        encoding="utf-8",
    )
    problems = readiness.notes_problems(root, VERSION)
    assert any("措辞过不了尺" in p for p in problems)


# ── 改号 ───────────────────────────────────────────────────────────────────


def test_the_bump_changes_every_place_and_the_date(tmp_path: Path):
    root = make_tree(tmp_path / "repo")
    touched = bump.bump(root, "9.2.0", "2026-12-24")
    assert readiness.version_problems(root) == []
    assert "plobi_cli/__init__.py" in touched
    assert "uv.lock" in touched
    assert 'version = "9.2.0"' in (root / "pyproject.toml").read_text(encoding="utf-8")
    assert '"package": "plobi-agent[acp]==9.2.0"' in (
        root / "acp_registry" / "agent.json"
    ).read_text(encoding="utf-8")
    assert '__release_date__ = "2026-12-24"' in (
        root / "plobi_cli" / "__init__.py"
    ).read_text(encoding="utf-8")


def test_a_place_whose_shape_changed_stops_the_bump(tmp_path: Path):
    """形状变了就停——脚本不该猜这一格该长什么样。"""
    root = make_tree(tmp_path / "repo")
    _write(root / "pyproject.toml", 'name = "plobi-agent"\nversion = 9  # 忘了引号\n')
    with pytest.raises(ValueError):
        bump.bump(root, "9.2.0", "2026-12-24")


def test_the_bump_refuses_a_version_that_is_not_semver(tmp_path: Path):
    root = make_tree(tmp_path / "repo")
    with pytest.raises(ValueError):
        bump.bump(root, "0.3", "2026-12-24")


def test_the_real_repo_is_ready_to_release(tmp_path: Path):
    """真仓自己过一遍闸：七处一致 ＋ 这一版的两份说明齐且对得上。

    这条在 CI 上就是那道硬闸——**版本号改了但说明没跟上**，或者反过来，都不许合进 main。
    """
    problems = readiness.readiness_problems(REPO_ROOT)
    assert problems == [], problems
