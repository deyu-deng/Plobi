"""Windows 脚本那把闸的测试（编码 ＋ param 第一句）。

为什么要有这把尺：2026-11-17 一个中文 Windows 用户首装 0.3.2，`install.ps1` 被 Windows
PowerShell 5.1 按 ANSI（GBK）解 ⇒ 中文被错解 ⇒ 引号与花括号对不上 ⇒ 解析器报
→「catch 语句缺少右花括号」⇒ **首启 bootstrap 死在语法错误上，后端起不来**。
本仓当时的约定是「install.ps1 保持纯 ASCII 以兼容 PS 5.1 解析器」，后来有人在里面加了
中文，那条约定就破了，而没有人检查 ⇒ 闸在这里：**带中文的 Windows 脚本必须有 UTF-8 BOM**。

第二半（2026-11-18，同一把刀带进来的回归）：PowerShell 要求 `param` 是脚本的**第一句**。
把一段代码（设置输出编码）塞在 `[CmdletBinding()]` 前面 ⇒ 两个脚本当场起不来。
所以这把尺也管那一条。

这些用例全是离线的：造临时文件，不碰真实仓。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts" / "plobi"


def _load():
    spec = importlib.util.spec_from_file_location(
        "check_script_encoding", SCRIPTS / "check_script_encoding.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_script_encoding"] = module
    spec.loader.exec_module(module)
    return module


enc = _load()


def test_a_chinese_script_without_a_bom_is_the_offender(tmp_path: Path):
    path = tmp_path / "bad.ps1"
    path.write_bytes("# 中文\nWrite-Output '在跑'\n".encode("utf-8"))
    problems = enc.check_file(path)
    assert problems and any("BOM" in problem for problem in problems)


def test_the_same_script_with_a_bom_is_clean(tmp_path: Path):
    path = tmp_path / "good.ps1"
    path.write_bytes(enc.UTF8_BOM + "# 中文\nWrite-Output '在跑'\n".encode("utf-8"))
    assert enc.check_file(path) == []


def test_a_pure_ascii_script_needs_no_bom(tmp_path: Path):
    path = tmp_path / "ascii.ps1"
    path.write_bytes(b"Write-Output 'hello'\n")
    assert enc.check_file(path) == []


def test_a_bom_makes_powershell_read_utf8_whatever_the_ansi_codepage_is():
    """BOM 是 Windows 自己的约定：见到 BOM 就按 UTF-8 解，与机器码页无关。

    这条不是文档上的断言，是这段话本身的前提：**没有 BOM**，PS 5.1 就按 ANSI 码页解
    （中文机器＝GBK）⇒ 中文字节被错解 ⇒ 引号/花括号对不上。带上 BOM，它就不看码页了。
    """
    assert enc.UTF8_BOM == b"\xef\xbb\xbf"
    assert enc.has_bom(enc.UTF8_BOM + b"x")
    assert not enc.has_bom(b"x")


def test_the_gate_passes_on_this_repo():
    """真仓自己过一遍：今天修完 ⇒ 带中文的脚本都有 BOM，param 块都在第一句。"""
    problems = []
    for path in enc.iter_scripts(REPO_ROOT):
        for problem in enc.check_file(path):
            problems.append(f"{path}: {problem}")
    assert not problems, problems


def test_the_gate_catches_a_script_that_lost_its_bom(tmp_path: Path):
    """把 BOM 拿掉 ⇒ 门要红（这正是 2026-11-17 那台机器踩的形状）。"""
    script = tmp_path / "install.ps1"
    script.write_bytes(enc.UTF8_BOM + "# 中文\nWrite-Output '在跑'\n".encode("utf-8"))
    assert enc.check_file(script) == []
    script.write_bytes(script.read_bytes()[len(enc.UTF8_BOM):])
    assert enc.check_file(script)


# --- param 必须是第一句（2026-11-18 那把回归）---


def test_code_before_the_param_block_is_the_offender(tmp_path: Path):
    """`param` 前面有代码 ⇒ 脚本起不来（解析器报 Unexpected attribute 'CmdletBinding]'）。"""
    script = tmp_path / "late.ps1"
    script.write_bytes(
        enc.UTF8_BOM
        + (
            "# 注释\n"
            "try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch { }\n"
            "[CmdletBinding()]\n"
            "param(\n"
            "    [string]$Config = ''\n"
            ")\n"
        ).encode("utf-8")
    )

    problems = enc.check_file(script)
    assert problems, problems
    assert any("param" in problem for problem in problems)


def test_param_after_only_comments_is_clean(tmp_path: Path):
    script = tmp_path / "ok.ps1"
    script.write_bytes(
        enc.UTF8_BOM
        + (
            "<#\n"
            ".SYNOPSIS\n"
            "    干什么的\n"
            "#>\n"
            "# 一行注释\n"
            "[CmdletBinding()]\n"
            "param(\n"
            "    [string]$Config = ''\n"
            ")\n"
            "Write-Output '在跑'\n"
        ).encode("utf-8")
    )

    assert enc.check_file(script) == []


def test_a_function_local_param_is_not_the_script_param(tmp_path: Path):
    """函数自己的 `param(` 是缩进的，不算脚本的 param 块 ⇒ 不该被这条判据抓。"""
    script = tmp_path / "funcs.ps1"
    script.write_bytes(
        enc.UTF8_BOM
        + (
            "Write-Output '在跑'\n"
            "function Get-Thing {\n"
            "    param([string]$Path)\n"
            "    Write-Output $Path\n"
            "}\n"
        ).encode("utf-8")
    )

    assert enc.check_file(script) == []


def test_a_script_without_any_param_block_is_not_judged(tmp_path: Path):
    script = tmp_path / "plain.ps1"
    script.write_bytes(enc.UTF8_BOM + "Write-Output '在跑'\n".encode("utf-8"))

    assert enc.check_file(script) == []
