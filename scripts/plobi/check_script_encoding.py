#!/usr/bin/env python3
"""Windows 上跑的脚本：带非 ASCII 字符的 `.ps1`／`.cmd`／`.bat` 必须带 UTF-8 BOM。

为什么（2026-11-18 现场）：`scripts/install.ps1` 里有中文，仓里存的是 **UTF-8 无 BOM**。
Windows PowerShell 5.1 读**没有 BOM** 的文件时按 **ANSI 码页**解（中文机器＝GBK）⇒
中文字节被错解 ⇒ 引号与花括号对不上 ⇒ 解析器报「catch 语句缺少右花括号」
⇒ **首启 bootstrap 直接死在语法错误上，后端起不来**。

实测（2026-11-17 09:24，一个中文 Windows 用户的首次安装）：
```
所在位置 D:\\plobi\\resources\\backend\\scripts\\install.ps1:2689 字符: 17
+         } catch {
MissingEndCurlyBrace
```
同一个文件按 UTF-8 读毫无问题，按 GBK 读报的**行号列号一模一样**（2689:17 / 2682:83 / 2677:37）
⇒ 根因确定是编码，不是脚本写错。

**为什么是 BOM 而不是「把中文都换成英文」**：那些中文是**给用户看的提示**（`Write-Info`），
换成英文是倒退；而 BOM 是 Windows 自己的约定：带 BOM 的 UTF-8 文件，PowerShell 5.1 一定按
UTF-8 解，**不管机器的 ANSI 码页是什么**。

**纯 ASCII 的脚本不需要 BOM**（ASCII 在任何码页下解出来都一样）——本门只抓「有中文又没 BOM」
这一种会炸的形状。

用法：
    python scripts/plobi/check_script_encoding.py            # 核整个仓
    python scripts/plobi/check_script_encoding.py <路径>...  # 核指定几个文件

退出码：0 全过；1 有会炸的脚本（逐个点名）；2 读不到文件
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SCRIPT_SUFFIXES = (".ps1", ".cmd", ".bat")
# 这些目录不进包，也不归我们判。
SKIP_PARTS = ("node_modules", ".venv", "third_party", "build", "dist", "release", ".git")

UTF8_BOM = b"\xef\xbb\xbf"


def is_script(path: Path) -> bool:
    return path.suffix.lower() in SCRIPT_SUFFIXES


def has_non_ascii(data: bytes) -> bool:
    return any(byte > 0x7F for byte in data)


def has_bom(data: bytes) -> bool:
    return data.startswith(UTF8_BOM)


def check_file(path: Path) -> str | None:
    """有问题返回一句话；没问题返回 None。"""
    try:
        data = path.read_bytes()
    except OSError as exc:
        return f"读不到：{exc}"
    if not has_non_ascii(data):
        return None
    if has_bom(data):
        return None
    return (
        "带中文却没有 UTF-8 BOM —— 在 ANSI 码页不是 UTF-8 的 Windows 上，"
        "Windows PowerShell 5.1 会按 ANSI 解它，中文字节被错解 ⇒ 脚本解析失败"
        "（2026-11-17 装机现场：install.ps1 解析错误，首启 bootstrap 死在语法错误上）"
    )


def iter_scripts(root: Path):
    """走 os.walk 而不是 rglob：仓里躺着 macOS 的 app bundle（`release/mac-arm64/…`），
    那些目录项在 Windows 上 stat 会抛 WinError 1920 ⇒ 逐个跳，别让它把门带崩。"""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_PARTS]
        for name in filenames:
            if not name.lower().endswith(SCRIPT_SUFFIXES):
                continue
            path = Path(dirpath) / name
            try:
                if not path.is_file():
                    continue
            except OSError:
                continue
            yield path


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    targets = [Path(a).resolve() for a in argv] if argv else list(iter_scripts(ROOT))

    problems: list[str] = []
    checked = 0
    for path in targets:
        if not path.is_file():
            print(f"读不到：{path}", file=sys.stderr)
            return 2
        checked += 1
        problem = check_file(path)
        if problem:
            problems.append(f"{path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}：{problem}")

    if problems:
        print(f"RED — {len(problems)} 个 Windows 脚本会在非 UTF-8 码页的机器上炸：", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "\n  修法：把那些脚本**存成 UTF-8 with BOM**（BOM ＝ 文件头三个字节 EF BB BF）。\n"
            "  PowerShell 5.1 见到 BOM 就按 UTF-8 解，机器的 ANSI 码页是什么都不影响。",
            file=sys.stderr,
        )
        return 1

    print(f"GREEN — {checked} 个 Windows 脚本：带中文的都有 UTF-8 BOM")
    return 0


if __name__ == "__main__":
    sys.exit(main())
