"""发布页「用户面」那把尺的测试。

为什么这些用例值钱：0.3.1 那一版**发布页上有 24 条**顶着「上一版…这一版…」，还有一段
AIGC 生成水印跟着上屏——**发布出去就撤不回来了**，而这在发版前完全没人拦。这把尺就是
那个拦：生成标记、摘要写成对比句、成对对比句式过量、内部实现词上屏，四样都判红。

用例全是离线的：造临时的假正文，不碰真实仓。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts" / "plobi"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rules = _load("release_copy_rules")

CLEAN = """## Plobi v9.9.9

[中文](https://github.com/deyu-deng/Plobi/releases/tag/v9.9.9) | [English](https://github.com/deyu-deng/Plobi/releases/download/v9.9.9/Plobi-9.9.9-release-notes.en.md)

v9.9.9 为桌面端加入派活单据与授权撤回。

安装包见本页面下方资产。

### 新增

- **任务单据** — 派出去的工作有单据可查：当前状态、派给了谁。

### 改进

- **加载态** — 统一成品牌小乌龟沿进度条行走。

### 修复

- **首启重装循环** — 探测超时此前被判定为环境故障，现按响应缓慢处理。

### 安装

下载下方的安装包直接运行——Windows 用 `.exe`（可选择数据目录）或 `.msi`（使用默认位置），macOS 用 `.dmg`，Linux 用 `.deb` / `.rpm` / `.AppImage`。

### 文档

- [README](https://github.com/deyu-deng/Plobi#readme)
- [完整变更列表](https://github.com/deyu-deng/Plobi/compare/v9.9.8...v9.9.9)
"""


def test_a_clean_body_has_no_problem():
    assert rules.copy_problems(CLEAN, "v9.9.9.md") == []


def test_the_generation_watermark_is_caught():
    body = "---\nAIGC:\n    Label: \"1\"\n    ContentProducer: 001191440300708461136T1XGW3\n---\n\n" + CLEAN
    problems = rules.copy_problems(body, "v9.9.9.md")
    assert any("生成标记" in p for p in problems)


def test_a_summary_written_as_a_comparison_is_caught():
    body = CLEAN.replace(
        "v9.9.9 为桌面端加入派活单据与授权撤回。",
        "相对 v9.9.8，这一版把派活与授权做成直接可用的能力。",
    )
    problems = rules.copy_problems(body, "v9.9.9.md")
    assert any("摘要写成了对比句" in p for p in problems)


def test_the_paired_comparison_frame_has_a_ceiling():
    """0.3.0 全篇只用了两次；0.3.1 用了 27 次。"""
    two = CLEAN + "\n上一版每家服务各有一条自己写的接入。这一版 magpie 随安装包提供。\n上一版每次进入都是新会话。这一版沿用其自身会话。\n"
    assert not any("成对句式" in p for p in rules.copy_problems(two, "v9.9.9.md"))

    three = two + "上一版全机共用一档模型。这一版可按 Agent 指定。\n"
    problems = rules.copy_problems(three, "v9.9.9.md")
    assert any("成对句式" in p for p in problems)


def test_internal_implementation_words_are_caught():
    body = CLEAN.replace(
        "**任务单据** — 派出去的工作有单据可查：当前状态、派给了谁。",
        "**任务单据** — 派活走 `plobi/tasks.py` 里的真源表。",
    )
    problems = rules.copy_problems(body, "v9.9.9.md")
    assert any("内部实现词" in p for p in problems)


def test_a_code_block_is_not_internal_wording():
    """从源码跑起来的命令是明写给开发者看的，不算「内部实现混进用户面」。"""
    body = CLEAN + "\n```bash\ncd apps/desktop && npm install\n```\n"
    assert rules.copy_problems(body, "v9.9.9.md") == []


def test_wording_a_user_would_type_is_allowed():
    """`[[双链]]` 是用户要照着用的写法，0.3.0 里就有，它不该被判成内部词。"""
    body = CLEAN.replace(
        "**任务单据** — 派出去的工作有单据可查：当前状态、派给了谁。",
        "**笔记双链** — `[[双链]]` 可点击打开。",
    )
    assert rules.copy_problems(body, "v9.9.9.md") == []


def test_the_release_that_is_the_exemplar_passes():
    """0.3.0 是用户点名写得最好的那一份——尺不该把它判红。"""
    notes = REPO_ROOT / "docs" / "plobi" / "releases"
    for name in ("v0.3.0.md", "Plobi-0.3.0-release-notes.en.md"):
        text = (notes / name).read_text(encoding="utf-8")
        assert rules.copy_problems(text, name) == [], name


def test_every_release_note_in_the_repo_survives_the_user_face_ruler():
    """全目录扫一遍：这一把尺不许有漏网的（旧版本也算——它们也是发布页）。"""
    notes = REPO_ROOT / "docs" / "plobi" / "releases"
    files = sorted(p for p in notes.glob("*.md") if p.name != "README.md")
    assert files, "no release notes found in docs/plobi/releases"
    problems: list[str] = []
    for path in files:
        problems += rules.copy_problems(path.read_text(encoding="utf-8"), path.name)
    assert not problems, problems
