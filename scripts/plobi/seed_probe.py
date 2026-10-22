"""现场探针：新建一家之后，那家里到底有什么？（``WP-PROFILE-STOP-SEEDING`` 的判据复跑件）

这档存在的理由是一条**判据分叉**，纸面上看不出来、只有跑一遍才分得开：

* **创建侧**：`plobi_cli.profiles.create_profile()` 现在只落 `sessions`/`logs`
  （`_PROFILE_DIRS` 已由 `913d149f` 收成两项，`_CLONE_CONFIG_FILES` 已由同刀清空）——这一侧是做到了的。
* **运行侧**：可一个会跑的 Agent 必然要 `load_config()`，而 `plobi_cli/config.py` 的
  `ensure_plobi_home()` 每轮都跑，会当场把九个目录**和一份 `SOUL.md`** 补回来。

所以 ADR-0013 的 DoD ②（「新建一家，那份家里没有 `config.yaml`／`SOUL.md`／`skills/`…」）
**按现场读数不成立**，成立的只有创建侧。签这一格的人必须看见这两个数并排摆着，
所以本探针一趟里跑两拍，各自印一次目录清单。

落点一律走产品自己的解析器（ADR-0013 硬约束第 3 条）。临时 root 用 `tempfile.gettempdir()`
（裁定 79：这台别写死 POSIX 路径），跑完 `finally` 删干净 ⇒ 不留 C 盘残渣；要留现场就加 `--keep`。

    ./.venv/Scripts/python.exe scripts/plobi/seed_probe.py

只读探针之外的唯一写动作是**在临时目录里**建家，绝不碰真实 `PLOBI_HOME`。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

NAME = "probe-xyz"


def tree(home: Path) -> dict[str, object]:
    """目录清单：目录只记名字，文件记字节数。家不存在时明写，不抛栈。"""
    if not home.is_dir():
        return {"(不存在)": True}
    out: dict[str, object] = {}
    for p in sorted(home.rglob("*")):
        # 目录名带一个尾斜杠，文件不带——不然 `SOUL.md` 会被印成 `SOUL.md/`，
        # 而下面第 [5] 行那种类名比对就会把"人设被补回来了"这件事看漏。
        rel = p.relative_to(home).as_posix()
        if p.is_dir():
            out[rel + "/"] = "dir"
        else:
            out[rel] = p.stat().st_size
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="新建一家之后有什么：创建侧与运行侧两个读数")
    ap.add_argument("--keep", action="store_true", help="跑完不删临时 root（要留现场给人看时用）")
    args = ap.parse_args(argv)

    root = Path(tempfile.gettempdir()) / "plobi-seed-probe"
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    # 家必须住在 <root>/profiles/<name> 底下——`_get_profiles_root()` 与
    # `ensure_plobi_home()` 里那道 "parent.name == 'profiles'" 守卫都认这个形状。
    (root / "profiles").mkdir(parents=True)
    home = root / "profiles" / NAME
    os.environ["PLOBI_HOME"] = str(home)
    exit_code = 0
    try:
        from plobi_cli.profiles import create_profile

        created = create_profile(NAME)
        after_create = tree(created)
        print(f"[1] create_profile({NAME!r}) 之后 = {after_create}")

        # 第二拍才 import config：`plobi_cli.config` 在 import 期会读环境，先把
        # PLOBI_HOME 指到这个新家再拿解析器，问出来的才是"这一家"的落点。
        from plobi_cli.config import load_config

        load_config()
        after_load = tree(home)
        print(f"[2] 同一趟 load_config() 之后 = {after_load}")

        rebuilt = sorted(set(after_load) - set(after_create))
        print(f"[3] 被补回来的 = {rebuilt}")
        print(f"[4] 创建侧判据（不该出现的几样）= "
              f"{[k for k in ('SOUL.md', 'skills/', 'memories/', 'config.yaml', '.env') if k in after_create]}")
        print(f"[5] 运行侧判据（同几样在不在）= "
              f"{[k for k in ('SOUL.md', 'skills/', 'memories/', 'config.yaml', '.env') if k in after_load]}")
        if "memories/" in after_load:
            print("⚠️ 第二台播种机那腿（`memories`）又回来了 —— `84f48f3e` 那条判据红")
            exit_code = 1
    finally:
        if args.keep:
            print(f"(--keep：临时 root 留在 {root})", file=sys.stderr)
        else:
            shutil.rmtree(root, ignore_errors=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
