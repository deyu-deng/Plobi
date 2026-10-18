"""反例：`WP-REGISTER-VERIFY-CELLS` 的两支新牙必须真咬（裁定 174 的落地件）。

「闸门会出声」不能是自述——这里造一格缺 `复跑：` 的待签行，跑一遍判据函数，
必须点到它的名字；补上命令后必须不再点它。执行脚本那头同样两头都验：
只读的跑得出读数、写盘的必须拒、真红的那条要回 rc=1（SKIP ≠ 通过，用 rc=2 分开）。
"""
import io
import os
import subprocess
import sys
import time

import pytest

CODE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DESK = os.path.dirname(CODE)
DOCS_SCRIPTS = os.path.join(DESK, "Docs", "scripts")
REGISTER = os.path.join(DESK, "Docs", "reference", "agent-register.md")

sys.path.insert(0, DOCS_SCRIPTS)
verify_register_cells = pytest.importorskip(
    "verify_register_cells",
    reason="Docs/scripts/verify_register_cells.py 不在旁边（只在本仓被单独 checkout 时会发生）")

HEADER = ("| 刀 | 谁 | 纸 | 认领的文件族 | 状态 | 时间 | 备注 |\n"
          "|---|---|---|---|---|---|---|\n")


def cell(wp, status, note):
    return (f"| **{wp}** 反例格 | Govern 线 `d0750608` | 纸 — | `Docs/README.md` | "
            f"{status} | 00:00 | `[{wp}]` — {note} |")


# ① 门的那支牙：缺命令要点名，补上命令要闭嘴
def test_tooth_names_a_pending_cell_without_a_command():
    text = HEADER + cell("WP-FAKE-NOCMD", "待签", "判据是一段叙述，没有命令") + "\n"
    assert verify_register_cells.rows_without_command(text) == ["WP-FAKE-NOCMD"]


def test_tooth_goes_quiet_once_the_command_is_written():
    text = HEADER + cell("WP-FAKE-HASCMD", "待签",
                         "复跑：`python Docs/scripts/check_docs_layout.py` 现取") + "\n"
    assert verify_register_cells.rows_without_command(text) == []


def test_tooth_ignores_other_statuses():
    text = HEADER + cell("WP-FAKE-DONE", "已闭", "这格没有命令也不该出声") + "\n"
    assert verify_register_cells.rows_without_command(text) == []
    assert verify_register_cells.rows_without_command(text, "已闭") == ["WP-FAKE-DONE"]


def test_a_cell_may_carry_several_commands():
    # 一格写几条是常态（本线自己那格就三条）：`复跑：`a` 与 `b`（读数）与 `c``
    text = HEADER + cell("WP-FAKE-MANY", "待签",
                         "复跑：`grep -c A f.txt` 与 **`ls Code/scripts`**（列一下）与 `git -C Code status --short`") + "\n"
    assert verify_register_cells.commands_of(verify_register_cells.rows_from(text)[0]) == [
        "grep -c A f.txt", "ls Code/scripts", "git -C Code status --short"]


def test_the_capture_stops_at_the_cell_separator():
    # 同格后半那句备注里的反引号是个文件名，不是命令——抓去跑就是替别人执行任意指令
    text = HEADER + cell("WP-FAKE-STOP", "待签",
                         "复跑：`grep -c A f.txt` ｜另见 `Docs/README.md` 那一节的口径") + "\n"
    assert verify_register_cells.commands_of(verify_register_cells.rows_from(text)[0]) == [
        "grep -c A f.txt"]


# ②b 「只能人现场看」不是欠账（裁 174），但要写成机器认得的样
def test_manual_marker_counts_as_written_and_yields_no_command():
    text = HEADER + cell("WP-FAKE-MANUAL", "待签",
                         "复跑：人工——重开一次桌面，看左栏那两行还在不在") + "\n"
    row = verify_register_cells.rows_from(text)[0]
    assert verify_register_cells.commands_of(row) == [], "人工那句被当成命令了"
    notes = verify_register_cells.manual_notes(row)
    assert len(notes) == 1 and "左栏" in notes[0], notes
    assert verify_register_cells.rows_without_command(text) == []


