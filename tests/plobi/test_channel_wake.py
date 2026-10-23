"""裁定 200：`say` 投递之后，收件人像收到一条用户消息那样自己开工。

钉四条护栏 + 一个跨模块交接：
1. 命令形状必须 `--resume` 到刚投递的那一屏（不是另开一屏）；
2. 同一个 Agent 有活着的唤醒进程时不再起第二轮（两次＝两份钱）；
3. 被唤醒的那一轮（`PLOBI_WAKE=1`）不再往下叫醒（防乒乓）；
4. 叫不醒不算 `say` 失败：投递已经成功，返回值照实带 `woke=false` + 原因；
5. `handle_say` 真的把刚落的那条会话号交给唤醒那一枚（跨模块那一行最容易断）。

不起真子进程、不写真家：`build_child_command` 与 `Popen` 都打桩，标记与日志落 tmp。
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO / "plugins" / "plobi-north-star"


@pytest.fixture(scope="module")
def PKG():
    name = "plobi_test_wake_ns_pkg"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)]
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        module.__package__ = name
        module.__path__ = [str(PLUGIN_DIR)]
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


@pytest.fixture()
def WK(PKG):
    return importlib.import_module(PKG.__name__ + ".wake")


@pytest.fixture()
def CH(PKG):
    return importlib.import_module(PKG.__name__ + ".channel")


class _FakeProc:
    pid = 4321


def _entry(name: str = "agenda"):
    from plobi.agents.registry import AgentEntry

    return AgentEntry.from_dict(name, {"role": "l2_agenda"})


@pytest.fixture()
def spawn(WK, tmp_path, monkeypatch):
    """把唤醒要碰的外部东西全指到 tmp；返回记录 Popen 的列表。"""
    monkeypatch.delenv("PLOBI_WAKE", raising=False)
    monkeypatch.setattr(WK, "_wake_dir", lambda: tmp_path / "wake")
    (tmp_path / "wake").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(WK, "_wake_log_path", lambda: tmp_path / "wake.log")

    starts: list[dict] = []

    def _fake_popen(argv, **kwargs):
        starts.append({"argv": argv, **kwargs})
        return _FakeProc()

    monkeypatch.setattr(WK.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(
        WK.AA,
        "build_child_command",
        lambda entry, prompt, asked_by="l1", resume_session=None: (
            ["plobi", "--resume", str(resume_session or ""), "-q", prompt],
            {},
            None,
        ),
    )
    return starts


def test_the_woken_turn_resumes_the_delivered_session(WK, spawn):
    ok, note = WK.wake_agent(_entry(), "sess-42", sender="L1")

    assert ok is True and "4321" in note
    argv = spawn[0]["argv"]
    assert argv[argv.index("--resume") + 1] == "sess-42", "叫醒的那一轮必须续在刚投递的那一屏"
    assert spawn[0]["env"]["PLOBI_WAKE"] == "1"
    assert spawn[0]["env"]["PLOBI_ASKED_BY"] == "say:L1"


def test_a_second_wake_is_skipped_while_the_first_is_alive(WK, spawn, tmp_path):
    marker = tmp_path / "wake" / f"{WK._safe_slug('agenda')}.json"
    marker.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")

    ok, note = WK.wake_agent(_entry(), "sess-1", sender="L1")

    assert ok is False
    assert "已经在干" in note
    assert spawn == [], "同一个 Agent 不许并发两轮"


def test_a_woken_turn_never_wakes_the_next_hop(WK, spawn, monkeypatch):
    monkeypatch.setenv("PLOBI_WAKE", "1")

    ok, note = WK.wake_agent(_entry(), "sess-1", sender="Aura")

    assert ok is False
    assert "不再往下叫醒" in note
    assert spawn == []


def test_a_failed_spawn_is_reported_not_raised(WK, tmp_path, monkeypatch):
    monkeypatch.delenv("PLOBI_WAKE", raising=False)
    monkeypatch.setattr(WK, "_wake_dir", lambda: tmp_path / "wake")
    (tmp_path / "wake").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(WK, "_wake_log_path", lambda: tmp_path / "wake.log")
    monkeypatch.setattr(
        WK.AA, "build_child_command", lambda entry, prompt, asked_by="l1", resume_session=None: (["plobi"], {}, None)
    )

    def _boom(argv, **kwargs):
        raise OSError("no such binary")

    monkeypatch.setattr(WK.subprocess, "Popen", _boom)

    ok, note = WK.wake_agent(_entry(), "sess-1", sender="L1")

    assert ok is False
    assert "叫不醒" in note


class _FakeDB:
    def __init__(self):
        self.appended = []

    def latest_conversational_session_for_agent(self, agent_id):
        return "sess-window"

    def create_session(self, session_id, *, source=None, agent_id=None):
        return session_id

    def append_message(self, session_id, *, role=None, content=None, asked_by=None):
        self.appended.append((session_id, role, content, asked_by))
        return "m1"


def test_handle_say_hands_the_new_session_to_the_waker(CH, monkeypatch):
    """跨模块那一行：唤醒拿到的必须是刚投递的那一屏，不是随便一条。"""
    db = _FakeDB()
    monkeypatch.setattr(CH, "_session_db", lambda: db)
    monkeypatch.setattr(CH, "_trace", lambda record: None)
    monkeypatch.setattr(
        CH,
        "_roster",
        lambda: type("R", (), {"agents": {}, "entries": lambda self=None: [], "get": lambda self, n: None})(),
    )
    # 收件人解析用真的那一枚太绕，这里直接把它打桩成「解析成功」。
    from plobi.agents.registry import AgentEntry

    entry = AgentEntry.from_dict("agenda", {"role": "l2_agenda"})
    monkeypatch.setattr(CH, "_deliver_or_fail", lambda to, parts: (entry, "一句话", None))
    seen: list[tuple] = []

    def _fake_wake(e, sid, sender):
        seen.append((e.name, sid))
        return True, "ok"

    monkeypatch.setattr(CH.WK, "wake_agent", _fake_wake)

    out = json.loads(CH.handle_say({"to": "agenda", "parts": ["一句话"]}, session_id="caller-session"))

    assert out["ok"] is True
    assert out["session_id"] == "sess-window"
    assert seen == [("agenda", "sess-window")], "唤醒必须拿到刚投递的那一屏"


def test_a_failed_wake_does_not_turn_say_into_an_error(CH, monkeypatch):
    from plobi.agents.registry import AgentEntry

    entry = AgentEntry.from_dict("agenda", {"role": "l2_agenda"})
    monkeypatch.setattr(CH, "_session_db", lambda: _FakeDB())
    monkeypatch.setattr(CH, "_trace", lambda record: None)
    monkeypatch.setattr(CH, "_deliver_or_fail", lambda to, parts: (entry, "一句话", None))
    monkeypatch.setattr(CH.WK, "wake_agent", lambda e, sid, sender: (False, "叫不醒它：没家"))

    out = json.loads(CH.handle_say({"to": "agenda", "parts": ["一句话"]}))

    assert out["ok"] is True, "话已经送到了，叫不醒不等于 say 失败"
    assert out["woke"] is False
    assert "没家" in out["wakeNote"]
