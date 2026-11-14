"""裁定 218：「派给谁」的真源从 SOUL 散文搬进名册行。

测的是**关系**，不是快照：域声明由名册行给出（category 永远算一个域）、
程序在**投递之前**拿这张表核对 ``to``、错配只拒绝并列出权威候选而**不改写
收件人**、没给 ``domain`` 时既有行为一格不变、名册读不动时旁路放行。
全部用假名册／假 SessionDB／独立 sqlite，不起真子进程、不写真家。
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO / "plugins" / "plobi-core"
PKG_NAME = "plobi_domain_roster_test_pkg"


@pytest.fixture(scope="module")
def CH():
    """按包方式加载（channel.py 有相对导入 ``from . import agent_ask``）。"""
    if PKG_NAME not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            PKG_NAME, PLUGIN_DIR / "__init__.py",
            submodule_search_locations=[str(PLUGIN_DIR)],
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        module.__package__ = PKG_NAME
        module.__path__ = [str(PLUGIN_DIR)]
        sys.modules[PKG_NAME] = module
        spec.loader.exec_module(module)
    return importlib.import_module(f"{PKG_NAME}.channel")


class _Entry:
    def __init__(self, name, *, category="", domains=(), archived=False):
        self.name = name
        self.category = category
        self.domains = tuple(domains)
        self.archived = archived


class _Registry:
    def __init__(self, entries):
        self.agents = {e.name: e for e in entries}

    def get(self, name):
        return self.agents.get(name)


def _default_registry():
    return _Registry([
        _Entry("l1", category="butler"),
        _Entry("agenda", category="butler", domains=("schedule", "agenda")),
        _Entry("Nymo", category="projects"),
        _Entry("电路", category="courses", domains=("homework",)),
        _Entry("旧摊", category="projects", archived=True),
    ])


class _FakeSessionDB:
    def __init__(self):
        self.sessions: dict[str, list[dict]] = {}
        self.appended: list[tuple] = []
        self.created: list[tuple] = []

    def latest_conversational_session_for_agent(self, agent_id):
        return next(iter(self.sessions), "")

    def create_session(self, session_id, *, source=None, agent_id=None):
        self.sessions.setdefault(session_id, [])
        self.created.append((session_id, source, agent_id))
        return session_id

    def append_message(self, session_id, *, role=None, content=None,
                       asked_by=None, kind=None, reply_to=None):
        rows = self.sessions.setdefault(session_id, [])
        mid = f"m_{len(self.appended) + 1}"
        rows.append({"id": mid, "role": role, "content": content})
        self.appended.append((session_id, role, content, asked_by, reply_to))
        return mid

    def get_messages(self, session_id):
        return list(self.sessions.get(session_id, []))

    def message_count(self, session_id=None, agent_id=None):
        return sum(len(v) for v in self.sessions.values())

    def get_session(self, session_id):
        return {}


@pytest.fixture
def env(monkeypatch, tmp_path, CH):
    """隔离票库 + 假名册/会话/wake/ask；返回可观察的上下文。"""
    monkeypatch.setenv("PLOBI_TICKET_DB", str(tmp_path / "tickets.db"))
    monkeypatch.setenv("PLOBI_AGENT_ID", "l1")
    db = _FakeSessionDB()
    db.create_session("chan_a", source="agent_message", agent_id="agenda")
    monkeypatch.setattr(CH, "_session_db", lambda: db)
    registry = _default_registry()
    monkeypatch.setattr(CH, "_roster", lambda: registry)

    wake_calls: list[dict] = []

    def fake_wake(entry, session_id, *, sender, ticket_id=""):
        wake_calls.append({"entry": entry.name, "ticket_id": ticket_id})
        return True, "ok"

    monkeypatch.setattr(CH.WK, "wake_agent", fake_wake)
    monkeypatch.setattr(CH, "_trace", lambda record: None)
    ask_calls: list[dict] = []

    def fake_ask(args, **kwargs):
        ask_calls.append(dict(args))
        return json.dumps({"ok": True, "answer": "排好了"})

    monkeypatch.setattr(CH.AA, "handle_agent_ask", fake_ask)
    return {
        "db": db, "wake": wake_calls, "ask": ask_calls,
        "registry": registry, "tmp": tmp_path,
    }


def _ledger():
    from plobi.delegation.tickets import TicketLedger

    return TicketLedger()


def _say(CH, **extra):
    return json.loads(CH.handle_say(
        {"to": "agenda", "parts": [{"text": "把下午的会排好"}], **extra}
    ))


# ── 纯函数：域声明是数据，不是散文 ──────────────────────────────────────


def test_category_is_always_a_domain_plus_optional_tags():
    from plobi.agents.domains import declared_domains

    assert declared_domains(_Entry("a", category="courses")) == frozenset({"courses"})
    assert declared_domains(_Entry(
        "a", category="butler", domains=("Schedule", " schedule ", "agenda")
    )) == frozenset({"butler", "schedule", "agenda"})
    # 存量行一行没写 domains 也照样有域，迁移成本为零。
    assert declared_domains(_Entry("a")) == frozenset()


def test_domain_roster_is_sorted_and_excludes_archived_rows():
    from plobi.agents.domains import domain_roster, owners_for_domain

    table = domain_roster(_default_registry().agents.values())
    assert table["schedule"] == ["agenda"]
    assert table["projects"] == ["Nymo"], "归档的「旧摊」不该还在候选里"
    assert table["courses"] == ["电路"]
    assert owners_for_domain("不存在的域", _default_registry().agents.values()) == []


def test_check_recipient_only_validates_never_rewrites():
    from plobi.agents.domains import check_recipient

    rows = list(_default_registry().agents.values())
    ok, note, owners = check_recipient(_Entry("agenda"), "schedule", rows)
    assert ok is True and note == "" and owners == ["agenda"]

    ok, note, owners = check_recipient(_Entry("Nymo"), "schedule", rows)
    assert ok is False and "没有声明负责域" in note and owners == ["agenda"]
    # 关键：函数只返回判断与候选，没有任何「改 to」的返回值或副作用。

    ok, note, owners = check_recipient(_Entry("agenda"), "不存在的域", rows)
    assert ok is False and owners == [] and "还没有任何一行" in note

    # 没给 domain = 完全不介入（fail-open），这是零回归的硬约束。
    assert check_recipient(_Entry("谁都行"), "", rows) == (True, "", [])


def test_agent_entry_domains_roundtrip():
    from plobi.agents.registry import AgentEntry

    entry = AgentEntry.from_dict("sim", {
        "role": "l2_project", "category": "courses", "domains": ["Homework", "exam"],
    })
    assert entry.domains == ("homework", "exam")
    data = entry.to_dict()
    assert data["domains"] == ["homework", "exam"]
    clone = AgentEntry.from_dict("sim", data)
    assert clone.domains == entry.domains and clone.category == "courses"
    assert "domains" not in AgentEntry.from_dict("bare", {"role": "l2_project"}).to_dict()


# ── 接进两枚嘴：错配在投递前挡下 ────────────────────────────────────────


def test_say_with_matching_domain_delivers_and_records_it(CH, env):
    out = _say(CH, domain="schedule")
    assert out["ok"] is True
    assert out["domain"] == "schedule" and out["domain_owners"] == ["agenda"]
    assert env["db"].appended, "匹配就该照常投递"
    assert env["wake"], "匹配就该照常唤醒"

    ticket = _ledger().get(out["ticket_id"])
    assert ticket is not None
    assert ticket.scope["domain"] == "schedule"
    assert ticket.refs["domain"] == "schedule"
    assert ticket.refs["domain_owners"] == ["agenda"]


def test_say_with_mismatched_domain_is_refused_before_any_delivery(CH, env):
    out = json.loads(CH.handle_say({
        "to": "Nymo", "parts": [{"text": "把下午的会排好"}], "domain": "schedule",
    }))
    assert out["ok"] is False
    assert "没有声明负责域" in out["error"]
    assert out["domain"] == "schedule"
    assert out["domain_owners"] == ["agenda"], "列权威候选，但不替模型挑人"
    assert "to" not in out, "绝不回一个被程序改过的收件人"
    assert env["db"].appended == [], "错配一个字节都不该投递"
    assert env["wake"] == [], "错配不该起那一轮"
    assert _ledger().list_tickets() == [], "错配不该开授权票"


def test_unknown_domain_is_refused_with_a_honest_empty_candidate_list(CH, env):
    out = _say(CH, domain="不存在的域")
    assert out["ok"] is False and "还没有任何一行" in out["error"]
    assert out["domain_owners"] == []
    assert env["db"].appended == []


def test_multiple_owners_are_all_listed_and_none_is_picked(CH, env):
    """一个域几行都声明了：程序照单全列，选谁仍由发起方决定。"""
    env["registry"].agents["Nymo"].domains = ("schedule",)
    out = json.loads(CH.handle_say({
        "to": "电路", "parts": [{"text": "排会"}], "domain": "schedule",
    }))
    assert out["ok"] is False
    assert sorted(out["domain_owners"]) == ["Nymo", "agenda"]


def test_no_domain_means_legacy_behavior_unchanged(CH, env):
    """硬约束：不填 domain，返回值与票里都不许多出这一层，也不许从内容猜域。"""
    out = _say(CH)
    assert out["ok"] is True
    assert "domain" not in out and "domain_owners" not in out
    ticket = _ledger().get(out["ticket_id"])
    assert "domain" not in ticket.scope and "domain" not in ticket.refs
    # parts 里明明有「会」，程序也没去反推这是个日程域。
    assert env["db"].appended


def test_ask_domain_gate_runs_before_spawning(CH, env):
    out = json.loads(CH.handle_ask({
        "to": "Nymo", "parts": [{"text": "排会"}], "domain": "schedule",
    }))
    assert out["ok"] is False and "没有声明负责域" in out["error"]
    assert env["ask"] == [], "错配不该起子进程问错人"

    good = json.loads(CH.handle_ask({
        "to": "agenda", "parts": [{"text": "排会"}], "domain": "schedule",
    }))
    assert good["ok"] is True and good["domain"] == "schedule"
    assert len(env["ask"]) == 1


def test_user_target_never_touches_the_domain_gate(CH, env, monkeypatch):
    """`to=用户` 在 domain 门之前就分流了（裁定 201），不写真家、不进 Agent 会话。"""
    delivered: list[str] = []

    def fake_user(text, *, sender):
        delivered.append(text)
        return json.dumps({"ok": True, "to": "用户"})

    monkeypatch.setattr(CH, "_deliver_to_user", fake_user)
    out = json.loads(CH.handle_say({
        "to": "用户", "parts": [{"text": "下午三点开会"}], "domain": "schedule",
    }))
    assert out["ok"] is True and delivered == ["下午三点开会"]
    assert env["db"].appended == []
    assert env["wake"] == []


def test_unreadable_roster_is_bypassed_not_blocking(CH, env, monkeypatch):
    """域表是旁路：名册读不动绝不许把说话这条路堵死。"""
    def broken():
        raise RuntimeError("名册暂时读不到")

    monkeypatch.setattr(CH, "_roster", broken)
    error, owners = CH._domain_gate({"domain": "schedule"}, _Entry("agenda"))
    assert error == "" and owners == []
    # 门不挡，但 resolve_recipient 那道**既有**的名册闸仍会照常拒绝——
    # 这里只钉「域层不额外引入失败」，不改原有的名册读取语义。
    out = json.loads(CH.handle_say({
        "to": "agenda", "parts": [{"text": "排会"}], "domain": "schedule",
    }))
    assert out["ok"] is False and "读不到名册" in out["error"]


def test_domain_is_metadata_not_a_third_mouth(CH):
    """裁定 183 不推翻：工具面仍只有 say/ask 两枚嘴，domain 只是可选元数据。"""
    from importlib import import_module

    assert CH.SAY_SCHEMA["name"] == "say" and CH.ASK_SCHEMA["name"] == "ask"
    for schema in (CH.SAY_SCHEMA, CH.ASK_SCHEMA):
        props = schema["parameters"]["properties"]
        assert schema["parameters"]["required"] == ["to", "parts"]
        assert "state" not in props and "intent" not in props
        assert props["domain"]["type"] == "string" and "enum" not in props["domain"]
    plugin = import_module(PKG_NAME)
    assert {row[0] for row in plugin._CHANNEL_TOOLS} == {"say", "ask"}
    # 没有关键词表：程序不从 parts 内容反推域（见 no_domain 那条零回归断言）。
