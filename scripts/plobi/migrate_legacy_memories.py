#!/usr/bin/env python3
"""把 ``<home>/memories/`` 里的存量拆进新的三份形状。只读默认，逐条验证后才允许删。

裁定 42/178 退休了「按分身家分持记忆」，但退休时只换了读侧（``tools.memory_tool``
现在解析 ``<共享根>/memory/``），没搬数据：这台上的旧那一份还躺在
``$PLOBI_HOME/memories/MEMORY.md``，而**没有任何提示词路径再读它**。这一支脚本把那
一格里还成立的事实拆成条目、按作用域放进新位置，再要人明确 ``--delete`` 才清旧目录。

三条硬规矩（都写进判据，不是嘴上说的）：

* **默认只算不写**（``--dry-run`` 是出厂行为）。``--apply`` 才落新那三份。
* **删之前逐条对账**：旧文件里每一 chunk 都能在新位置按原文找到，否则拒绝删并点名缺哪条。
  这一条就是「确认没用」四个字的机器版本 —— 用户 2026-10-20 令「旧东西确认没用就删掉」，
  前提是这个「确认」由脚本做，不靠人眼扫。
* **落点一律问被测代码自己的解析器**（``get_memory_dir`` / ``memory_path``），
  不许手拼字面路径；旧那一份的位置同样走 ``get_plobi_home()``。

    python Code/scripts/plobi/migrate_legacy_memories.py            # 只看
    python Code/scripts/plobi/migrate_legacy_memories.py --apply    # 写新家
    python Code/scripts/plobi/migrate_legacy_memories.py --apply --delete  # 对账过了才删旧

拆完的条目带出处指针（``〔出处 …〕``），指向这些事实的真源文档，方便下一趟复核。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

HERE = Path(os.path.dirname(os.path.abspath(__file__)))
CODE = HERE.parent.parent
if str(CODE) not in sys.path:
    sys.path.insert(0, str(CODE))

from plobi_constants import get_plobi_home  # noqa: E402
from tools.memory_tool import (  # noqa: E402
    ENTRY_DELIMITER,
    entry_with_source,
    load_on_disk_store,
    memory_path,
)

# 旧位置那一格 → 新作用域。旧 MEMORY.md 是所有 profile 共用的一份（裁定 42 之前
# get_memory_dir() 谁都解析到同一个文件），所以它的新家是「跨 Agent 都成立」的
# 全局那一格；旧 USER.md 本来就是画像，位置换、语义不换。
LEGACY_SCOPES = (("MEMORY.md", "global"), ("USER.md", "user"))

# 拆出来的每条事实该指回哪一份真源（相对指针，不许多处第二份全文）。
# global → 仓内文档索引（这些纪律的真源就是 Docs/，机器可核：文件在那儿）；
# user → 脑仓里那份人读画像的逻辑路径（Vault/… 是召回与写入共用的逻辑前缀）。
SOURCE_BY_SCOPE = {
    "global": "Docs/README.md",
    "user": "Vault/meta/profile/profile.md",
}


def _legacy_dir() -> Path:
    return get_plobi_home() / "memories"


def _chunks(path: Path) -> list[str]:
    """旧文件的条目名单：按 § 切，去掉首尾空段。"""
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    return [c.strip() for c in text.split(ENTRY_DELIMITER) if c.strip()]


# 无损拆分：只在「；」「。」之后下刀，分隔符留在前一段尾巴上，所以
# "".join(split_lossless(x)) == x 逐字成立——不丢一个字，也不改一个字。
_BOUNDARY_RE = re.compile(r"(?<=[；。])")
MIN_FRAGMENT = 12


def split_lossless(chunk: str) -> list[str]:
    """把一整段拆成条目名单（可证明无损：拼回去必须等于原文，否则不拆）。

    短段不拆（拆出一句「铁律：」这种没头没尾的碎片反而更差）；拆完任何一段太短、
    或拼回去不等于原文，就退回整段——**宁可不拆，也不许改写成另一句话**。
    """
    parts = [p for p in _BOUNDARY_RE.split(chunk) if p.strip()]
    if len(parts) < 2:
        return [chunk]
    if "".join(parts) != chunk:
        return [chunk]
    if any(len(p.strip("；。 \n")) < MIN_FRAGMENT for p in parts):
        return [chunk]
    return parts


def plan(legacy: Path | None = None, *, split: bool = False,
         source_by_scope: dict[str, str] | None = None) -> list[tuple[str, str, str]]:
    """[(scope, 原文条目, 出处)] —— 只读，不碰任何文件。"""
    legacy = legacy or _legacy_dir()
    sources = source_by_scope or SOURCE_BY_SCOPE
    out: list[tuple[str, str, str]] = []
    for fname, scope in LEGACY_SCOPES:
        for chunk in _chunks(legacy / fname):
            for piece in (split_lossless(chunk) if split else [chunk]):
                out.append((scope, piece, sources[scope]))
    return out


def migrate(*, apply: bool = False, legacy: Path | None = None,
            split: bool = False, source_by_scope: dict[str, str] | None = None) -> dict:
    """把 plan() 的条目并进新那三份。已存在的原文不重复插。"""
    items = plan(legacy, split=split, source_by_scope=source_by_scope)
    store = load_on_disk_store()
    added: dict[str, int] = {}
    for scope, chunk, source in items:
        rendered = entry_with_source(chunk, source)
        current = list(store._entries_for(scope))
        already = any(chunk in e for e in current)
        if not already:
            current.append(rendered)
            if apply:
                store._set_entries(scope, current)
            added[scope] = added.get(scope, 0) + 1
    if apply:
        for scope in added:
            store.save_to_disk(scope)
        store = load_on_disk_store()  # 重读一遍，后面按盘上的实况对账
    return {
        "planned": len(items),
        "added": added,
        "targets": {scope: str(memory_path(scope, slug=store.slug)) for scope in added},
        "store": store,
        "items": items,
    }


def verify_migrated(store, originals: list[tuple[str, str, str]], *,
                    split: bool = False) -> list[str]:
    """返回「新家拼不回原文」的名单。空名单 = 可以删。

    对账以**未拆**的原文为准：拆过的条目必须逐段找得到、顺序不乱、且片段连起来
    逐字等于原文——「确认没用」的另一半是「确认没拆坏」。
    """
    problems: list[str] = []
    for scope, chunk, _ in originals:
        entries = store._entries_for(scope)
        frags = split_lossless(chunk) if split else [chunk]
        positions: list[int] = []
        for frag in frags:
            hits = [i for i, e in enumerate(entries) if frag in e]
            if not hits:
                problems.append(f"{scope}: 新家找不到这段 → {frag[:50]}")
                break
            positions.append(hits[0])
        else:
            if positions != sorted(positions):
                problems.append(f"{scope}: 片段顺序不对，拼不回原文")
            elif "".join(frags) != chunk:
                problems.append(f"{scope}: 片段连起来不等于原文（拆损了）")
    return problems


def delete_legacy(legacy: Path) -> list[str]:
    removed: list[str] = []
    if not legacy.is_dir():
        return removed
    for path in sorted(legacy.iterdir()):
        if path.is_file():
            path.unlink()
            removed.append(path.name)
    for path in sorted(legacy.iterdir()):  # 只剩子目录（旧 LOCK 之类不会留）
        if path.is_dir():
            for f in sorted(path.glob("*")):
                if f.is_file():
                    f.unlink()
                    removed.append(f"{path.name}/{f.name}")
            path.rmdir()
    legacy.rmdir()
    removed.append("<dir>")
    return removed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="真的写进新那三份（默认只算）")
    ap.add_argument("--delete", action="store_true", help="对账通过后删掉旧目录（需 --apply）")
    ap.add_argument("--split", action="store_true",
                    help="按「；」「。」边界无损拆条（可拼回原文才拆）")
    args = ap.parse_args(argv)

    legacy = _legacy_dir()
    originals = plan(legacy)              # 对账以未拆原文为准
    items = plan(legacy, split=args.split)
    print(f"旧位置 = {legacy}  现存条目 = {len(originals)}"
          f"{f'（拆成 {len(items)} 条）' if args.split else ''}")
    for scope, chunk, source in items:
        print(f"  → {scope}: {chunk[:70]}{'…' if len(chunk) > 70 else ''}  〔出处 {source}〕")
    if not items:
        print("没有要搬的存量（旧目录不存在或为空）。")
        return 0

    res = migrate(apply=args.apply, legacy=legacy, split=args.split)
    verb = "已写入" if args.apply else "打算写入"
    for scope, n in (res["added"] or {}).items():
        print(f"{verb} {scope}: {n} 条 -> {res['targets'][scope]}")
    if not args.apply:
        print("（只读模式，未写盘。加 --apply 才动。）")
        return 0

    store = res["store"]
    problems = verify_migrated(store, originals, split=args.split)
    if problems:
        print(f"RED — {len(problems)} 条在新家拼不回原文，旧目录保留：")
        for m in problems:
            print(f"  - {m}")
        return 1
    print(f"对账通过：{len(originals)} 条原文都能在新位置逐字拼回来。")

    if args.delete:
        removed = delete_legacy(legacy)
        print(f"已删旧目录内容：{', '.join(removed)}")
    else:
        print("旧目录保留（没给 --delete）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
