"""WP-PROFILE-LAZY-HOME-OFF —— `-p` 懒建退役：家不在就折回共享根，身份取名册那一行。

用户 10-25 改判：「**不保留了。但是kanban的能力要有**」。

三条建家口里此刻只剩 `-p` 这条还开着（`spawn` 早已不建 · `plobi profile create` 已关成
rc 2 · `POST /api/profiles` 已 410），本刀把它关上：**家目录不再被任何入口建出来**。
`-p` 仍是进程落点，但家不在时落到**共享根**，身份改由**名册那一行**带着
（裁定 180 / 192：身份是名册行，不是盘上一个目录）。

判据六条：
  ① `-p` 指向一个家不存在的名字 ⇒ **盘上不长那个家**（一个字节都不建）；
  ② 折回的是**共享根** `<root>`，不是 `<root>/profiles/<name>`；
  ③ **身份**跟着 `-p` 走：名册那一行的 `name` 进 `PLOBI_AGENT_ID`（**不是** profile 名——
     本机 24 行名册里有 11 行 `name ≠ profile`，如 `name: agenda` / `profile: l2-agenda`）；
  ④ **kanban 的腿不丢**：worker 子进程 argv 仍带 `-p <assignee>`、`PLOBI_PROFILE` 仍在；
  ⑤ 老装机逐字不变：家**在**时仍用那一家；
  ⑥ 显式 `-p` 而名册里没有 ⇒ **报错退出**；粘性的 `active_profile` ⇒ 只出一行 note、继续跑。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from plobi.agents.registry import AgentEntry, AgentRegistry

CODE_ROOT = Path(__file__).resolve().parents[2]

# 导入 `plobi_cli.main` 就等于应用 `-p` 覆盖（它在模块层跑 `_apply_profile_override()`），
# 所以探针只要导入后再把两个环境变量打出来。
_PROBE = (
    "import os\n"
    "import plobi_cli.main  # noqa: F401  —— 导入即应用 -p 覆盖\n"
    "print('HOME=' + str(os.environ.get('PLOBI_HOME')))\n"
    "print('AGENT=' + str(os.environ.get('PLOBI_AGENT_ID')))\n"
)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """隔离 PLOBI_HOME + Path.home()（与 test_registry_no_home 同一套口径）。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path / ".plobi"))
    monkeypatch.delenv("PLOBI_MODELS_CONFIG", raising=False)
    monkeypatch.delenv("PLOBI_PROJECTS_CONFIG", raising=False)
    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)
    return tmp_path


def _root(home) -> Path:
    return Path(home) / ".plobi"


def _seed_roster(root: Path, name: str, profile: str) -> None:
    """名册写共享层那一份（`default_path()` = `<root>/plobi/projects.yaml`）。"""
    reg = AgentRegistry(root / "plobi" / "projects.yaml")
    reg.upsert(AgentEntry(name=name, role="l2_agenda"))
    reg.save()


