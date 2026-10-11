"""`scripts/plobi/check_test_baseline.py --from-log` 的行为契约（额度线自己那把尺子）。

盯的是这台每天都被使用的一条判据：**"这一批全绿" 和 "我没解析出日志" 不能长成同一个样子**。
原版在 0 红时打印 "解析不到任何失败文件…别把解析不出来当成全绿" 并返回 1 ——
判据本身没错，但它把一次真全绿也判成 RED，于是"红"这个字在这条尺子上失去了含义。

四条：
  1. 全绿批次 → `files` 空但 `files_seen` > 0，`main()` 报 GREEN 且返回 0；
  2. 真解析不出（没有进度行）→ 仍然 RED，不许被第 1 条放水；
  3. 有红的批次 → 照旧解析出文件与条数（回归保护）；
  4. 拿 0 红的日志 `--write` → **拒绝**，否则会把这台真实存在的红名单抹成空账。

全程只在 `tmp_path` 里造日志，不读不写 `test_baseline_windows.json`。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "plobi" / "check_test_baseline.py"

GREEN_LOG = """\
▶ running per-file parallel test suite via run_tests_parallel.py
Discovered 2 test files (~30 tests) under [...]; running with -j 1
[ 50.0% |    14/~30 | ✓14 | ✗  0] ✓ tests\\tools\\test_a.py (14✓, 2.2s)
[100.0% |    30/~30 | ✓30 | ✗  0] ✓ tests\\tools\\test_b.py (16✓, 1.1s)

=== Summary: 2 files, 30 tests passed, 0 failed (100% complete) in 5.1s (1 workers) ===
"""

RED_LOG = """\
▶ running per-file parallel test suite via run_tests_parallel.py
Discovered 2 test files (~30 tests) under [...]; running with -j 1
[ 50.0% |   14/~30 | ✓13 | ✗ 1] ✗ tests\\tools\\test_a.py (13✓ 1✗, 2.2s)
[100.0% |   30/~30 | ✓27 | ✗  2] ✗ tests\\tools\\test_b.py (14✓ 2✗, 1.1s)

=== Summary: 2 files with test failures (2 tests failed) ===
  tests\\tools\\test_a.py  (1 test failed)
  tests\\tools\\test_b.py  (2 tests failed)
"""

NOISE_LOG = "some other program's output\nnothing to see here\n"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("check_test_baseline", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write(tmp_path: Path, text: str) -> str:
    log = tmp_path / "run.log"
    log.write_text(text, encoding="utf-8")
    return str(log)


def test_green_batch_is_not_confused_with_an_unparsable_log(mod, tmp_path):
    run = mod.parse_log(_write(tmp_path, GREEN_LOG))
    assert run["files"] == {}
    assert run["files_seen"] == 2, "progress lines are what proves the run actually happened"
    # padded columns must still parse, or a real green log reports "None 条全绿"
    assert run["tests_total"] == 30 and run["tests_passed"] == 30, run


def test_green_batch_exits_zero_and_says_green(mod, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["x", "--from-log", _write(tmp_path, GREEN_LOG)])
    assert mod.main() == 0
    out = capsys.readouterr().out
    assert "GREEN" in out and "0 红" in out


def test_a_log_with_no_progress_lines_is_still_red(mod, tmp_path, monkeypatch, capsys):
    """The original warning must survive its own fix — otherwise every garbage log
    gets a free pass as 'green'."""
    monkeypatch.setattr(sys, "argv", ["x", "--from-log", _write(tmp_path, NOISE_LOG)])
    assert mod.main() == 1
    assert "进度行" in capsys.readouterr().out


def test_failures_are_still_counted(mod, tmp_path):
    run = mod.parse_log(_write(tmp_path, RED_LOG))
    assert run["files"] == {"tests\\tools\\test_a.py": 1, "tests\\tools\\test_b.py": 2}
    assert run["tests_failed"] == 3
    assert run["files_seen"] == 2


def test_zero_red_log_cannot_rebaseline(mod, tmp_path, monkeypatch, capsys):
    """`--write` off a small green batch would erase the machine's real red list."""
    monkeypatch.setattr(mod, "BASELINE", str(tmp_path / "baseline.json"))
    monkeypatch.setattr(sys, "argv", ["x", "--from-log", _write(tmp_path, GREEN_LOG), "--write"])
    assert mod.main() == 1
    assert "拒绝" in capsys.readouterr().out
    assert not (tmp_path / "baseline.json").exists()
