#!/usr/bin/env python3
"""三表分离的快速自检：连库 → 看三张表各多少行 → 空库时先迁移一次。

**这一支是薄壳**：真正的迁移逻辑在 ``scripts/migrate_vault_db.py``（那一支已经修过
扫描根、分类名与时间戳格式）。旧版本这里把路径写成
``parent.parent / "plobi-desktop" / "Code"``——**大小写与分隔符都不对**
（这台盘上是 ``Plobi-Desktop``），import 必失败；而且它把数据根写成
``home / "vault" / ...``，那个目录不存在 ⇒ 扫到 0 条。
现在两处都改走 ``migrate_vault_db`` 那一支。

用法::

    python scripts/quick_migrate.py            # 空库则迁移，非空只看读数
    python scripts/quick_migrate.py --dry-run  # 只报将要迁多少，不写库
"""

import sys
from pathlib import Path

# Add code directory to path —— `scripts/` 的上一级就是 Code 仓根
code_dir = Path(__file__).parent.parent
sys.path.insert(0, str(code_dir))


def main() -> int:
    print("=" * 60)
    print("Vault 三表分离自检")
    print("=" * 60)

    try:
        from plobi.mind.vault import connect

        print("\n[1] 连接数据库")
        conn = connect()
        print("    ok")

        counts = {}
        for table in ("vault_notes", "agent_dossiers", "user_diary"):
            counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"[2] {table:<16} {counts[table]:>5} 行")

        if not any(counts.values()):
            print("\n[3] 三张表都是空的 ⇒ 跑一次迁移")

            # 真正的迁移在那一支；它的扫描根走 real_target()，分类名照盘上真实的写
            from migrate_vault_db import get_roots, migrate_diary, migrate_dossiers, migrate_notes

            roots = get_roots()
            print(f"    notes    -> {roots['notes']}")
            print(f"    dossiers -> {roots['dossiers']}")
            print(f"    diary    -> {roots['diary']}")

            migrated = (
                migrate_notes(roots)
                + migrate_dossiers(roots)
                + migrate_diary(roots)
            )
            print(f"    共迁 {migrated} 条")

            for table in ("vault_notes", "agent_dossiers", "user_diary"):
                n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                print(f"    {table:<16} {n:>5} 行")
        else:
            print("\n[3] 库里已有数据 ⇒ 不重复迁移（要重来请跑 migrate_vault_db.py --force）")

        conn.close()
        print("\n完成")
        return 0

    except Exception as exc:
        print(f"\n失败：{exc}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