def test_manual_text_is_never_executed(tmp_path, capsys):
    hay = tmp_path / "hay.txt"
    hay.write_text("WP-FOUND\n", encoding="utf-8")
    text = HEADER + cell("WP-FAKE-ONLYMANUAL", "待签",
                         f"复跑：人工——白屏那一闪要看一眼，别跑 `grep -c WP-FOUND {hay.as_posix()}`") + "\n"
    reg = tmp_path / "reg.md"
    reg.write_text(text, encoding="utf-8")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", [sys.argv[0], "--register", reg.as_posix()])
        rc = verify_register_cells.main()
    out = capsys.readouterr().out
    assert "要人现场看" in out, out
    assert "rc=0" not in out, f"人工那条被拿去跑了：{out}"
    assert rc == 0, f"只有「要人看」不该把脚本判成没跑成（rc={rc}）——那正是把体验轮的账堆回线上"


def test_a_cell_may_have_both_a_command_and_a_manual_note(tmp_path, capsys):
    hay = tmp_path / "hay.txt"
    hay.write_text("WP-FOUND\n", encoding="utf-8")
    text = HEADER + cell("WP-FAKE-BOTH", "待签",
                         f"复跑：`grep -c WP-FOUND {hay.as_posix()}` 与 复跑：人工——顺手看一眼有没有白屏") + "\n"
    reg = tmp_path / "reg.md"
    reg.write_text(text, encoding="utf-8")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", [sys.argv[0], "--register", reg.as_posix()])
        rc = verify_register_cells.main()
    out = capsys.readouterr().out
    assert "✓ WP-FAKE-BOTH" in out and "要人现场看" in out, out
    assert rc == 0


# ② 白名单：只读的放、写盘/触网/起桌面/内联代码的全拒
@pytest.mark.parametrize("cmd", [
    "grep -c 'WP-' Docs/README.md",
    "git -C Code rev-parse --abbrev-ref HEAD",
    "cd Code && bash scripts/run_tests.sh -j 1 tests/plobi/test_x.py",
    "python Docs/scripts/check_docs_layout.py",
    # 这台的实际写法：venv 里的 python.exe，别因为它不是 `python` 就判成命令头不在白名单
    "cd Code && .venv/Scripts/python.exe scripts/plobi/check_test_baseline.py --from-log D:/Temp/gate-selfcheck-1018/full.log",
    # 列远端是只读的（本线自己的判据就要跑它）；改远端另说
    "git -C D:/Data/AppData/plobi/vault remote -v",
    # 「应该是 0 条」那种探针：包进 test 才是「判据成立⇒rc=0」，裸 grep 出 0 会报假红
    "test $(grep -c VAULT_ROOT Docs/DECISIONS.md) = 0",
    # 前端那一族真写法：tsx 跑单个 .test.ts，路径里有 `electron/` 目录名，不许因此判成「要起桌面」
    "cd Code/apps/desktop && node_modules/.bin/tsx --test apps/desktop/electron/chatlog-keys.test.ts",
    "ls Code/scripts/plobi",
])
def test_readonly_commands_pass_the_whitelist(cmd):
    assert verify_register_cells.classify(cmd) is None


