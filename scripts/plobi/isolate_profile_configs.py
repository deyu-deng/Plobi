"""把分身家各自那份 ``config.yaml`` 隔离掉，并交出**能区分真假**的前后读数
（``WP-AGENT-CONFIG-SINGLE-SOURCE`` 第二半；ADR-0013 拆除表 ``config.yaml`` 那一行）。

读链已经改成「名册行覆盖 → 顶层 → 出厂默认」（``plobi_constants.config_path_for_home``
+ ``plobi_cli.config.agent_config_overlay``），分身家那份复印件从此**没人读**。留着它的坏处
不是占地方，是它随时能被某个还手拼路径的旧代码当成真源、也能让下一个人以为改它就改了那个
分身。所以移走它——**只移不删**（ADR-0013 硬约束第 2 条：真删要带回落读数由用户当场签）。

    # 先看要动什么（不写盘）
    python scripts/plobi/isolate_profile_configs.py
    # 再动手
    python scripts/plobi/isolate_profile_configs.py --apply

每家的 ``config.yaml`` 移进 ``该家/_quarantine-<日期>/config.yaml``；顶层那一份在写
``skills.external_dirs`` 之前先备份到 ``<root>/_quarantine-<日期>/config.yaml``。

**读数口径**（三条都照 ADR-0013 硬约束第 3 条：用被测代码自己的解析函数，不手拼字面路径）：

* 生效值 = 为每个家**新起一个进程**跑 ``get_config_path()`` / ``load_config()``。不这么做
  的话同一进程里的 ``load_config`` 缓存会让"改前/改后"两个读数长得像同一个数——10-17 那条
  假读数就是这么造出来的（我自己的两次都算）。
* 份数与总体积 = ``profiles`` 目录数（判据：**不变**）+ 其下文件总体积（隔离轮次只移不删，
  所以这一格**不会下降**，下降要等真删那一轮）+ 家下可读 ``config.yaml`` 份数（24 → 0）。
* 每家 ``skills/``、``terminal.cwd``、``model`` 三个字段逐家列出来，缺哪个、报错哪个都照实写。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

READER = r"""
import json, os, sys
sys.path.insert(0, {repo!r})
from plobi_cli.config import get_config_path, load_config
home = os.environ["PLOBI_HOME"]
try:
    cfg = load_config()
    out = {{
        "resolved": str(get_config_path()),
        "model": cfg.get("model"),
        "cwd": (cfg.get("terminal") or {{}}).get("cwd"),
        "external_skills": (cfg.get("skills") or {{}}).get("external_dirs"),
        "platform_toolsets": cfg.get("platform_toolsets"),
    }}
except Exception as exc:
    out = {{"error": type(exc).__name__ + ": " + str(exc)}}
print(":::" + json.dumps(out, ensure_ascii=False))
"""


def _run_reader(home: Path) -> dict:
    """为这个家新起一个进程，问它自己解析到什么。"""
    env = dict(os.environ, PLOBI_HOME=str(home))
    code = READER.format(repo=str(REPO))
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120
    )
    for line in proc.stdout.splitlines():
        if line.startswith(":::"):
            return json.loads(line[3:])
    return {"error": f"no reading (exit {proc.returncode}): {proc.stderr.strip()[:200]}"}


def _measure(root: Path) -> dict:
    profiles = root / "profiles"
    homes = sorted(p for p in profiles.iterdir() if p.is_dir()) if profiles.is_dir() else []
    total_bytes = sum(
        f.stat().st_size for f in profiles.rglob("*") if f.is_file()
    ) if profiles.is_dir() else 0
    copies = [p / "config.yaml" for p in homes if (p / "config.yaml").is_file()]
    # 隔离区里那些"移走但没删"的字节——判据里「体积下降」只能在真删那一轮发生（ADR-0013
    # 硬约束第 2 条：真删要带回落读数由用户当场签），所以这里把它单列出来，别混成"没降"。
    quarantined = [
        f for p in homes for f in p.glob(f"_quarantine-*/config.yaml") if f.is_file()
    ]
    return {
        "profile_homes": len(homes),
        "profiles_total_mb": round(total_bytes / (1024 * 1024), 2),
        "readable_config_copies": len(copies),
        "copy_bytes": sum(c.stat().st_size for c in copies),
        "quarantined_copies": len(quarantined),
        "quarantined_bytes": sum(q.stat().st_size for q in quarantined),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="真的移（默认只报要动什么）")
    ap.add_argument(
        "--date", default=date.today().isoformat().replace("-", ""),
        help="隔离目录后缀，默认 _quarantine-<YYYYMMDD>",
    )
    args = ap.parse_args(argv)

    from plobi_constants import get_plobi_home, get_shared_plobi_root

    root = get_shared_plobi_root()
    if get_plobi_home() != root:
        print(json.dumps({"error": "跑这一句时不要让 PLOBI_PROFILE/--profile 指进分身家"},
                         ensure_ascii=False))
        return 2
    top = root / "config.yaml"
    if not top.is_file():
        # 顶层那一份不存在就隔离 = 所有 Agent 一起回到出厂默认。这一条不许"照办"。
        print(json.dumps({"error": f"{top} 不存在：先别隔离，顶层那份必须先落地"},
                         ensure_ascii=False))
        return 2

    profiles = root / "profiles"
    homes = sorted(p for p in profiles.iterdir() if p.is_dir()) if profiles.is_dir() else []

    before = _measure(root)
    readings_before = {p.name: _run_reader(p) for p in homes}

    moves, skipped, backups = [], [], []
    if args.apply:
        # 1) 先把共享技能根绑到那唯一一份上（复印件一走，技能不能跟着瞎）。
        from plobi.agents.registry import apply_shared_skills_root

        backup_dir = root / f"_quarantine-{args.date}"
        if not (backup_dir / "config.yaml").exists():
            backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(top, backup_dir / "config.yaml")
            backups.append(str(backup_dir / "config.yaml"))
        changed = apply_shared_skills_root()
        # 2) 逐家移：只移 config.yaml，别的一个字节不碰；目标已存在就跳过（幂等）。
        for home in homes:
            src = home / "config.yaml"
            if not src.is_file():
                continue
            dst_dir = home / f"_quarantine-{args.date}"
            dst = dst_dir / "config.yaml"
            if dst.exists():
                skipped.append(str(src))
                continue
            dst_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            moves.append({"from": str(src), "to": str(dst)})

    after = _measure(root)
    readings_after = {p.name: _run_reader(p) for p in homes}
    residuals = [
        str(p / "config.yaml")
        for p in homes
        if (p / "config.yaml").is_file()
    ]
    errors = {
        name: r["error"] for name, r in readings_after.items() if "error" in r
    }

    print(json.dumps({
        "mode": "apply" if args.apply else "dry-run",
        "shared_root": str(root),
        "top_level_backup_written": backups,
        "skills_root_binding_changed": bool(args.apply and changed) if args.apply else None,
        "before": before,
        "after": after,
        "moved": moves,
        "skipped_already_quarantined": skipped,
        "residual_config_yaml_in_profiles": residuals,
        "agents_erroring_after": errors,
        # 逐家前后对照：生效值应当**一字不变**（读侧早已不认自家那份），变的只是那份文件在哪。
        "per_home_effective": {
            name: {"before": readings_before[name], "after": readings_after[name]}
            for name in readings_before
        },
    }, ensure_ascii=False, indent=2))
    if not args.apply:
        print("\n(dry-run——加 --apply 才移；判据：份数不变、家下可读 config.yaml 归零)",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