def _run_cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    """起一个真进程跑 `plobi -p …` 的入口那一段，回读它落到哪、身份是谁。"""
    env = dict(os.environ)
    env["PLOBI_HOME"] = str(root)
    env["PYTHONPATH"] = str(CODE_ROOT)
    for stale in (
        "PLOBI_AGENT_ID",
        "PLOBI_PROFILE",
        "PLOBI_PROJECTS_CONFIG",
        "PLOBI_MODELS_CONFIG",
    ):
        env.pop(stale, None)
    return subprocess.run(
        [sys.executable, "-c", _PROBE, *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(CODE_ROOT),
        timeout=180,
    )


def _probe_values(out: str) -> tuple[str, str]:
    home = agent = ""
    for line in out.splitlines():
        if line.startswith("HOME="):
            home = line[len("HOME=") :].strip()
        elif line.startswith("AGENT="):
            agent = line[len("AGENT=") :].strip()
    return home, agent


# ── ① 盘上不长那个家 ────────────────────────────────────────────────────────


def test_dash_p_does_not_create_the_home(tmp_path):
    """① 家不存在的名字 ⇒ 盘上**不长**那个家（一个字节都不建）。"""
    root = tmp_path / ".plobi"
    root.mkdir()
    _seed_roster(root, "agenda", "l2-agenda")

    proc = _run_cli(root, "-p", "l2-agenda")

    assert proc.returncode == 0, proc.stderr
    assert not (root / "profiles" / "l2-agenda").exists(), "又建了一个分身家"
    assert not (root / "profiles").exists() or not any((root / "profiles").iterdir())


def test_dash_p_folds_to_the_shared_root(tmp_path):
    """② 折回的是**共享根**，不是 `<root>/profiles/<name>`。"""
    root = tmp_path / ".plobi"
    root.mkdir()
    _seed_roster(root, "agenda", "l2-agenda")

    proc = _run_cli(root, "-p", "l2-agenda")

    home, _agent = _probe_values(proc.stdout)
    assert home == str(root), f"折回的不是共享根：{home}"
    assert "profiles" not in home


# ── ③ 身份跟着 `-p` 走，且是名册的 name ─────────────────────────────────────


def test_identity_is_the_roster_name_not_the_profile_name(tmp_path):
    """③ `PLOBI_AGENT_ID` = 名册那一行的 **name**（不是 profile 名）。"""
    root = tmp_path / ".plobi"
    root.mkdir()
    _seed_roster(root, "agenda", "l2-agenda")

    proc = _run_cli(root, "-p", "l2-agenda")

    _home, agent = _probe_values(proc.stdout)
    assert agent == "agenda", f"身份不是名册 name：{agent!r}"
    assert agent != "l2-agenda", "身份落成了 profile 名——共享层的人设文件按 name 命名"


def test_agent_id_lookup_returns_the_roster_name(home):
    """③ 的读侧那一半：`-p` 给的是落点名，身份要的是名册 name。"""
    from plobi_cli.main import _agent_id_for_profile

    _seed_roster(_root(home), "agenda", "l2-agenda")

    assert _agent_id_for_profile("l2-agenda") == "agenda"
    assert _agent_id_for_profile("agenda") == "agenda"  # 名册 name 也认
    assert _agent_id_for_profile("查无此行") == ""


# ── ④ kanban 的腿不丢 ───────────────────────────────────────────────────────


def test_kanban_worker_still_spawns_with_dash_p(tmp_path, monkeypatch):
    """④ kanban worker 的 argv 仍带 `-p <assignee>`、`PLOBI_PROFILE` 仍在。"""
    from plobi_cli import kanban_db as kb

    home = tmp_path / ".plobi"
    home.mkdir()
    monkeypatch.setenv("PLOBI_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()

    captured: dict = {}

    class FakeProc:
        pid = 99999

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env", {})
        return FakeProc()

    monkeypatch.setattr("subprocess.Popen", fake_popen)

    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="lazy home off", assignee="l2-agenda")
        task = kb.get_task(conn, tid)
        workspace = kb.resolve_workspace(task)
        kb._default_spawn(task, str(workspace))
    finally:
        conn.close()

    cmd = captured["cmd"]
    assert "-p" in cmd, f"kanban worker 不再带 -p：{cmd}"
    assert cmd[cmd.index("-p") + 1] == "l2-agenda"
    assert captured["env"].get("PLOBI_PROFILE") == "l2-agenda"


# ── ⑤ 老装机逐字不变 ────────────────────────────────────────────────────────


def test_an_existing_home_is_used_verbatim(home):
    """⑤ 家**在**时仍用那一家（不折回共享根）。"""
    from plobi_cli.main import _fold_to_shared_root

    profiles_root = _root(home) / "profiles"
    (profiles_root / "l2-agenda").mkdir(parents=True)

    folded = _fold_to_shared_root("l2-agenda")

    assert folded is not None
    assert Path(folded) == profiles_root / "l2-agenda"


def test_an_illegal_name_still_reports_failure_instead_of_raising(home):
    """⑤b 名称非法 ⇒ 返 None，让上层照旧报错（不静默折到别处）。"""
    from plobi_cli.main import _fold_to_shared_root

    assert _fold_to_shared_root("bad name!!") is None


# ── ⑥ 显式 `-p` 与粘性 `active_profile` 走两条不同的路 ──────────────────────


def test_explicit_dash_p_without_a_roster_row_refuses(tmp_path):
    """⑥ 显式 `-p` 而名册里没有这一行 ⇒ 报错退出（不许以 L1 的身份替人干活）。"""
    root = tmp_path / ".plobi"
    root.mkdir()

    proc = _run_cli(root, "-p", "查无此行")

    assert proc.returncode != 0, "显式 -p 指向不存在的 Agent 时不许悄悄跑起来"
    assert "roster" in proc.stderr.lower(), proc.stderr
    assert not (root / "profiles").exists() or not any((root / "profiles").iterdir())


def test_a_stale_active_profile_keeps_running_without_identity(tmp_path):
    """⑥b 粘性的 `active_profile`（不是显式 `-p`）⇒ 只出一行 note，继续跑。"""
    root = tmp_path / ".plobi"
    root.mkdir()
    (root / "active_profile").write_text("ghost\n", encoding="utf-8")

    proc = _run_cli(root)

    assert proc.returncode == 0, proc.stderr
    assert "not an Agent in the roster" in proc.stderr
    home, agent = _probe_values(proc.stdout)
    assert home == str(root)
    assert agent in ("", "None")
