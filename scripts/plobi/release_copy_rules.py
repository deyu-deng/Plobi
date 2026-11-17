#!/usr/bin/env python3
"""发布页的「用户面」尺：这一版写给谁看、能不能上屏。

为什么要有这个：`scripts/release.py` 的 `notes_register_problems` 管的是**措辞**
（口语词、营销词、称呼读者、条目形状）。这一份管的是**另一件事**——**写什么**与
**怎么框**。两者曾经缺一，于是发布页长成了两种样子：

① **框架滥用**：整篇写成「上一版…这一版…」的对照句，每条都以「上一版」起头。
   2026-11-17 16:0x 用户原话：「我是叫你从与上一版的视角出发写新增功能。但是不要总是提
   『上一版』那两个字。」⇒ **比较是取内容的角度，不是句子的模板**。
   0.3.0 的水位：整篇「上一版／这一版」成对出现 **2 次**；0.3.1 是 **27 次**。
② **内部实现混进用户面**：仓库路径、代码文件后缀、内部模块名、构建期哈希、环境变量。
   用户看到的是「装完之后能干什么」，不是「我们内部怎么改的」。
③ **生成标记**：有的说明档开头带着 AIGC 生成水印（front matter）。它会跟着发布页上屏。

**这把尺与措辞那把是分工不是分叉**：措辞尺在 `release.py`（与产品文案同一把），这把在
本文件（写什么、怎么框）。两边各管一件事，都不许自己长出第二份。

用法：
    python scripts/plobi/release_copy_rules.py [--version 0.3.2]

退出码：
    0  这一版的用户面干净
    1  有问题（逐条打印）
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NOTES_REL = "docs/plobi/releases"

# 生成水印（AIGC 工具的 front matter）。它们会跟着发布页上屏。
GENERATION_MARKERS = (
    "AIGC:",
    "ContentProducer:",
    "ProduceID:",
    "ReservedCode1:",
    "ReservedCode2:",
    "ContentPropagator:",
    "PropagateID:",
)

# 摘要不许是「相对上一版…」的对比句——那是**写整篇**的框架，不该由摘要来承担。
COMPARISON_FRAMES = (
    "相对 v",
    "相比上一版",
    "与上一版相比",
    "上一版",
    "Compared with",
    "compared to the previous",
    "previous version",
)

# 「上一版…这一版…」这种成对句式，一篇里最多几次（0.3.0 的水位：2）。
MAX_PAIRED_COMPARISON = 2

# 内部实现词：仓库路径段、代码文件后缀、内部模块与构建期名词。
INTERNAL_PATTERNS = (
    r"(?:^|[\s`\"'(])plobi/",
    r"(?:^|[\s`\"'(])tools/",
    r"(?:^|[\s`\"'(])apps/",
    r"(?:^|[\s`\"'(])scripts/",
    r"(?:^|[\s`\"'(])tests/",
    r"\.(?:py|tsx?|mjs)\b",
    r"quota_hub",
    r"\bbezel\b",
    r"bootstrap",
    r"sha256",
    r"DO_NOT_TRACK",
    r"usemagpie\.ai",
)

_SECTION = re.compile(r"(?m)^#{2,3}\s")
_BULLET = re.compile(r"(?m)^- \*\*")
def _paired_comparison_lines(text: str) -> int:
    """「上一版…这一版…」这种成对句式，一条算一次。

    按**同一行**数，不按正则跨句匹配：条目是一行一句，两个词落在同一行里就是一次成对
    对照。0.3.0 全篇 2 次，0.3.1 那一版 24 次。
    """
    count = 0
    for line in text.splitlines():
        zh = "上一版" in line and "这一版" in line
        low = line.lower()
        en = "previous version" in low and ("this version" in low or "the new version" in low)
        if zh or en:
            count += 1
    return count


def _summary(text: str) -> str:
    """正文的第一段散文（跳过标题、互跳那一行、空行、小节标题与条目）。

    带 markdown 链接的行（「上一版：[v0.3.0](…)」）是**索引行**，不是摘要——摘要的毛病
    是「把整篇写成对比」而不是「多一行索引」。
    """
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("---"):
            continue
        if "](" in stripped:
            continue
        if stripped.startswith("-"):
            continue
        return stripped
    return ""


def _strip_code_fences(text: str) -> str:
    """去掉 ``` 围起来的代码块。

    代码块是**明写给开发者看**的东西（从源码跑起来的命令），它跟「给用户看的散文」不是
    一回事——0.1.0／0.2.0 的正文里有一段这样的代码块，那不是「内部实现混进用户面」。
    AIGC 水印那种 `---` front matter 不是代码块，不受这里影响。
    """
    return re.sub(r"(?ms)^```.*?^```\s*$", "", text)


def _internal_hits(text: str) -> list[str]:
    hits: list[str] = []
    for pattern in INTERNAL_PATTERNS:
        for m in re.finditer(pattern, text):
            hits.append(m.group(0).strip())
    return hits


def copy_problems(text: str, label: str) -> list[str]:
    """一份说明的用户面问题清单。空清单＝干净。"""
    problems: list[str] = []

    for marker in GENERATION_MARKERS:
        if marker in text:
            problems.append(f"{label}：带着生成标记（{marker.strip(':')}）——它会跟着发布页上屏")
            break

    summary = _summary(text)
    if summary:
        for frame in COMPARISON_FRAMES:
            if frame in summary:
                problems.append(
                    f"{label}：摘要写成了对比句（{frame}）——摘要说这一版给了什么，不说它跟谁比"
                )
                break

    paired = _paired_comparison_lines(text)
    if paired > MAX_PAIRED_COMPARISON:
        problems.append(
            f"{label}：「上一版…这一版…」这种成对句式 {paired} 次（上限 {MAX_PAIRED_COMPARISON}）"
            "——比较是取内容的角度，不是每条的开头"
        )

    hits = _internal_hits(_strip_code_fences(text))
    if hits:
        uniq = sorted(set(hits))
        problems.append(
            f"{label}：有内部实现词上屏（{'、'.join(uniq[:6])}）——用户看的是装完之后能干什么"
        )

    return problems


def notes_paths(version: str) -> tuple[Path, Path]:
    notes_dir = ROOT / NOTES_REL
    return (
        notes_dir / f"v{version}.md",
        notes_dir / f"Plobi-{version}-release-notes.en.md",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="发布页的用户面尺")
    parser.add_argument("--version", default="", help="核这一版（留空＝仓里当前那一版）")
    args = parser.parse_args(argv)

    version = args.version
    if not version:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from release_readiness import current_version

        version = current_version(ROOT) or ""
    if not version:
        print("RED — 读不到版本号", file=sys.stderr)
        return 2

    problems: list[str] = []
    for path in notes_paths(version):
        if not path.is_file():
            problems.append(f"缺 {path.name}")
            continue
        problems += copy_problems(path.read_text(encoding="utf-8"), path.name)

    if problems:
        print(f"RED — {version} 的用户面有 {len(problems)} 处问题：", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(f"GREEN — {version} 的用户面干净")
    return 0


if __name__ == "__main__":
    sys.exit(main())
