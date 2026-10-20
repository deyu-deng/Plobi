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


# ── 基线必须连"跑的时候树脏不脏"一起记（10-20，`WP-WIN-TEST-REBASELINE`）──────────
# `code_head` 只说哪个提交，说不了跑的那刻有没有别人的未提交代码。这台多线共写一棵树，
# 拿脏树 --write 就等于把别人在飞的东西算进 HEAD 的账——正是裁 174「绿要看在带着什么跑的」那一族。
def _repo(tmp_path: Path) -> Path:
    import subprocess
    root = tmp_path / "codetree"
    (root / "tools").mkdir(parents=True)
    (root / "tools" / "a.py").write_text("A = 1\n", encoding="utf-8")
    for cmd in (["git", "init", "-q", "."], ["git", "config", "user.email", "x@y"],
                ["git", "config", "user.name", "x"], ["git", "add", "-A"],
                ["git", "commit", "-qm", "seed"]):
        subprocess.run(cmd, cwd=root, check=True, capture_output=True)
    return root


def test_worktree_dirty_names_the_in_flight_files(mod, tmp_path, monkeypatch):
    root = _repo(tmp_path)
    monkeypatch.setattr(mod, "REPO_ROOT", str(root))
    (root / "tools" / "a.py").write_text("A = 2\n", encoding="utf-8")
    (root / "tools" / "new.py").write_text("N = 1\n", encoding="utf-8")
    import subprocess
    subprocess.run(["git", "-C", str(root), "add", "tools/new.py"], check=True, capture_output=True)
    got = mod.worktree_dirty()
    names = " ".join(got)
    assert any("tools/a.py" in g for g in got), got      # 改过没提交的
    assert any("new.py" in g for g in got), got          # 暂存过但没提交的
    assert "（M）" in names and "（A）" in names, names    # 带状态码，接手的人才知道是什么形状


def test_clean_tree_yields_empty_list_not_a_crash(mod, tmp_path, monkeypatch):
    import subprocess
    root = _repo(tmp_path)
    monkeypatch.setattr(mod, "REPO_ROOT", str(root))
    assert subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                          capture_output=True, text=True).stdout.strip() == ""
    assert mod.worktree_dirty() == []


def test_write_records_the_dirty_tree_and_says_so(mod, tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path)
    (root / "tools" / "a.py").write_text("A = 2\n", encoding="utf-8")
    monkeypatch.setattr(mod, "REPO_ROOT", str(root))
    monkeypatch.setattr(mod, "BASELINE", str(tmp_path / "baseline.json"))
    monkeypatch.setattr(sys, "argv", ["x", "--from-log", _write(tmp_path, RED_LOG), "--write"])
    assert mod.main() == 0
    out = capsys.readouterr().out
    assert "取自工作树" in out, out
    import json
    payload = json.load(open(tmp_path / "baseline.json", encoding="utf-8"))
    assert any("tools/a.py" in g for g in payload["worktree_dirty_at_write"]), payload


def test_a_baseline_admitting_dirty_tree_cannot_be_read_as_head_clean(
        mod, tmp_path, monkeypatch, capsys):
    import json
    log = _write(tmp_path, RED_LOG)
    got = mod.parse_log(log)   # 键由被测代码自己解析——手写 "tests\tools" 会被 \t 吃掉，假新增红
    base = tmp_path / "baseline.json"
    json.dump({"date": "2026-10-20", "code_head": mod.code_head() or "unknown",
               "files_with_failures": got["files_with_failures"],
               "tests_failed": got["tests_failed"], "machine": "x", "entry": "y",
               "files": got["files"],
               "worktree_dirty_at_write": ["tools/a.py（M）"]},
              open(base, "w", encoding="utf-8"), ensure_ascii=False)
    monkeypatch.setattr(mod, "BASELINE", str(base))
    monkeypatch.setattr(sys, "argv", ["x", "--from-log", log])
    rc = mod.main()
    out = capsys.readouterr().out
    assert rc == 0, out                     # 没有新增红，退出码本来就该是 0
    assert "没有新增红" in out, out
    assert "取自工作树" in out and "不等于 HEAD 干净" in out, out


# ── 起跑那一刻的 HEAD 才是这批红的测量对象（10-08 22:2x 实测踩过，坑记在登记册）────────
def _head(root):
    import subprocess
    return subprocess.run(["git", "-C", str(root), "rev-parse", "--short=8", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def test_write_pins_the_head_the_log_actually_measured(mod, tmp_path, monkeypatch, capsys):
    import json
    import subprocess
    root = _repo(tmp_path)
    start = _head(root)
    (root / "tools" / "b.py").write_text("B = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "second"], cwd=root, check=True, capture_output=True)
    monkeypatch.setattr(mod, "REPO_ROOT", str(root))
    monkeypatch.setattr(mod, "BASELINE", str(tmp_path / "baseline.json"))
    monkeypatch.setattr(sys, "argv", ["x", "--from-log", _write(tmp_path, RED_LOG),
                                      "--write", "--started-at", start])
    assert mod.main() == 0
    out = capsys.readouterr().out
    payload = json.load(open(tmp_path / "baseline.json", encoding="utf-8"))
    assert payload["code_head"] == start, payload          # 测量对象=起跑那刻
    assert payload["run_start_head"] == start, payload     # 字段名沿用 10-08 那份账上已有的两个词
    assert payload["head_at_write"] == _head(root)          # 落盘那刻另记，两个数都看得见
    assert "没被这批日志测过" in out, out                   # 中间那笔不许算成被这份基线覆盖


def test_without_pinning_the_two_heads_are_the_same_number_once(mod, tmp_path, monkeypatch, capsys):
    import json
    root = _repo(tmp_path)
    monkeypatch.setattr(mod, "REPO_ROOT", str(root))
    monkeypatch.setattr(mod, "BASELINE", str(tmp_path / "baseline.json"))
    monkeypatch.setattr(sys, "argv", ["x", "--from-log", _write(tmp_path, RED_LOG), "--write"])
    assert mod.main() == 0
    payload = json.load(open(tmp_path / "baseline.json", encoding="utf-8"))
    assert payload["code_head"] == payload["run_start_head"] == payload["head_at_write"] == _head(root), payload
    assert "没被这批日志测过" not in capsys.readouterr().out
