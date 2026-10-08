"""轮转与留痕：每天排程跑一次之后，这两个问题变成真的（`WP-MIND-BUNDLES` 排程那格，用户 10-08 拍甲）。

一天约 300 MB（脑 208.9 · Code 76.4 · Docs 14.5 · 鸿蒙 0.1），没有轮转的常驻排程就是在造堆——
那正是本仓刚清过的盘根 residue 那一族。四条：

  1. `--keep N` 只剪**本脚本自己命名**的 bundle（时间戳在名字里，字典序＝时间序）；
  2. 别人家的东西一个都不许碰：`keep/data/*.tar.gz.enc`（加密归档）、`docs-*.bundle`（offdisk 那条路）、
     `vaelis-*.bundle`（09-27 报废那台的旧份）——它们都躺在同一个 `keep/` 里；
  3. **今天没备成的那个仓，不许剪掉它的历史**：失败≠新的一份产生了，剪了就是把仅存的副本删了；
  4. 每轮留一行 `backup-run.log`：计划任务的 stdout 没人看，**跑没跑过、哪个仓 FAIL，必须盘上有据**。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "backup_repos.py"


def _load():
    """按路径加载脚本：`scripts/` 不是包，也不在 sys.path 里，不能直接 import。"""
    spec = importlib.util.spec_from_file_location("backup_repos", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rotate = _load().rotate


def _mk(dest: Path, name: str) -> Path:
    p = dest / name
    p.write_text("x", encoding="utf-8")
    return p


def test_keep_prunes_only_our_own_naming(tmp_path: Path) -> None:
    dest = tmp_path / "keep"
    dest.mkdir()
    for stamp in ("20261001-1200", "20261002-1200", "20261003-1200", "20261004-1200"):
        _mk(dest, f"plobi-mind-{stamp}.bundle")
    _mk(dest, "plobi-code-20261001-1200.bundle")

    removed = rotate(dest, "mind", 2)

    assert removed == ["plobi-mind-20261001-1200.bundle", "plobi-mind-20261002-1200.bundle"]
    assert sorted(p.name for p in dest.glob("plobi-mind-*.bundle")) == [
        "plobi-mind-20261003-1200.bundle",
        "plobi-mind-20261004-1200.bundle",
    ]
    # 别的仓不在本仓的命名族里，不许被顺手动到
    assert (dest / "plobi-code-20261001-1200.bundle").is_file()


def test_foreign_artifacts_in_the_same_directory_survive(tmp_path: Path) -> None:
    dest = tmp_path / "keep"
    (dest / "data").mkdir(parents=True)
    ours = [_mk(dest, f"plobi-docs-2026100{i}-1200.bundle") for i in (1, 2, 3)]
    foreign = [
        _mk(dest / "data", "plobi-data-20260928-0910.tar.gz.enc"),
        _mk(dest, "docs-20260928-2305-R42.bundle"),
        _mk(dest, "vaelis-20260922-1200.bundle"),
    ]

    rotate(dest, "docs", 1)

    assert len(list(dest.glob("plobi-docs-*.bundle"))) == 1
    for f in foreign:
        assert f.is_file(), f"{f.name} 被轮转误删了"


def test_keep_zero_disables_pruning(tmp_path: Path) -> None:
    dest = tmp_path / "keep"
    dest.mkdir()
    for i in range(4):
        _mk(dest, f"plobi-code-2026100{i}-1200.bundle")
    assert rotate(dest, "code", 0) == []
    assert len(list(dest.glob("plobi-code-*.bundle"))) == 4


def test_scheduled_run_leaves_a_durable_record(tmp_path: Path) -> None:
    """计划任务的输出没人看 ⇒ 跑过与跑坏都得写在盘上。"""
    dest = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--dest", str(dest), "--only", "code", "--keep", "1"],
        capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode == 0, proc.stderr

    log = (dest / "backup-run.log").read_text(encoding="utf-8").strip().splitlines()
    assert log, "没有 backup-run.log：这轮排程跑没跑过无从证明"
    assert "code=ok" in log[-1], log[-1]
