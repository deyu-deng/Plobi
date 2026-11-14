"""WP-SECRETARY-TASK 收口：秘书每一跳在看板 ``logged`` 上留一件有身份的记录。

这一档测的是**生产者回来了**，不是快照。10-05 那版把落痕写在
``run_secretary_ask`` 的包装里，11-02 ``WP-RETIRED-MOUTH-SWEEP`` 删
``_secretary_ask`` 一窝时把它一起带走了（后台读：``kanban.db`` 32 条
``logged``，最后一条停在 2026-10-20 23:06，之后再没长过）。今天秘书派活走的
就是 ``say`` / ``ask`` 这两枚嘴，所以生产者挂在那儿。

三条关系：
1. L1 秘书说一句话 ⇒ 板上多一条 ``logged``、工具回传里带着那个 id 与「本趟记了
   1 件」那半句（形状 B：看得见这趟记了几件）。
2. 不是 L1 的人说话 ⇒ **一条都不加**——看板不许被 Agent 间的日常对话灌满；
   L2→L3 那一跳已经有授权票可追。
3. 板子写不进去 ⇒ 这一句话照旧送到（旁路不许反噬投递）。

真库隔离靠把 ``kanban_db.connect_closing`` 指到 tmp，不靠 ``PLOBI_HOME``
（那个常量在进程里有缓存，monkeypatch 环境变量不保证生效）。
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO / "plugins" / "plobi-core"


@pytest.fixture(scope="module")
def CH():
    """按包方式加载（channel.py 有相对导入 ``from . import agent_ask``）。"""
    pkg_name = "plobi_test_secretary_record_pkg"
    if pkg_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            pkg_name, PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)]
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        module.__package__ = pkg_name
        module.__path__ = [str(PLUGIN_DIR)]
        sys.modules[pkg_name] = module
        spec.loader.exec_module(module)
    return importlib.import_module(f"{pkg_name}.channel")


@pytest.fixture()
def board(CH, monkeypatch, tmp_path):
    """把看板库指到 tmp；返回 ``(kb, db_path)`` 供断言。"""
    from plobi_cli import kanban_db as kb

    db_path = tmp_path / "kanban.db"

    @contextlib.contextmanager
    def _open(*_args, **_kwargs):
        conn = kb.connect(db_path=db_path)
        try:
            yield conn
        finally:
            conn.close()

    monkeypatch.setattr(kb, "connect_closing", _open)
    return kb, db_path


@pytest.fixture(autouse=True)
def _isolate_ticket_db(monkeypatch, tmp_path):
    """say/ask 会开授权票；这一档的「不写真家」承诺要继续成立。"""
    monkeypatch.setenv("PLOBI_TICKET_DB", str(tmp_path / "tickets.db"))


class _FakeSessionDB:
    def __init__(self):
        self.appended: list[tuple] = []
        self.created: list[tuple] = []

    def latest_conversational_session_for_agent(self, agent_id):
        return "s_existing"

    def create_session(self, session_id, *, source=None, agent_id=None):
        self.created.append((session_id, source, agent_id))
        return session_id

    def append_message(self, session_id, *, role=None, content=None, asked_by=None, kind=None, reply_to=None):
        self.appended.append((session_id, role, content, asked_by))
        return f"m_{len(self.appended)}"

    def message_count(self, session_id=None, agent_id=None):
        if session_id is None:
            return len(self.appended)
        return sum(1 for row in self.appended if row[0] == session_id)

    def get_session(self, session_id):
        return {"session_id": session_id, "agent_id": ""}


@pytest.fixture()
def db(CH, monkeypatch):
    fake = _FakeSessionDB()
    monkeypatch.setattr(CH, "_session_db", lambda: fake)
    return fake


def _roster(rows: dict[str, dict]):
    from plobi.agents.registry import AgentEntry

    agents = {name: AgentEntry.from_dict(name, spec) for name, spec in rows.items()}

    class _Registry:
        def __init__(self, table):
            self.agents = table

        def get(self, name):
            return self.agents.get(name)

    return _Registry(agents)


@pytest.fixture()
def secretary_roster(CH, monkeypatch):
    """名册里有一行 L1 总秘书和一行普通 L2。"""
    rows = _roster(
        {
            "secretary": {"role": "l1_secretary", "category": "butler", "profile": "default"},
            "Nymo": {"role": "l2_project", "category": "projects"},
        }
    )
    monkeypatch.setattr(CH, "_roster", lambda: rows)
    # 不真起子进程：wake 那条腿与本档无关。
    monkeypatch.setattr(CH.WK, "wake_agent", lambda *a, **kw: (False, ""))
    return rows


def _logged_rows(kb, db_path):
    conn = kb.connect(db_path=db_path)
    try:
        return [
            dict(row)
            for row in conn.execute("SELECT * FROM tasks WHERE status = 'logged' ORDER BY created_at")
        ]
    finally:
        conn.close()


# ─── 生产者回来了 ───────────────────────────────────────────────────────────


def test_l1_secretary_say_leaves_a_logged_record(CH, monkeypatch, db, board, secretary_roster):
    """秘书说一句 ⇒ 板上一条 logged，回传里带着 id 与「本趟记了 1 件」。"""
    kb, db_path = board
    monkeypatch.setattr(CH, "current_agent_name", lambda session_id="": "secretary")

    out = json.loads(CH.handle_say({"to": "Nymo", "parts": ["排一下明天"]}))

    assert out["ok"] is True and out["mode"] == "say"
    assert out["logged_column"] == "logged"
    assert out["logged_task_id"].startswith("t_")
    assert "本趟记了 1 件" in out["logged_note"]
    assert out["logged_task_id"] in out["logged_note"], "回传要把件号写出来，不然用户找不到它"

    rows = _logged_rows(kb, db_path)
    assert len(rows) == 1
    assert rows[0]["id"] == out["logged_task_id"]
    assert rows[0]["created_by"] == "secretary"
    # 假名册那一行不在**真**名册里 ⇒ ``_canonical_assignee`` 走旧归一（小写）。
    # 这是裁 192 的既有契约：认得出那一行才原样留写法。
    assert rows[0]["assignee"].lower() == "nymo"
    assert rows[0]["title"].startswith("secretary:say · Nymo · ")
    assert out["ticket_id"] in rows[0]["body"], "记录要能指回授权票，两边才对得上号"


def test_logged_record_can_never_be_dispatched(CH, monkeypatch, db, board, secretary_roster):
    """记账不许变成排队：落的是 ``logged``，而 dispatcher 只捡 ``ready``。"""
    kb, db_path = board
    monkeypatch.setattr(CH, "current_agent_name", lambda session_id="": "secretary")

    out = json.loads(CH.handle_say({"to": "Nymo", "parts": ["看一下导出"]}))

    rows = _logged_rows(kb, db_path)
    assert rows and rows[0]["id"] == out["logged_task_id"]
    assert rows[0]["status"] == "logged"
    # claim_task 的 CAS、recompute_ready、promote_task 三处都只认 ready/todo/blocked。
    assert rows[0]["status"] not in {"ready", "todo", "blocked"}
    assert "logged" in kb.VALID_INITIAL_STATUSES


def test_ask_leaves_a_logged_record_too(CH, monkeypatch, db, board, secretary_roster):
    """问这一趟也是工作：ask 同样留一件，不只看 say。"""
    kb, db_path = board
    monkeypatch.setattr(CH, "current_agent_name", lambda session_id="": "secretary")
    monkeypatch.setattr(
        CH.AA, "handle_agent_ask",
        lambda *a, **kw: json.dumps({"ok": True, "answer": "明天三节课"}),
    )

    out = json.loads(CH.handle_ask({"to": "Nymo", "parts": ["明天有什么"]}))

    assert out["ok"] is True and out["answer"] == "明天三节课"
    rows = _logged_rows(kb, db_path)
    assert len(rows) == 1 and rows[0]["title"].startswith("secretary:ask · Nymo · ")
    assert out["logged_task_id"] == rows[0]["id"]


# ─── 不许泛滥 ───────────────────────────────────────────────────────────────


def test_non_secretary_say_leaves_no_record(CH, monkeypatch, db, board, secretary_roster):
    """L2 之间说话不落痕——看板不该被日常对话灌满。"""
    kb, db_path = board
    monkeypatch.setattr(CH, "current_agent_name", lambda session_id="": "Nymo")

    out = json.loads(CH.handle_say({"to": "secretary", "parts": ["这周做完了"]}))

    assert out["ok"] is True
    assert "logged_task_id" not in out
    assert _logged_rows(kb, db_path) == []


def test_unknown_sender_leaves_no_record(CH, monkeypatch, db, board, secretary_roster):
    """名册里查不到的身份（中性兜底名）不落痕，绝不按名字前缀猜。"""
    kb, db_path = board
    monkeypatch.setattr(CH, "current_agent_name", lambda session_id="": "plobi")

    out = json.loads(CH.handle_say({"to": "Nymo", "parts": ["在吗"]}))

    assert out["ok"] is True
    assert "logged_task_id" not in out
    assert _logged_rows(kb, db_path) == []


# ─── 旁路不许反噬 ───────────────────────────────────────────────────────────


def test_board_write_failure_does_not_break_say(CH, monkeypatch, db, board, secretary_roster):
    """板子写不进去只是少一条痕，这一句话照旧送到、照旧开到票。"""
    kb, db_path = board
    monkeypatch.setattr(CH, "current_agent_name", lambda session_id="": "secretary")

    def _boom(*_a, **_kw):
        raise RuntimeError("board locked")

    monkeypatch.setattr(kb, "create_task", _boom)

    out = json.loads(CH.handle_say({"to": "Nymo", "parts": ["排一下明天"]}))

    assert out["ok"] is True, "旁路失败不许把投递说成失败"
    assert out["to"] == "Nymo"
    assert "logged_task_id" not in out
    assert out.get("ticket_id"), "票照旧要开——记账与授权是两件事"
