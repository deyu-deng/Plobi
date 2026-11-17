#!/usr/bin/env python3
"""发版就绪闸：这一版能不能发出去，机械地核一遍。

为什么要有这个：发版这件事以前只有「说明」没有「闸」，出过三类漂：

① **版本号散在七处**，全靠手改。漂了没人知道——发出去的包装的是上一版、说明写的是这一版，
   这种事只有装到别人机器上才看得见。
② **两份说明**（中文正文 ＋ 英文附件）是分别写的。流水线在找不到正文时会**回退**到 GitHub
   自动生成的英文清单 ⇒ 直接发出一个「文案很差、没有双语」的发布页（2026-11-11 v0.3.1
   那次就是这么漂的）。措辞与双语的尺其实早就有（``scripts/release.py`` 的
   ``notes_register_problems`` ＋ ``tests/scripts/test_release_notes_guards.py``），
   但那些尺只扫**已经存在**的文件；「这一版的说明在不在、对不对得上」没人问。
③ **发布之后没人核资产**：三平台齐不齐、英文附件挂没挂、正文是不是中文那份，全靠人肉看。
   出过「只有 win 的包」。

这个脚本核的是**发版之前**那部分（① ② ＋ tag 的前置条件）：只读盘上与远端，不改任何东西；
有问题就非零退出。它跟 ``scripts/plobi/verify_release_assets.py``（发布之后那一半）是一对。

用法：
    python scripts/plobi/release_readiness.py                  # 核当前版本（仓内状态）
    python scripts/plobi/release_readiness.py --version 0.3.3  # 核指定的号
    python scripts/plobi/release_readiness.py --require-remote # 还要核远端没有这个 tag

退出码：
    0  就绪
    1  有问题（逐条打印）
    2  读不到本地仓（不是 git 仓，或缺文件到没法判断）
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[2]

DEFAULT_REMOTE = "https://github.com/deyu-deng/Plobi.git"


class Place(NamedTuple):
    """发布版本号的一处落点。

    每一处都用**精确正则**：读（就绪闸）与写（`bump_version.py`）走同一张表、同一套
    正则 ⇒ 不可能「读的是一处、改的是另一处」。正则要求恰好命中一次，形状变了当场报错，
    不猜。
    """

    label: str
    rel: str
    pattern: str


# 七处版本号。**这张表是唯一的真源**。
#
# 仓根 `package.json`（name `plobi-agent` / version `1.0.0`）**不在表里**：那是 npm
# workspace 容器自己的号，从来不跟着发布走；发布号住在 `apps/desktop` 那一格。
PLACES = (
    Place("plobi_cli/__init__.py 的 __version__", "plobi_cli/__init__.py", r'^__version__ = "([^"]+)"'),
    Place("pyproject.toml 的 version", "pyproject.toml", r'^version = "([^"]+)"'),
    Place("apps/desktop/package.json 的 version", "apps/desktop/package.json", r'^\s*"version": "([^"]+)"'),
    Place(
        "package-lock.json 里 apps/desktop 的 version",
        "package-lock.json",
        r'"apps/desktop": \{\s*\n\s*"name": "plobi",\s*\n\s*"version": "([^"]+)"',
    ),
    Place("acp_registry/agent.json 的 version", "acp_registry/agent.json", r'^\s*"version": "([^"]+)"'),
    Place(
        "acp_registry/agent.json 的 uvx 钉",
        "acp_registry/agent.json",
        r'"package": "plobi-agent\[acp\]==([^"]+)"',
    ),
    Place(
        "uv.lock 里 plobi-agent 的 version",
        "uv.lock",
        r'\[\[package\]\]\nname = "plobi-agent"\nversion = "([^"]+)"',
    ),
)

RELEASE_DATE_REL = "plobi_cli/__init__.py"
RELEASE_DATE_PATTERN = r'^__release_date__ = "([^"]+)"'

NOTES_REL = "docs/plobi/releases"


def read_place(root: Path, place: Place) -> str | None:
    """读一处版本号；读不到（文件缺／形状变了）返回 None——**不猜**。"""
    path = root / place.rel
    if not path.is_file():
        return None
    m = re.search(place.pattern, path.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else None


def current_version(root: Path = ROOT) -> str | None:
    """仓里现在的发布版本号（``__version__`` 是源，其余几处跟着它）。"""
    return read_place(root, PLACES[0])


def version_problems(root: Path = ROOT) -> list[str]:
    """七处版本号必须一致，且都得在。"""
    problems: list[str] = []
    values: dict[str, str | None] = {p.label: read_place(root, p) for p in PLACES}

    source = values[PLACES[0].label]
    if source is None:
        problems.append(f"读不到 {PLACES[0].label}（版本号的源）")
        return problems

    for place in PLACES:
        value = values[place.label]
        if value is None:
            problems.append(f"读不到 {place.label}——版本号散在七处，缺一处就说不清发出去的是哪一版")
        elif value != source:
            problems.append(f"{place.label} 是 {value}，与 {PLACES[0].label} 的 {source} 不一致")
    return problems


def release_date_problems(root: Path = ROOT) -> list[str]:
    """发布日必须是 `YYYY-MM-DD`（真发布那天，不是占位）。"""
    path = root / RELEASE_DATE_REL
    if not path.is_file():
        return [f"读不到 {RELEASE_DATE_REL}"]
    m = re.search(RELEASE_DATE_PATTERN, path.read_text(encoding="utf-8"), re.M)
    if not m:
        return [f"{RELEASE_DATE_REL} 里没有 __release_date__"]
    value = m.group(1)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return [f"__release_date__ 是 {value!r}，要 YYYY-MM-DD"]
    return []


def _release_module() -> object:
    """借 `scripts/release.py` 里那几把尺（措辞／双语／条目数）。

    **只借它的纯函数**：尺只有一份，闸里不许另长一套（两边各长一把尺，最后两边都
    漂）。`release.py` 的入口在 `__main__` 守卫下，import 不会跑它的 main。
    """
    path = ROOT / "scripts" / "release.py"
    spec = importlib.util.spec_from_file_location("_plobi_release_for_gates", path)
    if not spec or not spec.loader:
        raise ImportError(f"读不到 {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _copy_rules_module() -> object:
    """借 `release_copy_rules.py` 那把「用户面」尺（写什么、怎么框）。

    与措辞那把（`release.py`）是分工：措辞尺管「这句话像不像人话」，这把管「该不该给
    用户看、该不该拿上一版当句式模板」。
    """
    path = Path(__file__).resolve().parent / "release_copy_rules.py"
    spec = importlib.util.spec_from_file_location("_plobi_copy_rules_for_gates", path)
    if not spec or not spec.loader:
        raise ImportError(f"读不到 {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def notes_problems(root: Path, version: str) -> list[str]:
    """这一版的两份说明：都在、都非空、互相对得上、措辞过得了尺。"""
    problems: list[str] = []
    notes_dir = root / NOTES_REL
    zh_path = notes_dir / f"v{version}.md"
    en_path = notes_dir / f"Plobi-{version}-release-notes.en.md"

    if not zh_path.is_file():
        problems.append(f"缺中文正文 {NOTES_REL}/v{version}.md")
    if not en_path.is_file():
        problems.append(f"缺英文附件 {NOTES_REL}/Plobi-{version}-release-notes.en.md")
    if problems:
        return problems

    zh = zh_path.read_text(encoding="utf-8")
    en = en_path.read_text(encoding="utf-8")
    if not zh.strip():
        problems.append(f"{zh_path.name} 是空的")
    if not en.strip():
        problems.append(f"{en_path.name} 是空的")
    if problems:
        return problems

    try:
        release = _release_module()
    except Exception as exc:  # 尺读不到 ⇒ 判不了，不当作通过
        problems.append(f"尺读不到（scripts/release.py）：{exc}")
        return problems

    problems += link_problems(zh, version, notes_dir)

    zh_items = release._change_section_bullets(zh, release.REGISTER_SECTIONS_ZH)
    en_items = release._change_section_bullets(en, release.REGISTER_SECTIONS_EN)
    if not zh_items:
        problems.append(f"{zh_path.name} 一个条目都没有")
    elif len(zh_items) != len(en_items):
        problems.append(f"中英条目数对不上：中文 {len(zh_items)} 条、英文 {len(en_items)} 条")

    for problem in release.notes_register_problems(zh, language="zh"):
        problems.append(f"{zh_path.name} 的措辞过不了尺：{problem}")
    for problem in release.notes_register_problems(en, language="en"):
        problems.append(f"{en_path.name} 的措辞过不了尺：{problem}")

    # 用户面那把尺：写什么、怎么框（生成标记／摘要写成对比句／成对对比句式过量／
    # 内部实现词上屏）。措辞尺管「像不像人话」，这把管「该不该给用户看」。
    try:
        copy_rules = _copy_rules_module()
    except Exception as exc:
        problems.append(f"用户面那把尺读不到（release_copy_rules.py）：{exc}")
    else:
        problems += copy_rules.copy_problems(zh, zh_path.name)
        problems += copy_rules.copy_problems(en, en_path.name)
    return problems


def link_problems(body: str, version: str, notes_dir: Path) -> list[str]:
    """正文里的链接：互跳那一行必须指着这一版；其余版本链接必须真有那一版。

    为什么不用 `release.py` 的 ``body_reference_problems``：它把**任何**指向别的版本的
    链接都算成错，而每一版正文里都有一行「上一版：[v0.3.1]」——那是故意的、也是该有的
    （读者要能一键回上一版）。闸只核两件事：
    ① **互跳那一行**（``[中文] | [English]``）指着这一版的发布页与这一版的英文附件名；
    ② 正文里别的版本链接，仓里得有那一版的说明（否则那个链接点过去是 404）。
    """
    problems: list[str] = []
    en_asset = f"Plobi-{version}-release-notes.en.md"

    language_lines = [line for line in body.splitlines() if "[中文]" in line or "[English]" in line]
    if not language_lines:
        problems.append("正文里没有互跳那一行（[中文] | [English]）")
        return problems

    line = language_lines[0]
    if f"releases/tag/v{version}" not in line:
        problems.append(f"互跳那一行没指向本版发布页（releases/tag/v{version}）")
    if en_asset not in line:
        problems.append(f"互跳那一行没指向本版英文附件（{en_asset}）")

    for ref in sorted(set(re.findall(r"releases/tag/(v\d+\.\d+\.\d+)", body))):
        if ref == f"v{version}":
            continue
        if not (notes_dir / f"{ref}.md").is_file():
            problems.append(f"正文里那个 {ref} 的链接点过去是 404——仓里没有 {ref} 的说明")
    return problems


def remote_tag_problems(version: str, remote: str = DEFAULT_REMOTE) -> list[str]:
    """远端不能已经有这个号——同一个号发两次会覆盖上一版的 Release。"""
    try:
        proc = subprocess.run(
            ["git", "ls-remote", "--tags", remote, f"refs/tags/v{version}"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"查不到远端（{remote}）：{exc}"]

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        return [f"查不到远端（{remote}）：{detail[-1] if detail else 'git 报错了'}"]
    if proc.stdout.strip():
        return [f"远端已经有 v{version} 这个 tag——发过了就换号，别覆盖"]
    return []


def readiness_problems(
    root: Path = ROOT,
    version: str | None = None,
    require_remote: bool = False,
    remote: str = DEFAULT_REMOTE,
) -> list[str]:
    """发版就绪闸的全部检查。空清单＝就绪。"""
    version = version or current_version(root)
    if not version:
        return [f"读不到 {RELEASE_DATE_REL} 里的 __version__"]

    problems = version_problems(root)
    problems += release_date_problems(root)
    problems += notes_problems(root, version)
    if require_remote:
        problems += remote_tag_problems(version, remote)
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="发版就绪闸")
    parser.add_argument("--version", default="", help="核这个号（默认：仓里现在的号）")
    parser.add_argument("--require-remote", action="store_true", help="还要核远端没有这个 tag")
    parser.add_argument("--remote", default=DEFAULT_REMOTE, help="远端地址（默认 GitHub 上的本仓）")
    args = parser.parse_args(argv)

    version = args.version or current_version(ROOT)
    if not version:
        print("RED — 读不到版本号", file=sys.stderr)
        return 2

    print(f"发版就绪闸：版本 {version}")
    problems = readiness_problems(
        ROOT, version=version, require_remote=args.require_remote, remote=args.remote
    )
    if problems:
        print(f"RED — {len(problems)} 处问题：", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(f"GREEN — {version} 就绪：版本号七处一致、两份说明齐且对得上")
    return 0


if __name__ == "__main__":
    sys.exit(main())
