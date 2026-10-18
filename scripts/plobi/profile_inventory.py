#!/usr/bin/env python3
"""ADR-0013 的完成度总账：**只数，不改**。

派工口径要的是每类文件「现读几份、谁在写、能不能停读」里那第一列。
这一列不许靠手拼字面路径去数（本仓 10-17 就栽过一次：拿 `mind\\Vault\\projects\\Aura`
判「子树不存在」）——落点一律问被测代码自己的解析器，数出来的才是**它实际会读到的那份**。

只读保证：全程只用 `is_dir` / `is_file` / `iterdir` / `stat`，不 `open(..., "w")`、
不调任何 `ensure_*` / `create_profile` / `seed_*`（那几个会写盘）。
它不动 `$PLOBI_HOME/**` 一个字节，跑一百遍与跑一遍一样。

    python Code/scripts/plobi/profile_inventory.py
    python Code/scripts/plobi/profile_inventory.py --sizes
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(os.path.dirname(os.path.abspath(__file__)))
CODE = HERE.parent.parent
if str(CODE) not in sys.path:
    sys.path.insert(0, str(CODE))

# 每类：显示名 → (相对分身教的路径, 是目录吗, 读链走哪个解析器)
# 解析器列只是**说明这份数是从谁的嘴里读出来的**；真正解析在 resolve_notes 里统一做。
CLASSES = [
    ("config.yaml", "config.yaml", False),
    ("SOUL.md", "SOUL.md", False),
    ("plobi/models.json", "plobi/models.json", False),
    ("models_dev_cache.json", "models_dev_cache.json", False),
    (".env", ".env", False),
    ("auth.json", "auth.json", False),
    ("skills/", "skills", True),
    ("memories/", "memories", True),
    ("sessions/", "sessions", True),
    ("plans/", "plans", True),
    ("skins/", "skins", True),
    ("workspace/", "workspace", True),
    ("home/", "home", True),
    ("cron/", "cron", True),
    ("logs/", "logs", True),
]


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} GB"


def tree_size(p: Path, cap: int | None = None) -> int:
    """目录里所有文件的字节数。只 stat，不读内容。"""
    total = 0
    for root, dirs, files in os.walk(p):
        for f in files:
            fp = Path(root) / f
            try:
                total += fp.stat().st_size
            except OSError:
                pass
        if cap and total > cap:
            break
    return total


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="按分身复制的文件到底有几份（只数不改）")
    ap.add_argument("--sizes", action="store_true", help="顺带算每类的合计字节（大目录会慢一些）")
    ap.add_argument("--quarantine", action="store_true", default=True,
                    help="列每家 `_quarantine-*` 里装了哪几档（默认开）")
    a = ap.parse_args(argv)

    from plobi_constants import (config_path_for_home, get_plobi_home,
                                 get_shared_plobi_root, shared_root_for)
    from plobi_cli.profiles import _get_profiles_root

    home = get_plobi_home()
    proot = _get_profiles_root()
    sroot = get_shared_plobi_root()
    print("解析器给的落点（三个都问代码，不手拼）：")
    print(f"  get_plobi_home()      = {home}")
    print(f"  _get_profiles_root()  = {proot}   exists={proot.is_dir()}")
    print(f"  get_shared_plobi_root()= {sroot}   exists={sroot.is_dir()}")
    if not proot.is_dir():
        print(f"\nFATAL — 分身根目录不在盘上：{proot}（这台还没建过任何分身？）")
        return 2

    homes = sorted(p for p in proot.iterdir() if p.is_dir() and not p.name.startswith((".", "_")))
    print(f"  └─ 现读分身家数        = {len(homes)}")

    print("\n每类现读几份（分母＝上面那个家数；「共享」列＝顶层那一份在不在）")
    print(f"{'类':<24}{'分身家有':>9}{'合计':>10}   说明")
    rows = []
    for label, rel, is_dir in CLASSES:
        n = 0
        size = 0
        for h in homes:
            p = h / rel
            if p.is_dir():
                n += 1
                if a.sizes:
                    size += tree_size(p)
            elif p.is_file():
                n += 1
                if a.sizes:
                    size += p.stat().st_size
        top = sroot / rel
        shared = "有" if (top.is_file() or top.is_dir()) else "无"
        rows.append((label, n, size, shared))
        print(f"{label:<24}{n:>5}/{len(homes)}{(human(size) + '  ') if a.sizes and size else '':>10}   顶层一份：{shared}")

    # config.yaml 的读链单独核一遍：数「有几份复印件」不够，要看**那个家实际会读哪一份**
    print("\nconfig.yaml 读链现读（`config_path_for_home`，裁 166 那条）：")
    read_shared = read_own = missing = 0
    for h in homes:
        cp = config_path_for_home(h)
        if not cp.is_file():
            missing += 1
        elif shared_root_for(h) != h and cp.parent == shared_root_for(h):
            read_shared += 1
        else:
            read_own += 1
    print(f"  读到共享那份 = {read_shared} 家 | 仍读到自家复印件 = {read_own} 家 | 两处都没有 = {missing} 家")
    print(f"  （活跃家自己走 {config_path_for_home(home).as_posix()}）")

    if a.quarantine:
        print("\n_quarantine-* 里装了哪几档（撤下来的复印件，按「先隔离不删」）")
        seen = 0
        for h in homes:
            for q in sorted(h.iterdir()):
                if q.is_dir() and q.name.startswith("_quarantine"):
                    files = sorted(f.name for f in q.rglob("*") if f.is_file())
                    print(f"  {h.name}/{q.name}: {', '.join(files) if files else '(空)'}")
                    seen += 1
        top_q = sorted(p.name for p in sroot.iterdir() if p.name.startswith("_quarantine")) if sroot.is_dir() else []
        if top_q:
            print(f"  顶层 {sroot.name}: {', '.join(top_q)}")
        if not seen:
            print("  （一家都没有——那把隔离刀还没跑，或跑在别处）")

    print("\n本脚本只数不改：全程 is_dir/is_file/iterdir/stat，未 open 任何文件写、未调用 ensure_*/create_profile。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