@pytest.mark.parametrize("cmd,frag", [
    ("git -C Code commit -m x", "改 git 索引或历史"),
    ("python Code/scripts/plobi/check_test_baseline.py --write", "--write"),
    ("rm -f Docs/README.md", "改盘上文件"),
    ("npm i vitest", "装包"),
    ("curl -s http://127.0.0.1:8000/v1/models", "触网"),
    ("cd Code && npm run dev", "起桌面"),
    ("python -c \"print(open('Code/plobi/agenda/planning.py').read())\"", "内联代码"),
    ("cd Code && bash scripts/run_tests.sh -j 1", "整族跑"),
    ("echo hi > Docs/README.md", "重定向"),
    ("git -C Code reset --hard", "改 git 索引或历史"),
    ("node -e \"require('node:sqlite')\"", "内联代码"),
    ("cd Code/apps/desktop && npx vitest run", "装包"),
    ("cd Code/apps/desktop && vitest run --environment jsdom", "没指到单个测试文件"),
    ("cd Code && pytest tests/plobi/test_mind_proposal_gate.py", "禁直调 pytest"),
    ("git -C D:/Data/AppData/plobi/vault remote set-url origin https://x/y.git", "改远端"),
    ("git -C D:/Data/AppData/plobi/mind remote -v", None),
])
def test_writing_or_heavy_commands_are_refused_with_a_reason(cmd, frag):
    why = verify_register_cells.classify(cmd)
    if frag is None:                                  # 反向一格：列远端是只读的，别把它当写盘拒掉
        assert why is None, f"只读的这条被拒了：{why}"
        return
    why = verify_register_cells.classify(cmd)
    assert why is not None, f"这条本该拒却放行了：{cmd}"
    assert frag in why, f"拒因读不懂在拒什么：{why}（命令 {cmd}）"


def test_placeholder_command_is_refused():
    # 登记册里真有这种写法：`PATH=<mingw64>\bin:$PATH go build …`，看着像命令，跑不了
    assert "占位" in verify_register_cells.classify(
        "CGO_ENABLED=1 PATH=<mingw64>\\bin:$PATH go build -o D:\\Temp\\x.exe .")


# ③ 执行脚本真的会跑、真红会回 1、缺命令回 2
def test_executor_runs_and_reports_green_and_red(tmp_path, capsys):
    hay = tmp_path / "hay.txt"
    hay.write_text("WP-FOUND\nnothing\n", encoding="utf-8")
    posix = hay.as_posix()
    text = HEADER + cell("WP-FAKE-GREEN", "待签", f"复跑：`grep -c WP-FOUND {posix}`") + "\n" + \
        cell("WP-FAKE-RED", "待签", f"复跑：`grep -c WP-ABSENT {posix}`") + "\n" + \
        cell("WP-FAKE-NOCMD2", "待签", "还是没命令") + "\n"
    reg = tmp_path / "reg.md"
    reg.write_text(text, encoding="utf-8")
    argv = [sys.argv[0], "--register", reg.as_posix()]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", argv)
        rc = verify_register_cells.main()
    out = capsys.readouterr().out
    assert "✓ WP-FAKE-GREEN" in out, out
    assert "✗ WP-FAKE-RED" in out, out
    assert "WP-FAKE-NOCMD2" in out, out
    assert rc == 1, f"有红必须回 1，实际 {rc}"


def test_executor_refuses_to_call_itself_a_pass_when_nothing_ran(tmp_path, capsys):
    hay = tmp_path / "hay.txt"
    hay.write_text("WP-FOUND\n", encoding="utf-8")
    text = HEADER + cell("WP-FAKE-SKIP", "待签",
                         f"复跑：`curl -s http://127.0.0.1:9/v1/models && grep -c WP-FOUND {hay.as_posix()}`") + "\n"
    reg = tmp_path / "reg.md"
    reg.write_text(text, encoding="utf-8")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", [sys.argv[0], "--register", reg.as_posix()])
        rc = verify_register_cells.main()
    assert rc == 2, f"SKIP ≠ 通过，该回 2，实际 {rc}"
    assert "SKIP" in capsys.readouterr().out


# ④ 第三向：工作树里每一档未提交的改动，反过来问登记册有没有一格认领它
def _stall_row(status="待签", sess="deadbeef", claim="Code/plobi_cli/config.py"):
    return (f"| **WP-FAKE-CLAIM** 反例格 | 某线 `{sess}` | 纸 — | `{claim}` | "
            f"{status} | 00:00 | `[WP-FAKE-CLAIM]` — 反例 |")


