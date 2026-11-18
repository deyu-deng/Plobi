#!/usr/bin/env python3
"""从网关名册生成 ``providers/hub_software.py`` 里的 ``HUB_SOFTWARE`` 表。

谁是「软件」这件事，真源只有一份：网关自己的
``bezel/bezel/providers/apps/magpie/providers.yaml``
（左边是我们的 id = **模型 id 前缀**，``name`` 是屏上读的名字）。
``providers/hub_software.py`` 里那张表必须由本脚本生成 —— 手抄一次就会错一次：
2026-11-18 之前手抄的 9 项里，有 6 项的名字与真源对不上
（``antigravity`` vs ``agy-cli``、``qoder_intl`` vs ``qoder-cn``、
``opencode_api`` vs ``opencode-zen-free``…），还漏了 13 家。

用法::

    python scripts/gen_hub_software.py            # 打印生成的表格块
    python scripts/gen_hub_software.py --check    # 与 hub_software.py 现值比对，漂了就返回 1

``tests/plobi_cli/test_hub_software_roster.py`` 会跑 ``--check``，
所以真源一改而表没重生成，CI 直接红。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ROSTER_PATH = REPO_ROOT / "bezel" / "bezel" / "providers" / "apps" / "magpie" / "providers.yaml"
TARGET_PATH = REPO_ROOT / "providers" / "hub_software.py"

BEGIN = "# >>> HUB_SOFTWARE (generated from bezel/bezel/providers/apps/magpie/providers.yaml) >>>"
END = "# <<< HUB_SOFTWARE <<<"


def load_roster(path: Path = ROSTER_PATH) -> "list[tuple[str, str]]":
    """按名册顺序返回 ``[(app_id, display_name), ...]``。

    不引第三方 yaml 依赖：这张表的结构是固定的两级缩进，正则足够；
    也避免脚本在裸 python 环境下跑不起来。
    """
    text = path.read_text(encoding="utf-8")
    entries: "list[tuple[str, str]]" = []
    current: "str | None" = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        m = re.match(r"^  ([A-Za-z0-9][A-Za-z0-9_.-]*):\s*$", line)
        if m:
            current = m.group(1)
            continue
        m = re.match(r"^    name:\s*(.+?)\s*$", line)
        if m and current:
            name = m.group(1).strip().strip('"').strip("'")
            entries.append((current, name))
            current = None
    if not entries:
        raise SystemExit(f"no providers parsed from {path}")
    return entries


def render_block(entries: "list[tuple[str, str]]") -> str:
    width = max(len(f'"{sid}":') for sid, _ in entries)
    lines = [BEGIN, "HUB_SOFTWARE: dict[str, str] = {"]
    for sid, name in entries:
        key = f'"{sid}":'
        lines.append(f"    {key.ljust(width)} {name!r},")
    lines.append("}")
    lines.append(END)
    return "\n".join(lines)


def current_block(target_text: str) -> "str | None":
    start = target_text.find(BEGIN)
    end = target_text.find(END)
    if start < 0 or end < 0:
        return None
    return target_text[start : end + len(END)]


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="只比对，不打印；不一致时返回 1")
    args = ap.parse_args(argv)

    entries = load_roster()
    generated = render_block(entries)

    if not args.check:
        print(generated)
        return 0

    text = TARGET_PATH.read_text(encoding="utf-8")
    existing = current_block(text)
    if existing is None:
        print(f"{TARGET_PATH.name}: HUB_SOFTWARE generated block markers not found", file=sys.stderr)
        return 1
    if existing != generated:
        print(f"{TARGET_PATH.name}: HUB_SOFTWARE is stale vs providers.yaml — run scripts/gen_hub_software.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
