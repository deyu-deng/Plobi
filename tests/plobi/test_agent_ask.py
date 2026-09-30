"""裁定 50.4（方案甲）— ``plobi_agent_ask``：总秘书当面问一个项目分身。

Covers the acceptance lines the ruling cares about:

* Target resolution goes through the registry — an unknown name is refused
  *with the names that are actually askable*, an archived row is refused, and
  a non-project row is refused.
* The child's environment is the registry entry's, not the caller's:
  ``PLOBI_HOME`` / ``PLOBI_PROFILE`` / ``TERMINAL_CWD`` all come from the row.
* ``--yolo`` is never in the argv, whatever the registry row says.
* The wait is bounded, and a config value can't push it past the ceiling.
* Timeout, non-zero exit and a missing binary come back as structured tool
  errors — never an exception out of the secretary's turn — and a wedged child
  is killed rather than left running.
* E2E: a stub ``PLOBI_BIN`` proves the argv + env really arrive in a child
  process, and that the answer comes back out of *the child's own session
  record* rather than whatever it happened to print.

The tool is registered by the plugin and must never join
``_PLOBI_CORE_TOOLS`` — asserted here so the prompt-cache footprint can't
regress silently.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import time
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO / "plugins" / "plobi-north-star"


def _load_plugin_package():
    """Load ``plugins/plobi-north-star`` as a package under a synthetic parent.

    The directory name is hyphenated, so it can't be imported by dotted name.
    ``PluginManager._load_directory_module`` does this same dance at runtime
    (``submodule_search_locations`` + an explicit ``__package__``), which is
    what lets ``agent_ask``'s ``from . import master_tools`` resolve — so the
    test exercises the plugin's real service gate instead of a stand-in.
    """
    module_name = "plobi_test_ns_agent_ask_pkg"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(
        module_name,
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    module.__package__ = module_name
    module.__path__ = [str(PLUGIN_DIR)]
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


NS = _load_plugin_package()
AA = NS.AA

LIVE_PROJECT = "resonote"
_OTHER_PROJECT = "framelet"


# ---------------------------------------------------------------------------
# Roster fixture — a temp home with two live 分身 and their neighbours
# ---------------------------------------------------------------------------


@pytest.fixture()
def roster(tmp_path, monkeypatch):
    """``projects.yaml`` + profile dirs + a project folder, all under tmp_path."""
    # Profile roots are HOME-anchored on purpose (AGENTS.md §Profiles rule 6).
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("PLOBI_PROJECTS_CONFIG", raising=False)
    monkeypatch.delenv("PLOBI_KANBAN_TASK", raising=False)
    monkeypatch.delenv("PLOBI_TUI", raising=False)

    plobi_home = tmp_path / ".plobi"
    (plobi_home / "plobi").mkdir(parents=True)
    monkeypatch.setenv("PLOBI_HOME", str(plobi_home))

    project = tmp_path / "work" / "Resonote"
    project.mkdir(parents=True)

    from plobi.agents.registry import AgentEntry, AgentRegistry

    registry_path = plobi_home / "plobi" / "projects.yaml"
    reg = AgentRegistry.load(registry_path)
    reg.agents = {
        LIVE_PROJECT: AgentEntry(
            name=LIVE_PROJECT,
            display_name="Resonote",
            role="l2_project",
            category="projects",
            project_path=str(project),
        ),
        _OTHER_PROJECT: AgentEntry(
            name=_OTHER_PROJECT,
            role="l2_project",
            category="projects",
            project_path=str(project),
        ),
        # 裁定 19: archived rows stay on disk and hide from the default list.
        "gone": AgentEntry(
            name="gone",
            role="l2_project",
            category="butler",
            archived=True,
            archived_at="2026-09-01",
        ),
        # The agenda 分身 already has its own mouth — it is not a project 分身.
        "agenda": AgentEntry(name="agenda", role="l2_agenda", profile="l2-agenda"),
    }
    reg.save()

    for profile in (LIVE_PROJECT, _OTHER_PROJECT):
        (plobi_home / "profiles" / profile).mkdir(parents=True, exist_ok=True)

    return types.SimpleNamespace(
        plobi_home=plobi_home,
        project=project,
        profile_dir=plobi_home / "profiles" / LIVE_PROJECT,
        registry=AgentRegistry.load(registry_path),
    )


@pytest.fixture()
def master_mode(monkeypatch):
    """Simulate the L1 profile that opted into ``plobi_north_star``."""
    monkeypatch.setattr(
        "plobi_cli.config.load_config",
        lambda: {"toolsets": ["plobi_north_star"]},
    )


def _error_payload(raw: str) -> dict:
    payload = json.loads(raw)
    assert "error" in payload, f"expected a structured tool error, got {payload!r}"
    return payload


# ---------------------------------------------------------------------------
# Target resolution — through the registry, never a made-up profile name
# ---------------------------------------------------------------------------


def test_live_roster_is_the_askable_set(roster):
    entries = AA.askable_entries(roster.registry)
    assert {entry.name for entry in entries} == {LIVE_PROJECT, _OTHER_PROJECT}


def test_unknown_name_is_refused_listing_the_askable_names(roster):
    with pytest.raises(AA.AskRefusal) as excinfo:
        AA.resolve_target("resonote-2", registry=roster.registry)
    refusal = excinfo.value
    assert sorted(refusal.available) == sorted([LIVE_PROJECT, _OTHER_PROJECT])
    # The message must be actionable prose, not a bare identifier.
    assert "resonote-2" in refusal.message
    assert LIVE_PROJECT in refusal.message and _OTHER_PROJECT in refusal.message


def test_handler_refuses_an_unknown_name_with_the_real_names(roster):
    payload = _error_payload(
        AA.handle_agent_ask({"agent": "nope", "question": "现在怎么样"})
    )
    assert payload["ok"] is False
    assert sorted(payload["available"]) == sorted([LIVE_PROJECT, _OTHER_PROJECT])


def test_archived_entry_is_refused(roster):
    with pytest.raises(AA.AskRefusal) as excinfo:
        AA.resolve_target("gone", registry=roster.registry)
    assert "归档" in excinfo.value.message
    assert LIVE_PROJECT in excinfo.value.message


def test_non_project_entry_is_refused(roster):
    with pytest.raises(AA.AskRefusal) as excinfo:
        AA.resolve_target("agenda", registry=roster.registry)
    assert "项目分身" in excinfo.value.message


def test_blank_name_is_refused_not_crashed(roster):
    with pytest.raises(AA.AskRefusal):
        AA.resolve_target("   ", registry=roster.registry)
    payload = _error_payload(AA.handle_agent_ask({"agent": "", "question": "在吗"}))
    assert payload["ok"] is False


def test_display_name_and_case_both_resolve(roster):
    for spelling in (LIVE_PROJECT, "Resonote", "RESONOTE", " Resonote "):
        entry = AA.resolve_target(spelling, registry=roster.registry)
        assert entry.name == LIVE_PROJECT, spelling


# ---------------------------------------------------------------------------
# The question the child receives is attributed, and says no framework words
# ---------------------------------------------------------------------------


def test_question_names_the_asker_and_carries_the_original_words(roster):
    entry = AA.resolve_target(LIVE_PROJECT, registry=roster.registry)
    text = AA.compose_question("这周到底推到哪一步了？", entry=entry)
    assert "总秘书" in text
    assert "这周到底推到哪一步了？" in text
    assert AA._label(entry) in text


def test_question_text_carries_no_internal_vocabulary(roster):
    entry = AA.resolve_target(LIVE_PROJECT, registry=roster.registry)
    lowered = AA.compose_question("问题", entry=entry).casefold()
    for token in ("profile", "plopi_home", "plobi_home", "terminal_cwd", "registry", "l1", "l2"):
        assert token not in lowered, (
            f"{token!r} is an internal name — not something to put in front of a 分身"
        )


# ---------------------------------------------------------------------------
# Child command + environment — the dispatcher's recipe, the entry's values
# ---------------------------------------------------------------------------


def test_env_pins_the_entry_not_the_caller(roster, monkeypatch):
    monkeypatch.setenv("PLOBI_HOME", str(roster.plobi_home / "elsewhere"))
    monkeypatch.setenv("PLOBI_TUI", "1")
    entry = AA.resolve_target(LIVE_PROJECT, registry=roster.registry)

    _argv, env, cwd = AA.build_child_command(entry, "问")

    assert env["PLOBI_HOME"] == str(roster.profile_dir)
    assert env["PLOBI_PROFILE"] == LIVE_PROJECT
    assert env["TERMINAL_CWD"] == str(roster.project)
    assert cwd == str(roster.project)
    # A child must never boot the interactive TUI.
    assert "PLOBI_TUI" not in env


def test_argv_shape_and_no_force_approval_flag(roster):
    entry = AA.resolve_target(LIVE_PROJECT, registry=roster.registry)
    argv, _env, _cwd = AA.build_child_command(entry, "PROMPT")

    # argv[0] is whatever the shared resolver picked (PLOBI_BIN / PATH shim /
    # interpreter module form); the conversation flags after it are pinned.
    head = ["-p", LIVE_PROJECT, "--cli", "--accept-hooks", "chat", "-q"]
    assert argv[1 : 1 + len(head)] == head
    assert argv[1 + len(head) :] == ["PROMPT"]
    # The invariant that matters: a 分身 answers a question, it does not get a
    # blank cheque — holds for every future argv addition too.
    assert not any("yolo" in part.casefold() for part in argv)


def test_profile_that_never_landed_on_this_machine_is_refused(roster):
    os.rmdir(roster.plobi_home / "profiles" / _OTHER_PROJECT)
    payload = _error_payload(
        AA.handle_agent_ask({"agent": _OTHER_PROJECT, "question": "在吗"})
    )
    assert payload["ok"] is False
    assert LIVE_PROJECT in payload["available"]


# ---------------------------------------------------------------------------
# Bounded wait — config value, hard ceiling, no env-var back door
# ---------------------------------------------------------------------------


def _cfg(value):
    return {
        "plugins": {
            "entries": {AA.PLUGIN_KEY: {"agent_ask": {"timeout_seconds": value}}}
        }
    }


def test_default_timeout_is_bounded():
    resolved = AA.timeout_seconds({})
    assert AA.MIN_TIMEOUT_SECONDS <= resolved <= AA.MAX_TIMEOUT_SECONDS


def test_config_can_widen_but_never_breach_the_ceiling():
    assert AA.timeout_seconds(_cfg(60)) == 60.0
    assert AA.timeout_seconds(_cfg(10 ** 9)) == AA.MAX_TIMEOUT_SECONDS
    assert AA.timeout_seconds(_cfg(0)) == AA.MIN_TIMEOUT_SECONDS
    # Unparseable / absent fall back to the bounded default, never "no wait".
    assert AA.timeout_seconds(_cfg("soon")) == AA.DEFAULT_TIMEOUT_SECONDS
    assert AA.timeout_seconds(_cfg(None)) == AA.DEFAULT_TIMEOUT_SECONDS


def test_timeout_is_not_an_env_var_knob(monkeypatch):
    """Behavioural settings live in config.yaml — a ``PLOBI_*`` var is not a door."""
    monkeypatch.setenv("PLOBI_AGENT_ASK_TIMEOUT_SECONDS", "42")
    assert AA.timeout_seconds({}) == AA.DEFAULT_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# Failure mapping — structured error, never an exception out of the turn
# ---------------------------------------------------------------------------


def _fake_run(**overrides):
    base = {
        "timed_out": False,
        "missing_binary": False,
        "returncode": 0,
        "stdout": "stub stdout",
        "stderr": "",
        "duration": 1.0,
    }
    base.update(overrides)
    return lambda *args, **kwargs: base


def test_non_zero_exit_becomes_a_tool_error(roster, monkeypatch):
    monkeypatch.setattr(AA, "_run_child", _fake_run(returncode=3, stderr="boom"))
    payload = _error_payload(
        AA.handle_agent_ask({"agent": LIVE_PROJECT, "question": "在吗"})
    )
    assert payload["ok"] is False
    assert payload["exit_code"] == 3
    assert "boom" in payload["error"]


def test_timeout_becomes_a_tool_error(roster, monkeypatch):
    monkeypatch.setattr(AA, "_run_child", _fake_run(timed_out=True, returncode=-9))
    payload = _error_payload(
        AA.handle_agent_ask({"agent": LIVE_PROJECT, "question": "在吗"})
    )
    assert payload["ok"] is False
    assert payload["timeout_seconds"] == AA.timeout_seconds({})
    assert "Resonote" in payload["error"] and "秒" in payload["error"]


def test_missing_binary_becomes_a_tool_error(roster, monkeypatch):
    monkeypatch.setattr(AA, "_run_child", _fake_run(missing_binary=True))
    payload = _error_payload(
        AA.handle_agent_ask({"agent": LIVE_PROJECT, "question": "在吗"})
    )
    assert payload["ok"] is False


def test_a_silent_child_is_an_error_not_an_empty_answer(roster, monkeypatch):
    monkeypatch.setattr(AA, "_run_child", _fake_run(stdout="   ", returncode=0))
    payload = _error_payload(
        AA.handle_agent_ask({"agent": LIVE_PROJECT, "question": "在吗"})
    )
    assert payload["ok"] is False


def test_handler_never_raises_even_when_the_registry_explodes(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("registry on fire")

    monkeypatch.setattr(AA, "askable_entries", boom)
    raw = AA.handle_agent_ask({"agent": "anything", "question": "在吗"})
    assert "error" in json.loads(raw)


# ---------------------------------------------------------------------------
# Real subprocess behaviour — bounded, reaped, no stray children
# ---------------------------------------------------------------------------


def _child_script(tmp_path: Path, body: str) -> str:
    path = tmp_path / "child.py"
    path.write_text(body, encoding="utf-8")
    return str(path)


def test_run_child_reports_a_real_non_zero_exit(tmp_path):
    script = _child_script(tmp_path, "import sys\nsys.exit(7)\n")
    result = AA._run_child([sys.executable, script], dict(os.environ), str(tmp_path), 30)
    assert result["returncode"] == 7
    assert result["timed_out"] is False
    assert result["missing_binary"] is False


def test_run_child_reports_a_real_timeout_and_leaves_no_stray(tmp_path):
    if not hasattr(os, "killpg"):  # pragma: no cover - POSIX-only reaping check
        pytest.skip("process-group kill is POSIX-only")
    pidfile = tmp_path / "child.pid"
    script = _child_script(
        tmp_path,
        "import os, time\n"
        f"open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
        "time.sleep(120)\n",
    )

    result = AA._run_child([sys.executable, script], dict(os.environ), str(tmp_path), 1)

    assert result["timed_out"] is True
    pid = int(pidfile.read_text(encoding="utf-8"))
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        except PermissionError:  # pragma: no cover - not ours to see
            break
        time.sleep(0.05)
    else:  # pragma: no cover - fails loudly if a child is ever orphaned
        pytest.fail(f"child {pid} still running after the timeout — stray process")


def test_run_child_reports_a_missing_binary_without_raising(tmp_path):
    missing = str(tmp_path / "definitely-not-a-plobi")
    result = AA._run_child([missing], dict(os.environ), None, 5)
    assert result["missing_binary"] is True
    assert result["returncode"] is None


# ---------------------------------------------------------------------------
# Footprint gate — the schema only exists when somebody can be asked
# ---------------------------------------------------------------------------


def test_gate_off_without_master_mode(roster, monkeypatch):
    monkeypatch.setattr("plobi_cli.config.load_config", lambda: {})
    assert AA.check_agent_ask_available() is False


def test_gate_on_when_there_is_somebody_to_ask(roster, master_mode):
    assert AA.check_agent_ask_available() is True


def test_gate_off_when_no_live_alter_ego(roster, master_mode):
    from plobi.agents.registry import AgentRegistry

    path = roster.plobi_home / "plobi" / "projects.yaml"
    reg = AgentRegistry.load(path)
    for name in (LIVE_PROJECT, _OTHER_PROJECT):
        reg.dissolve(name)
    reg.save()
    assert AA.check_agent_ask_available() is False


def test_plugin_registers_the_tool_with_its_own_gate(roster, master_mode, monkeypatch):
    """``register(ctx)`` must wire the handler under the plugin toolset + gate."""
    from plobi.agents import registry as agent_registry

    # register() also opens the toolset on the active profile — that write is
    # this plugin's own bootstrapping, not what this test is about.
    monkeypatch.setattr(agent_registry, "ensure_north_star_toolset", lambda **kw: {})

    class _Ctx:
        def __init__(self):
            self.tools = []

        def register_tool(self, **kwargs):
            self.tools.append(kwargs)

        def register_hook(self, *_args, **_kwargs):
            pass

    ctx = _Ctx()
    NS.register(ctx)
    registered = {tool["name"]: tool for tool in ctx.tools}

    assert "plobi_agent_ask" in registered, sorted(registered)
    tool = registered["plobi_agent_ask"]
    assert tool["toolset"] == "plobi_north_star"
    assert tool["handler"] is AA.handle_agent_ask
    assert tool["schema"] is AA.AGENT_ASK_SCHEMA
    assert tool["check_fn"].__qualname__.endswith("check_agent_ask_available")
    assert tool["check_fn"]() is True
    # The new mouth must not have been folded into the closed Master group.
    assert registered["plobi_master_dispatch"]["check_fn"]() is False
    assert registered["plobi_secretary_ask"]["check_fn"]() is True


def test_tool_is_not_a_core_tool():
    """Not in ``_PLOBI_CORE_TOOLS`` — only toolsets that include it pay the schema."""
    from toolsets import _PLOBI_CORE_TOOLS

    assert "plobi_agent_ask" not in _PLOBI_CORE_TOOLS


# ---------------------------------------------------------------------------
# E2E — a stub PLOBI_BIN proves the propagation, not a mock's own return value
# ---------------------------------------------------------------------------

_STUB_ANSWER = "分身本人的回答：这周把采集接上了，还剩导出没做。"
_STUB_SESSION = "stub-session-1"


def _write_stub(path: Path, *, record: Path, exit_code: int, persist: bool) -> None:
    """A fake ``plobi`` that records what it was invoked with, like a child would."""
    stub = f"""#!{sys.executable}
