"""把 ADR-0013 **甲组**存量搬进各家 ``_quarantine-<日期>/``（WP-PROFILE-CANCEL-QUARANTINE 第 4 步·甲组）。

纸上判据（``Docs/tasks/wp-profile-cancel-quarantine-task.md`` 第四节）：这一组零依赖、零行为变化，
因为里面只有两种东西——没人读的复印件，和本来就空的壳：

* ``models_dev_cache.json`` —— 读侧 :func:`agent.models_dev._get_cache_path` 解析到
  :func:`plobi_constants.get_shared_plobi_root`，分身家那一份没有生产者也没有读者。
* ``plans/`` · ``skins/`` · ``workspace/`` · ``home/`` · ``memories/`` —— 现核**一个文件都没有**
  （不是 0 字节，是 0 个文件）。它们不在 ``plobi_cli/config.py`` 的 ``_HOME_REQUIRED_SUBDIRS`` /
  ``_HOME_EXTRA_SUBDIRS`` 里，所以搬走不会被 ``ensure_plobi_home()`` 建回来——**这正是乙组做不到的那一条**
  （``cron``·``sessions``·``logs``·``skills`` 会被播种机逐轮补回来，搬了等于白搬）。

乙组、丙组、以及 ``state.db`` 一律不碰。**白名单写在结构里**（只认 ``ALLOWED`` 那六类），不是黑名单：
``state.db`` 就躺在同一个目录里（10-22 现读 20 家各一份，那是真会话总账，不在任何 git 仓），
一次手滑就是一次没有历史的损失。

只移不删——ADR-0013 硬约束第 2 条：真删要带回落读数由用户当场签。

    # 只看计划，一个字节都不写
    ./.venv/Scripts/python.exe scripts/plobi/quarantine_profile_remnants.py
    # 真搬
    ./.venv/Scripts/python.exe scripts/plobi/quarantine_profile_remnants.py --apply

**读数口径**（ADR-0013 硬约束第 3 条：落点一律问代码自己的解析器，不手拼字面路径）：
分母 ``profiles_root`` 走 ``plobi_cli.profiles._get_profiles_root()``；搬完之后立刻再测一遍
"那个家会读哪一份 ``models_dev_cache.json``"（``plobi_constants.shared_root_for``，纯函数、
不读环境、也不缓存，所以进程内逐家问就够了，不必像 ``isolate_profile_configs.py`` 那样为
``load_config`` 逐家新起进程）。判据是**家数不变**＋**盘点表里甲组六类归零**＋**回退 0 家**＋**报错 0 家**。

⚠️ **纸上第四节那句"总体积 110 320 KB → 约 24 400 KB（−85.9 MB）"在只移不删这一步是量不出来的**——
搬进 ``_quarantine-<日期>/`` 之后那些字节还躺在 ``profiles/`` 底下，``du -sk profiles`` 一个字都不少。
能降的只有盘点表里那六类的**份数与体积**（它们从"分身家的东西"变成"隔离区里的东西"，而读侧本来
就不看隔离区）。真正的 ``−85.9 MB`` 属于**真删那一轮**，那一轮只有用户拿着回落读数当场签才允许跑
（同 ``isolate_profile_configs.py`` 末段那句口径）。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# 甲组的两族：一份没人读的复印件，和五类本来就空的壳。
FILE_CLASS = "models_dev_cache.json"
SHELL_CLASSES = ("plans", "skins", "workspace", "home", "memories")
ALLOWED = (FILE_CLASS,) + SHELL_CLASSES

QUARANTINE_PREFIX = "_quarantine-"


def count_files(p: Path) -> int:
    """目录树里有几个文件。搬壳之前用它证明"壳真的是空的"，不是照抄纸上的旧数。"""
    return sum(1 for x in p.rglob("*") if x.is_file())


def tree_bytes(p: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(p):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total


def homes_of(profiles_root: Path) -> list[Path]:
    if not profiles_root.is_dir():
        return []
    return sorted(p for p in profiles_root.iterdir() if p.is_dir() and not p.name.startswith((".", "_")))


def measure(profiles_root: Path, homes: list[Path]) -> dict:
    """家数 / 总体积 / 甲组六类逐类现读——搬之前搬之后各测一遍，判据只看这三样怎么变。"""
    per_class: dict[str, dict] = {}
    for label in ALLOWED:
        n = 0
        b = 0
        empty_shells = 0
        for h in homes:
            p = h / label
            if p.is_file():
                n += 1
                b += p.stat().st_size
            elif p.is_dir():
                n += 1
                b += tree_bytes(p)
                if count_files(p) == 0:
                    empty_shells += 1
        per_class[label] = {"homes": n, "bytes": b, "empty_dirs": empty_shells}
    return {
        "homes": len(homes),
        "total_bytes": tree_bytes(profiles_root),
        "classes": per_class,
    }


def plan_moves(profiles_root: Path, date_tag: str, homes: list[Path]) -> tuple[list[dict], list[dict], list[dict]]:
    """算出要搬什么。**纯算，不碰盘**（dry-run 也走这一格，所以它绝不会自己把锚点搬走）。

    返回 ``(moves, refused, skipped)``：
      * ``moves``  —— 允许搬的（在白名单里，且壳确实是空的）。
      * ``refused`` —— 在白名单里但**壳不空**：当场拒绝并说明，等人来判那些文件是什么。
      * ``skipped`` —— 目标位已经有同名东西（幂等：重跑第二遍应当 0 搬）。
    """
    moves: list[dict] = []
    refused: list[dict] = []
    skipped: list[dict] = []
    qname = f"{QUARANTINE_PREFIX}{date_tag}"
    for h in homes:
        for label in ALLOWED:
            src = h / label
            if not src.exists():
                continue
            if src.is_dir() and count_files(src) > 0:
                refused.append({"home": h.name, "class": label,
                                "files": count_files(src),
                                "why": "空壳判据不成立——里面有文件，甲组不许搬有内容的目录"})
                continue
            dst = h / qname / label
            if dst.exists():
                skipped.append({"home": h.name, "class": label, "already": str(dst)})
                continue
            size = src.stat().st_size if src.is_file() else tree_bytes(src)
            moves.append({"home": h.name, "class": label, "src": str(src), "dst": str(dst),
                          "bytes": size, "kind": "file" if src.is_file() else "dir"})
    return moves, refused, skipped


def apply_moves(moves: list[dict]) -> tuple[list[dict], list[dict]]:
    """逐条 ``shutil.move``。一件失败不中断整批（后端正开着那个文件时 Windows 会报共享冲突），
    失败照实列出来——**判据是"0 家报错"，不是"看起来搬完了"**。"""
    done: list[dict] = []
    errors: list[dict] = []
    for m in moves:
        src = Path(m["src"])
        dst = Path(m["dst"])
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            done.append({"home": m["home"], "class": m.get("class", "whole-house"), "to": str(dst)})
        except OSError as exc:
            errors.append({"home": m["home"], "class": m.get("class", "whole-house"),
                           "error": f"{type(exc).__name__}: {exc}"})
    return done, errors


def read_chain(homes: list[Path]) -> dict:
    """每一家**现在会读到哪一份** ``models_dev_cache.json``，以及那份在不在。

    这一格是给"搬完有没有静默出事"作证的：读侧本来就走共享根，所以搬掉复印件之后
    每家仍应解析到顶层那一份，且那份必须是文件。回退（解析到自己家里）或缺失都点名。
    """
    from plobi_constants import shared_root_for

    shared = 0
    fallback = 0
    missing = 0
    errors: dict[str, str] = {}
    for h in homes:
        try:
            path = shared_root_for(h) / FILE_CLASS
            if path.parent == h:
                fallback += 1
            elif path.is_file():
                shared += 1
            else:
                missing += 1
        except OSError as exc:
            errors[h.name] = f"{type(exc).__name__}: {exc}"
    return {"read_shared_copy": shared, "fell_back_into_home": fallback,
            "shared_copy_missing": missing, "errors": errors}


def plan_whole_house(profiles_root: Path, date_tag: str, homes: list[Path]) -> tuple[list[dict], list[dict]]:
    """整屋退役：``<root>/profiles/<name>`` → ``<root>/_quarantine-<日期>/profiles/<name>``。

    为什么把隔离区放在 ``profiles/`` **外面**：`plobi profile list` 与探针都按
    ``profiles/`` 下的子目录数家，把 ``_quarantine-*`` 放进去虽然名字以 ``_`` 开头、
    也容易在某条忘记过滤的读路上变成一个"名字叫 quarantine 的分身"。放在共享根下、
    与 ``profiles/`` 平级，既在同一个盘上、又不改变"家里还剩几个"这个读数。

    返回 ``(moves, skipped)``。纯算，不碰盘。
    """
    moves: list[dict] = []
    skipped: list[dict] = []
    for h in homes:
        dst = profiles_root.parent / f"{QUARANTINE_PREFIX}{date_tag}" / "profiles" / h.name
        if dst.exists():
            skipped.append({"home": h.name, "already": str(dst)})
            continue
        moves.append({"home": h.name, "class": "whole-house", "src": str(h),
                      "dst": str(dst), "bytes": tree_bytes(h)})
    return moves, skipped


def plan_restore(profiles_root: Path, date_tag: str) -> tuple[list[dict], list[dict]]:
    """把某一次整屋退役**逐字搬回**：``_quarantine-<日期>/profiles/<name>`` → ``profiles/<name>``。

    存在即 skip，**不覆盖**——若那一家已经被别的进程建回来了（spawn 会），
    覆盖等于把别人写的东西当我的旧副本压回去。返回 ``(moves, skipped)``，纯算不碰盘。
    """
    src_root = profiles_root.parent / f"{QUARANTINE_PREFIX}{date_tag}" / "profiles"
    moves: list[dict] = []
    skipped: list[dict] = []
    if not src_root.is_dir():
        return moves, [{"home": "*", "missing_source": str(src_root)}]
    for h in sorted(p for p in src_root.iterdir() if p.is_dir()):
        dst = profiles_root / h.name
        if dst.exists():
            skipped.append({"home": h.name, "target_exists": str(dst)})
            continue
        moves.append({"home": h.name, "class": "restore", "src": str(h),
                      "dst": str(dst), "bytes": tree_bytes(h)})
    return moves, skipped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ADR-0013 甲组存量隔离 / 第 4 步整屋退役（只移不删）")
    ap.add_argument("--apply", action="store_true", help="真搬。不加这个参数只出计划。")
    ap.add_argument("--date", default=date.today().strftime("%Y%m%d"),
                    help="隔离目录的日期后缀（默认今天；写死是为了重跑幂等）")
    ap.add_argument("--profiles-root", default=None,
                    help="调试用：覆盖 `plobi_cli.profiles._get_profiles_root()`")
    ap.add_argument("--whole-house", action="store_true",
                    help="第 4 步整屋退役：把 profiles/<name> 整个移进 "
                         "<共享根>/_quarantine-<日期>/profiles/<name>（含 state.db/凭据/技能，只移不删）")
    ap.add_argument("--restore", action="store_true",
                    help="把某次整屋退役逐字搬回 profiles/（存在即 skip，不覆盖）——"
                         "用于回滚一次过早的数据搬运，不是恢复 profile 设计")
    args = ap.parse_args(argv)

    if args.profiles_root:
        profiles_root = Path(args.profiles_root)
        root_source = "--profiles-root"
    else:
        from plobi_cli.profiles import _get_profiles_root
        profiles_root = _get_profiles_root()
        root_source = "_get_profiles_root()"

    homes = homes_of(profiles_root)

    if args.restore:
        moves, skipped = plan_restore(profiles_root, args.date)
        done, errors = apply_moves(moves) if args.apply else ([], [])
        print(json.dumps({
            "mode": "apply" if args.apply else "dry-run",
            "batch": "restore",
            "profiles_root": str(profiles_root),
            "root_resolved_by": root_source,
            "from": str(profiles_root.parent / f"{QUARANTINE_PREFIX}{args.date}" / "profiles"),
            "planned_moves": len(moves),
            "planned_bytes": sum(m["bytes"] for m in moves),
            "skipped_target_exists": skipped,
            "moved": done,
            "errors": errors,
            "homes_before": len(homes),
            "homes_after": len(homes_of(profiles_root)),
        }, ensure_ascii=False, indent=2))
        if not args.apply:
            print("\n(dry-run——加 --apply 才搬回。判据：homes_after 回到搬走之前的家数；"
                  "目标已存在的一律 skip，不覆盖别人建回来的东西)", file=sys.stderr)
            return 0
        return 1 if errors else 0

    if args.whole_house:
        # 动整屋之前先确认真实活跃家不在要被搬的那棵树里——否则等于把正在用的家搬走。
        from plobi_constants import get_plobi_home
        active = get_plobi_home().resolve()
        try:
            active.relative_to(profiles_root.resolve())
            print(f"REFUSED — 活跃 PLOBI_HOME 就在 profiles 根里：{active}", file=sys.stderr)
            return 2
        except ValueError:
            pass

        moves, skipped = plan_whole_house(profiles_root, args.date, homes)
        # 体积必须在**搬之前**量：搬完再量等于在问空路径，会得到一排 0（我第一版就是这么错的，
        # 是 `test_whole_house_moves_every_home_and_keeps_the_bytes` 抓出来的）。
        values_before = {h.name: tree_bytes(h) for h in homes}
        done, errors = apply_moves(moves) if args.apply else ([], [])
        homes_after = homes_of(profiles_root)
        print(json.dumps({
            "mode": "apply" if args.apply else "dry-run",
            "batch": "whole-house",
            "profiles_root": str(profiles_root),
            "root_resolved_by": root_source,
            "quarantine_root": str(profiles_root.parent / f"{QUARANTINE_PREFIX}{args.date}" / "profiles"),
            "homes_before": len(homes),
            "homes_after": len(homes_after),
            "values_before": values_before,
            "planned_moves": len(moves),
            "planned_bytes": sum(m["bytes"] for m in moves),
            "already_quarantined": skipped,
            "moved": done,
            "errors": errors,
        }, ensure_ascii=False, indent=2))
        if not args.apply:
            print("\n(dry-run——加 --apply 才搬。判据：homes_after == 0；每个家的字节数在 "
                  "_quarantine-<日期>/profiles/ 下原样可查；真删仍要用户签)", file=sys.stderr)
            return 0
        return 1 if (homes_after or errors) else 0

    before = measure(profiles_root, homes)
    moves, refused, skipped = plan_moves(profiles_root, args.date, homes)
    chain_before = read_chain(homes) if args.apply else None

    if args.apply:
        done, errors = apply_moves(moves)
    else:
        done, errors = [], []

    homes_after = homes_of(profiles_root)
    after = measure(profiles_root, homes_after)
    chain_after = read_chain(homes_after)

    print(json.dumps({
        "mode": "apply" if args.apply else "dry-run",
        "profiles_root": str(profiles_root),
        "root_resolved_by": root_source,
        "allowed_classes": list(ALLOWED),
        "before": before,
        "after": after,
        "planned": {"moves": len(moves), "refused": refused, "already_quarantined": skipped},
        "bytes_planned": sum(m["bytes"] for m in moves),
        "moved": done,
        "errors": errors,
        "read_chain_before": chain_before,
        "read_chain_after": chain_after,
    }, ensure_ascii=False, indent=2))

    if not args.apply:
        print("\n(dry-run——加 --apply 才搬。判据：家数不变、甲组六类在盘点表里归零、"
              "搬完每家的 `models_dev_cache.json` 仍解析到共享那一份、磁盘总量**不变**"
              "（只移不删，下降要等真删那一轮）)", file=sys.stderr)
    residual = sum(c["homes"] for c in after["classes"].values())
    # 非零退出只给两种真事故：甲组还没搬干净（`refused` 那种"壳不空"就在里面），
    # 或者搬完之后某一家又解析回自己家里（那说明读侧不是纸上写的那样）。
    if args.apply and (residual or chain_after["fell_back_into_home"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
