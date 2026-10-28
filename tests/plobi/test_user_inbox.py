"""裁定 201：对用户说话有自己的收件人和落点，并且要提醒他。

用户 10-23 拍的口径：「回访 / 提醒这类对你说的话，落在你在 L1 的聊天」，
「要有提示用户的手段」。四条判据：
1. 用户那一屏的唯一判据 = `source='desktop'` 且 `agent_id` 空、未归档的最近一条；
2. `say(to=用户)` 落进那一屏、带 `[来自 X · 时间]` 与 `asked_by=say:X`，**不叫醒**；
3. 发给自己要被拒（自指那条永远没有回音）；
4. cron 的 `deliver=user` 与它同落点，失败要说成失败。
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO / "plugins" / "plobi-north-star"
USER_SESSION = "sess-user"


@pytest.fixture(scope="module")
def PKG():
    name = "plobi_test_user_ns_pkg"
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
def CH(PKG):
    return importlib.import_module(PKG.__name__ + ".channel")


class _FakeDB:
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else [
            {"id": "sess-agent", "agent_id": "Framelet"},
            {"id": USER_SESSION, "agent_id": ""},
        ]
        self.appended = []

    def list_sessions_rich(self, **_kwargs):
        return list(self.rows)

    def append_message(self, session_id, *, role=None, content=None, asked_by=None, kind=None, reply_to=None):
        self.appended.append((session_id, role, content, asked_by))
        return "m-user"


def test_the_user_screen_is_the_desktop_row_without_an_agent():
    import plobi_user_inbox as UI

    fake = _FakeDB()
    original = UI.SessionDB
    UI.SessionDB = lambda: fake
    try:
        assert UI.user_inbox_session_id() == USER_SESSION
    finally:
        UI.SessionDB = original


def test_say_to_user_lands_in_the_users_screen_and_does_not_wake(CH, monkeypatch):
    import plobi_user_inbox as UI

    fake = _FakeDB()
    notified = []
    monkeypatch.setattr(UI, "SessionDB", lambda: fake)
    monkeypatch.setattr(UI, "notify_user", lambda body, **kw: (notified.append((body, kw)), True)[1])
    monkeypatch.setattr(CH, "_trace", lambda record: None)

    out = json.loads(CH.handle_say({"to": "用户", "parts": ["今晚回访一件事"]}))

    assert out["ok"] is True and out["to"] == "用户"
    assert out["session_id"] == USER_SESSION
    assert out["notified"] is True
    session_id, role, content, asked_by = fake.appended[-1]
    assert session_id == USER_SESSION and role == "user"
    assert content.startswith("[来自 plobi · ") or "[来自" in content
    assert asked_by.startswith("say:")
    assert notified, "对用户说话必须提醒他一声"


def test_saying_to_yourself_is_refused(CH, monkeypatch):
    from plobi.agents.registry import AgentEntry

    entry = AgentEntry.from_dict("agenda", {"role": "l2_agenda"})
    monkeypatch.setattr(CH, "current_agent_name", lambda *a, **k: "agenda")
    monkeypatch.setattr(CH, "_deliver_or_fail", lambda to, parts: (entry, "话", None))
    monkeypatch.setattr(CH, "_session_db", lambda: _FakeDB())

    out = json.loads(CH.handle_say({"to": "agenda", "parts": ["话"]}))

    assert out["ok"] is False
    assert "就是你自己" in out["error"]
    assert "to=用户" in out["error"]


def test_ask_cannot_target_the_user(CH):
    out = json.loads(CH.handle_ask({"to": "用户", "parts": ["在吗"]}))

    assert out["ok"] is False
    assert "say(to=用户)" in out["error"]