import json, os, sys
from pathlib import Path

record = {{
    "argv": sys.argv[1:],
    "cwd": os.getcwd(),
    "env": {{
        key: os.environ.get(key)
        for key in ("PLOBI_HOME", "PLOBI_PROFILE", "TERMINAL_CWD", "PLOBI_TUI")
    }},
}}
Path({str(record)!r}).write_text(json.dumps(record), encoding="utf-8")
"""
    if persist:
        stub += f"""
home = os.environ.get("PLOBI_HOME")
if home:
    sys.path.insert(0, {str(REPO)!r})
    from plobi_state import SessionDB
    db = SessionDB(db_path=Path(home) / "state.db")
    db.create_session({_STUB_SESSION!r}, source="cli")
    db.append_message({_STUB_SESSION!r}, role="user", content=sys.argv[-1])
    db.append_message({_STUB_SESSION!r}, role="assistant", content={_STUB_ANSWER!r})
print("this stdout line is a decoy and must not become the answer")
"""
    stub += f"sys.exit({exit_code})\n"
    path.write_text(stub, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture()
def stub_bin(tmp_path, monkeypatch):
    record = tmp_path / "received.json"

    def _make(*, exit_code=0, persist=True):
        script = tmp_path / "plobi-stub"
        _write_stub(script, record=record, exit_code=exit_code, persist=persist)
        monkeypatch.setenv("PLOBI_BIN", str(script))
        return record

    return _make


def test_stub_child_receives_the_expected_argv_and_env(roster, stub_bin):
    record = stub_bin()

    raw = AA.handle_agent_ask({"agent": "Resonote", "question": "这周项目到底怎么样？"})
    payload = json.loads(raw)

    received = json.loads(record.read_text(encoding="utf-8"))
    head = ["-p", LIVE_PROJECT, "--cli", "--accept-hooks", "chat", "-q"]
    assert received["argv"][: len(head)] == head
    assert not any("yolo" in part.casefold() for part in received["argv"])
    # The attribution arrived intact: who is asking, and the owner's own words.
    prompt = received["argv"][len(head)]
    assert "总秘书" in prompt and "这周项目到底怎么样？" in prompt

    assert received["env"]["PLOBI_HOME"] == str(roster.profile_dir)
    assert received["env"]["PLOBI_PROFILE"] == LIVE_PROJECT
    assert received["env"]["TERMINAL_CWD"] == str(roster.project)
    assert os.path.realpath(received["cwd"]) == os.path.realpath(roster.project)
    assert received["env"]["PLOBI_TUI"] is None

    # The answer comes back out of the child's own session record — not the
    # noise it printed — and the session id travels with it, so the secretary
    # can quote the 分身 and still point at the record.
    assert payload["ok"] is True
    assert payload["agent"] == LIVE_PROJECT
    assert payload["answer"] == _STUB_ANSWER
    assert payload["session_id"] == _STUB_SESSION
    assert (roster.profile_dir / "state.db").is_file()
    # The question itself is in that record: the 分身 kept a copy of being asked.
    import sqlite3

    conn = sqlite3.connect(str(roster.profile_dir / "state.db"))
    try:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id",
            (_STUB_SESSION,),
        ).fetchall()
    finally:
        conn.close()
    assert [role for role, _ in rows] == ["user", "assistant"]
    assert "这周项目到底怎么样？" in rows[0][1]


def test_stub_child_failure_propagates_as_a_tool_error(roster, stub_bin):
    stub_bin(exit_code=4, persist=False)
    payload = _error_payload(
        AA.handle_agent_ask({"agent": LIVE_PROJECT, "question": "在吗"})
    )
    assert payload["ok"] is False
    assert payload["exit_code"] == 4