def _sandbox(tmp_path, files=("plobi_cli/config.py", "orphan.py")):
    """真起一棵一次性 git 仓（不碰真仓），改脏其中几档——`unattributed_work` 读的就是 git status。"""
    base = tmp_path / "desk"
    code = base / "Code"
    (code / "plobi_cli").mkdir(parents=True)
    for f in files:
        (code / f).parent.mkdir(parents=True, exist_ok=True)
        (code / f).write_text("A = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "."], cwd=code, check=True)
    subprocess.run(["git", "config", "user.email", "x@y"], cwd=code, check=True)
    subprocess.run(["git", "config", "user.name", "x"], cwd=code, check=True)
    subprocess.run(["git", "add", "--", *files], cwd=code, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=code, check=True)
    for f in files:
        (code / f).write_text("A = 2\n", encoding="utf-8")     # 全部改脏
    return base


def test_unclaimed_dirty_file_is_named_orphan(tmp_path):
    base = _sandbox(tmp_path)
    reg = HEADER + _stall_row(claim="Code/plobi_cli/config.py") + "\n"
    orph, stall, why = verify_register_cells.unattributed_work(
        reg, str(base).replace(os.sep, "/"), live={})
    assert why == "", why
    assert any("orphan.py" in o for o in orph), f"没人认领的那档没被点名：{orph}"
    assert not any("config.py" in o for o in orph), f"有人认领的档被误报成无主：{orph}"


def test_dirty_file_claimed_only_by_a_dead_session_is_stalled(tmp_path):
    base = _sandbox(tmp_path, files=("plobi_cli/config.py",))
    reg = HEADER + _stall_row(sess="deadbeef") + "\n"
    _orph, stall, why = verify_register_cells.unattributed_work(reg, str(base), live={})
    assert stall and "config.py" in stall[0] and "deadbeef" in stall[0], stall


def test_same_cell_is_quiet_when_the_session_is_still_alive(tmp_path):
    # 同一条脏档，会话还在库里 ⇒ 不该报——它判的是「没人认领」，不是「工作树脏」
    base = _sandbox(tmp_path, files=("plobi_cli/config.py",))
    reg = HEADER + _stall_row(sess="f00dcafe") + "\n"
    _orph, stall, _why = verify_register_cells.unattributed_work(
        reg, str(base), live={"f00dcafe-1111": time.time()})
    assert stall == [], stall


def test_claim_matching_is_on_path_boundaries():
    assert verify_register_cells._covers("Code/plobi/agenda", "Code/plobi/agenda/planning.py")
    assert verify_register_cells._covers("Code/plobi/quota/**", "Code/plobi/quota/route.py")
    assert not verify_register_cells._covers("Code/plobi/agendaX", "Code/plobi/agenda/planning.py")
    assert not verify_register_cells._covers("Code/scripts", "Code/scripts_extra/boot.py")


def test_no_session_db_says_so_instead_of_passing():
    # SKIP ≠ 通过：库读不到时必须出声，不能让这条门静默变成「都活着」
    live, why = verify_register_cells.live_sessions(paths=())
    assert live is None and why, (live, why)


# ⑤ 门与执行脚本读的是同一份口径：现算的数必须一致，不许各数各的
def test_gate_and_executor_count_the_same_real_cells():
    missing = verify_register_cells.rows_without_command(
        io.open(REGISTER, encoding="utf-8", errors="replace").read())
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable, os.path.join(DOCS_SCRIPTS, "check_docs_layout.py")],
                       capture_output=True, text=True, errors="replace",
                       cwd=DESK, env=env, timeout=300)
    line = [l for l in (p.stderr or "").splitlines() if "待签」行既没写" in l]
    assert p.returncode == 0, f"门自己红了：{p.stdout[-400:]} {p.stderr[-400:]}"
    assert len(line) == 1, f"门没出声或出了两遍声：{line}"
    assert f"{len(missing)} 个" in line[0], f"门的数 {line[0][:20]} ≠ 现算 {len(missing)} 格"


if __name__ == "__main__":
    sys.exit(subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"]).returncode)
