#!/usr/bin/env python3
"""Windows 上跑的脚本那把闸：**带非 ASCII 字符的 `.ps1`／`.cmd`／`.bat` 必须带 UTF-8 BOM**，
**且 `.ps1` 的 `param` 块必须是脚本的第一句**。

为什么有 BOM 这一条（2026-11-18 现场）：`scripts/install.ps1` 里有中文，仓里存的是 **UTF-8 无 BOM**。
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

**为什么有 `param` 第一句这一条**（2026-11-18 现场，同一把刀带进来的回归）：PowerShell 要求
`param` 是脚本的**第一句**（前面只能有注释）。把一段**代码**（哪怕只是设置输出编码）塞在
`[CmdletBinding()]` 前面 ⇒ 解析器报「Unexpected attribute 'CmdletBinding]'」
⇒ 脚本**根本起不来**（`bezel_start.ps1` 与 `chatlog_server.ps1` 都中过）。
这条判据是**纯文本**的 ⇒ CI 的 Linux 机器上也能判，不需要 PowerShell。

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

# `param` 必须是脚本的第一句——前面只能有注释。这两条是「允许出现在 param 之前的」形状。
CMDLET_BINDING = "[CmdletBinding()]"


def is_script(path: Path) -> bool:
    return path.suffix.lower() in SCRIPT_SUFFIXES


def has_non_ascii(data: bytes) -> bool:
    return any(byte > 0x7F for byte in data)


def has_bom(data: bytes) -> bool:
    return data.startswith(UTF8_BOM)


def has_top_level_param(lines: list[str]) -> bool:
    """顶层 `param(`：**行首**就是 `param(`（缩进的那些是函数自己的参数，不是脚本的）。"""
    return any(line.startswith("param(") for line in lines)


def param_block_not_first(data: bytes) -> str | None:
    """`param` 块不是第一句 ⇒ 返回一句话；没问题返回 None。

    PowerShell 的规则：`param` 必须是脚本的**第一句**（前面只能有注释、空白、
    以及随后的 `[CmdletBinding()]`）。前面出现任何**代码**，解析器就报
    「Unexpected attribute 'CmdletBinding]'」⇒ 脚本起不来。

    2026-11-18 现场：把「输出编码」块（那是代码）塞在 `[CmdletBinding()]` 前面，
    `bezel_start.ps1` 与 `chatlog_server.ps1` 两个脚本当场起不来。
    """
    # `utf-8-sig`：BOM 要**剥掉**再看第一行——留着它，第一行就不以 `#` 开头，
    # 于是每个带 BOM 的脚本都被当成「param 前面有代码」。
    text = data.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()

    if not has_top_level_param(lines):
        return None

    in_block_comment = False

    for index, line in enumerate(lines, 1):
        stripped = line.strip()

        if in_block_comment:
            if "#>" in stripped:
                in_block_comment = False
            continue

        if not stripped:
            continue

        if stripped.startswith("<#"):
            in_block_comment = "#>" not in stripped
            continue

        if stripped.startswith("#"):
            continue

        # 到第一句「代码」了。
        if stripped.startswith(CMDLET_BINDING):
            continue

        if line.startswith("param("):
            return None

        return (
            f"param 块不是第一句（第 {index} 行先出现了代码）——"
            "PowerShell 要求 param 是脚本的第一句（前面只能有注释），"
            "否则报「Unexpected attribute 'CmdletBinding]'」，脚本根本起不来"
        )

    return None


def check_file(path: Path) -> list[str]:
    """有问题返回一句话的列表；没问题返回空列表。"""
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [f"读不到：{exc}"]

    problems: list[str] = []

    if has_non_ascii(data) and not has_bom(data):
        problems.append(
            "带中文却没有 UTF-8 BOM —— 在 ANSI 码页不是 UTF-8 的 Windows 上，"
            "Windows PowerShell 5.1 会按 ANSI 解它，中文字节被错解 ⇒ 脚本解析失败"
            "（2026-11-17 装机现场：install.ps1 解析错误，首启 bootstrap 死在语法错误上）"
        )

    if path.suffix.lower() == ".ps1":
        problem = param_block_not_first(data)
        if problem:
            problems.append(problem)

    return problems


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
        for problem in check_file(path):
            problems.append(f"{path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}：{problem}")

    if problems:
        print(f"RED — {len(problems)} 处会让 Windows 上的脚本炸掉：", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "\n  修法（编码那条）：把那些脚本**存成 UTF-8 with BOM**（BOM ＝ 文件头三个字节 EF BB BF）。\n"
            "  PowerShell 5.1 见到 BOM 就按 UTF-8 解，机器的 ANSI 码页是什么都不影响。\n"
            "  修法（param 那条）：把 `param(...)` 挪回脚本第一句（前面只能留注释），"
            "要放在 param 之后的代码原样跟着挪。",
            file=sys.stderr,
        )
        return 1

    print(f"GREEN — {checked} 个 Windows 脚本：带中文的都有 UTF-8 BOM，param 块都在第一句")
    return 0


if __name__ == "__main__":
    sys.exit(main())
