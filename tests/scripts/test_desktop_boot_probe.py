"""WP-DESKTOP-BOOT-COST 的纯逻辑守卫。

这里**不测真起后端**——那一发要 2–20 秒、还会往家目录写，属现场验（纸 §4 判据①②的复跑行才是它）。
本格的价值在于「读数必须自带语境」和「阈值怎么判」这两条形状，它们可以离线钉住。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from dataclasses import fields
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "desktop_boot_probe.py"


def _load():
    spec = importlib.util.spec_from_file_location("desktop_boot_probe", str(SCRIPT))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load()


def _boot(**over):
    base = dict(which="source", argv=["plobi", "serve"], git_rev="abcdef1", home="/tmp/x",
                ready_s=3.2, http_s=3.4, talk_s=4.0, uptime_min=340, error="")
    base.update(over)
    return probe.Boot(**base)


def test_missing_context_fails_the_gate_not_the_number():
    """四件语境少任何一件，这条读数就不算数：判 1，别让它被当成"没问题"。

    这台实测过：同一份码同一条命令，开机 30 分钟取到 18.84s、5.5 小时取到 3.25s。
    不带「开机多久」的读数，下一个读的人一定把缓存冷当成回归。
    """
    for missing in ("which", "git_rev", "home"):
        assert probe.exit_code_for(_boot(**{missing: ""}), 30) == 1, missing
    assert probe.exit_code_for(_boot(uptime_min=0), 30) == 1


def test_threshold_is_inclusive_and_error_wins():
    assert probe.exit_code_for(_boot(ready_s=5.0), 5.0) == 0
    assert probe.exit_code_for(_boot(ready_s=5.01), 5.0) == 1
    assert probe.exit_code_for(_boot(ready_s=1.0, error="没等到 ready"), 30) == 1


def test_json_output_shape_carries_the_four_context_fields():
    """`--json` 是给闸门与流水吃的：那四件语境必须在字段里，不在文案里。"""
    names = {f.name for f in fields(probe.Boot)}
    assert {"which", "git_rev", "home", "uptime_min"} <= names


def test_git_rev_reads_a_real_short_sha(tmp_path):
    """git_rev 必须是仓里真取得的那一笔——读数不带语境就等于没带（也防有人凭记忆写号）。"""
    out = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True)
    real = out.stdout.strip()
    assert probe._git_rev(Path(REPO)) == real
    assert probe._git_rev(tmp_path) in ("?", "")


def test_source_argv_points_at_a_file_that_exists():
    """取"仓里那份"的入口走 venv 布局自己探（Windows 是 Scripts\\，POSIX 是 bin/），不手拼字面量。"""
    argv = probe._source_argv()
    assert isinstance(argv, list) and argv
    if not Path(argv[0]).exists():
        pytest.skip("这台没有 Code/.venv，探针的落点探测留给有 venv 的环境")
    assert Path(argv[0]).exists()
