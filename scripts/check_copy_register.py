#!/usr/bin/env python3
"""产品文案语域门：静态产品文案必须是书面产品语言，不许口语、吐槽腔、第一人称代言。

**存在的理由**（用户 2026-11-07 的原话）：Agent 新写的文案口语化、啰嗦，不像行业标杆。
"像行业标杆"是个形容词，抓不住；这里把它翻成可 grep 的硬判据，一条一条列在
`apps/desktop/src/i18n/copy-register.test.ts` 的 `ZH_RULES` / `EN_RULES` / `JA_RULES`：

    C1 句末语气词   了哦/啦/哈/呗/嘛            「同步完成啦」
    C2 代办铺垫     我来帮你/给你/咱们散着来      「我来帮你打开设置」
    C3 空形容词     丝滑/强大/极致               「丝滑的写作体验」
    C4 口语词       搞定/弄好/点一下/点开*/东西   「点开这一行看看」
    C5 第一人称复数 我们/我们的；we/our/us        「我们要构建什么」
    C6 破折号       入报告项，不判红（英文行文用得上）
    C7 一句多动作   顿号列表拼多段，入报告项
    C8 超长句       入报告项，只提示不拦

**范围界线**（用户同一天明确）：只约束**静态产品文案**——仓库 i18n 四语文案，以及今后
所有产品层文案。用户与 Agent 的对话文案是会变的运行时文本，不在约束内。

**为什么门落在 TS 测试、这里只做调度**：i18n 是 TS 对象，vitest 直接 import 就能拿到
结构化的键值，一条规则只有一个真身。在这里再照抄一遍正则副本，两份迟早漂移；而
「会冤枉人的门，第三次就没人看它了」（R-050，见 check_status_copy_coverage.py 同款教训）。
所以本脚本只干一件事：暂存里动了 i18n 就把那份测试跑起来，红就拦。

**靶子的边界**：`apps/desktop/src/i18n/` 是硬门对象。绕过 i18n 直接写在 .tsx/.ts 里的
中文字面量属于「i18n 覆盖度」问题，归 retired-words / coverage 那一族，不在本门范围。

环境不全时（没装 node_modules）只出声、不拦人——环境缺失不是文案的错。
紧急跳过：SKIP_COPY_REGISTER=1（Agent 不该用）。

退出码：0＝绿或本次没动 i18n；1＝红。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "apps" / "desktop"
I18N_DIR = "apps/desktop/src/i18n/"
TARGET = "src/i18n/copy-register.test.ts"
TEST_FILE = DESKTOP / TARGET


def staged_i18n_files() -> list[str]:
    """暂存区里动过的 i18n 源文件（不含测试自身）。"""
    try:
        out = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    hits = []
    for line in out.splitlines():
        path = line.strip().replace("\\", "/")
        if path.startswith(I18N_DIR) and path.endswith(".ts") and not path.endswith(".test.ts"):
            hits.append(path)
    return hits


def find_node() -> str | None:
    found = shutil.which("node")
    if found:
        return found
    # pre-commit 的 PATH 常比登录 shell 短，退到几个常见位置找一遍。
    for cand in (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs" / "node.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "nodejs" / "node.exe",
    ):
        if cand.is_file():
            return str(cand)
    return None


def main() -> int:
    if os.environ.get("SKIP_COPY_REGISTER") == "1":
        print("文案语域门：SKIP_COPY_REGISTER=1，已跳过。")
        return 0

    touched = staged_i18n_files()
    if not touched:
        print("文案语域门：本次提交没动 i18n 文案，跳过。")
        return 0

    if not TEST_FILE.is_file():
        print(f"文案语域门：找不到 {TARGET}，无法判定——这不是文案的问题，不拦。", file=sys.stderr)
        return 0

    node = find_node()
    vitest = ROOT / "node_modules" / "vitest" / "vitest.mjs"
    if node is None or not vitest.is_file():
        missing = "node" if node is None else "node_modules/vitest"
        print(f"文案语域门：环境里没有 {missing}，跳过判定（文案本身未经检查）。", file=sys.stderr)
        return 0

    print(f"文案语域门：这次改到 {len(touched)} 个 i18n 文件，跑一遍语域判据——")
    for path in touched:
        print(f"  · {path}")

    env = dict(os.environ, VITE_CONFIG_NATIVE_IGNORE_WARNING="true", CI="1")
    proc = subprocess.run(
        [node, str(vitest), "run", TARGET, "--reporter=default"],
        cwd=DESKTOP, env=env, capture_output=True, text=True,
    )
    if proc.returncode == 0:
        print("文案语域门：绿。")
        return 0

    print(proc.stdout.rstrip())
    print(proc.stderr.rstrip(), file=sys.stderr)
    print(
        "\n文案语域门红：上面的句子读起来不像产品文案。\n"
        "判据与改法写在 apps/desktop/src/i18n/copy-register.test.ts 的 rules 表里，\n"
        "按 advice 那句改，改完重跑：node node_modules/vitest/vitest.mjs run src/i18n/copy-register.test.ts\n"
        "真属误判就往该文件的 ALLOWED 里登记 key 并写明理由，别放宽正则。",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
