"""裁定 183 的两枚原子：``say`` 与 ``ask`` 的行为契约。

测的是关系，不是快照：投递落到**接收方自己的会话**、``from`` 由运行时写不许模型
自报、文件指针只留路径不读内容、拒绝时给得出能发的人名、``ask`` 投递只做一次且
超时不丢「话已送到」这个事实。全部用假 SessionDB / 假名册 / 假 agent_ask，不起
真子进程、不写真家。
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
PLUGIN_DIR = REPO / "plugins" / "plobi-core"


@pytest.fixture(scope="module")
def CH():
    """按包方式加载（channel.py 有相对导入 ``from . import agent_ask``）。"""
    pkg_name = "plobi_test_channel_ns_pkg"
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


@pytest.fixture(autouse=True)
def _isolate_ticket_db(monkeypatch, tmp_path):
    """裁定 216 之后 say/ask 会开授权票；这一档「不写真家」的承诺要继续成立。"""
    monkeypatch.setenv("PLOBI_TICKET_DB", str(tmp_path / "tickets.db"))


class _FakeSessionDB:
    """只收本模块用到的方法；把每次调用记下来供断言。

    ``latest_conversational_session_for_agent`` 是 2026-10-21 收口后的唯一
    落点判据（真实现见 ``plobi_state.SessionDB``）：这里按「existing 第一条
    = 最新可见会话」模拟——测试要造「有会话」就往 existing 放一行。
    """

    def __init__(self, existing: list[dict] | None = None, sessions: dict | None = None):
        self.existing = existing or []
        self.sessions = sessions or {}
        self.created: list[tuple] = []
        self.appended: list[tuple] = []

    def latest_conversational_session_for_agent(self, agent_id):
        self.last_agent_id = agent_id
        if not self.existing:
            return None
        first = self.existing[0]
        return str(first.get("session_id") or first.get("id"))

    def list_sessions_rich(self, agent_id=None, limit=1):
        self.last_agent_id = agent_id
        return list(self.existing)

    def create_session(self, session_id, *, source=None, agent_id=None):
        self.created.append((session_id, source, agent_id))
        return session_id

    def append_message(self, session_id, *, role=None, content=None, asked_by=None, kind=None, reply_to=None):
        self.appended.append((session_id, role, content, asked_by))
        return f"m_{len(self.appended)}"

    def message_count(self, session_id=None, agent_id=None):
        """那一屏已有几条——ask 的回退投递靠它判「子进程已经落过话没有」。"""
        if session_id is None:
            return len(self.appended)
        return sum(1 for row in self.appended if row[0] == session_id)

    def get_session(self, session_id):
        return dict(self.sessions.get(session_id) or {})


def _roster_of(*names: str, archived: tuple[str, ...] = (), role: str = "principal"):
    """一个只认这几个名字的假名册。`role` 用来造日程秘书那一行（不是项目分身）。"""
    from plobi.agents.registry import AgentEntry

    agents: dict[str, AgentEntry] = {}
    for name in names:
        agents[name] = AgentEntry.from_dict(name, {"role": role, "category": "projects"})
    for name in archived:
        agents[name] = AgentEntry.from_dict(name, {"role": "principal", "category": "projects", "archived": True})

    class _Registry:
        def __init__(self, rows):
            self.agents = rows

        def get(self, name):
            return self.agents.get(name)

    return _Registry(agents)


@pytest.fixture()
def db(CH, monkeypatch):
    """把 channel 的 SessionDB 换成假的，并返回它（测试通过返回值拿调用记录）。

    """
    fake = _FakeSessionDB()
    monkeypatch.setattr(CH, "_session_db", lambda: fake)
    return fake


@pytest.fixture()
def trace(CH, tmp_path, monkeypatch):
    """把痕文件指到 tmp；返回读痕的帮手。"""
    target = tmp_path / "plobi" / CH.TRACE_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(CH, "trace_path", lambda: target)

    def _read() -> list[dict]:
        if not target.exists():
            return []
        return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]

    return _read


# ─── say：投递即返回 ───────────────────────────────────────────────────────


def test_say_delivers_into_the_recipient_own_session(CH, monkeypatch, db):
    """没有会话就开一个 source=agent_message 的；消息带 [来自 X · 时间] 头。"""
    monkeypatch.setattr(CH, "_roster", lambda: _roster_of("Nymo"))
    monkeypatch.setenv("PLOBI_AGENT_ID", "Aura")

    out = json.loads(CH.handle_say({"to": "Nymo", "parts": ["导出那块本周能接上"]}))

    assert out["ok"] is True and out["mode"] == "say" and out["to"] == "Nymo"
    assert out["deliverer"] == "Aura", "from 必须由运行时写，不许模型自报"
    # 会话问的是接收方那一行，不是发起方
    assert db.last_agent_id == "Nymo"
    assert len(db.created) == 1
    session_id, source, agent_id = db.created[0]
    assert source == "agent_message" and agent_id == "Nymo"
    assert session_id.startswith("chan_")
    # 消息追加进刚开的那个会话，role=user，asked_by 记发起方
    assert db.appended[0][0] == session_id
    assert db.appended[0][1] == "user"
    assert db.appended[0][3] == "Aura"
    assert "[来自 Aura · " in db.appended[0][2]
    assert "导出那块本周能接上" in db.appended[0][2]


def test_say_reuses_the_latest_session(CH, monkeypatch, db):
    """接收方已有会话 ⇒ 不新开，追加进最新那个。"""
    monkeypatch.setattr(CH, "_roster", lambda: _roster_of("Nymo"))
    db.existing = [{"session_id": "s_existing"}]

    out = json.loads(CH.handle_say({"to": "Nymo", "parts": [{"text": "在吗"}]}))

    assert out["ok"] is True
    assert db.created == [], "已有会话就不该再开一个"
    assert out["session_id"] == "s_existing"
    assert db.appended[0][0] == "s_existing"


def test_say_refuses_without_a_reachable_recipient(CH, monkeypatch, db):
    """空 to / 名册没有 / 已归档：明确回错并列出现在能发的人名。"""
    monkeypatch.setattr(CH, "_roster", lambda: _roster_of("Nymo", archived=("旧项目",)))

    empty = json.loads(CH.handle_say({"to": "", "parts": ["x"]}))
    assert empty["ok"] is False and "没说要发给谁" in empty["error"]

    missing = json.loads(CH.handle_say({"to": "不存在的人", "parts": ["x"]}))
    assert missing["ok"] is False and "没有叫" in missing["error"]
    assert missing["reachable"] == ["Nymo"], "拒绝时要把能发的人名给模型看"

    archived = json.loads(CH.handle_say({"to": "旧项目", "parts": ["x"]}))
    assert archived["ok"] is False and "归档" in archived["error"]

    assert db.appended == [], "拒绝的请求一个字节都不该投递"


def test_say_refuses_bad_parts(CH, monkeypatch, db):
    """空 parts / 空段 / 把文件内容当指针：都拒。"""
    monkeypatch.setattr(CH, "_roster", lambda: _roster_of("Nymo"))

    for parts, why in (
        ([], "至少要有一段"),
        ([{"text": ""}], "是空的"),
        ([{"path": "surface/x;base64,AAAA"}], "不是文件内容"),
    ):
        out = json.loads(CH.handle_say({"to": "Nymo", "parts": parts}))
        assert out["ok"] is False and why in out["error"], (parts, out)
    assert db.appended == []


def test_say_file_pointer_stays_a_pointer(CH, monkeypatch, db, tmp_path):
    """裁 183③：文件指针只留路径，绝不把内容读进上下文。"""
    material = tmp_path / "material.txt"
    material.write_text("这是正文，不该出现在消息里", encoding="utf-8")
    monkeypatch.setattr(CH, "_roster", lambda: _roster_of("Nymo"))

    out = json.loads(
        CH.handle_say({"to": "Nymo", "parts": [{"text": "看这个", "path": "surface/material.txt"}]})
    )

    assert out["ok"] is True
    delivered = db.appended[0][2]
    assert "[文件] surface/material.txt" in delivered
    assert "这是正文" not in delivered, "文件内容不许被内联进消息"


def test_say_writes_a_trace_with_both_ends(CH, monkeypatch, db, trace):
    """每条消息落一条带收发双方与时间戳的痕（追加式）。"""
    monkeypatch.setattr(CH, "_roster", lambda: _roster_of("Nymo"))
    monkeypatch.setenv("PLOBI_AGENT_ID", "Kit")

    CH.handle_say({"to": "Nymo", "parts": ["一条话"]})

    rows = trace()
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "say" and row["from"] == "Kit" and row["to"] == "Nymo"
    assert row["trace_id"] and row["session_id"] and row["message_id"]
    assert row["ts"] and row["parts"] == "一条话"


def test_say_delivery_failure_is_not_a_fake_success(CH, monkeypatch, db):
    """投递抛异常 ⇒ ok=False 带原因，绝不伪装成「发好了」。"""
    monkeypatch.setattr(CH, "_roster", lambda: _roster_of("Nymo"))

    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(db, "append_message", _boom)

    out = json.loads(CH.handle_say({"to": "Nymo", "parts": ["x"]}))
    assert out["ok"] is False and "disk full" in out["error"]


# ─── ask：投递一次，等，超时不丢「已送到」 ──────────────────────────────────


def _stub_answer(monkeypatch, CH, *answers):
    """把「叫醒成功 + 这一趟的答复」摆好，不真起子进程、不真等。

    2026-11-25 起 ask 与 say 共用 ``_deliver`` 落库、再经 ``wake_agent`` 叫醒、
    由 ``_wait_for_answer`` 轮询取答：替身只要盖住「地板」和「地板之后写出来的
    那一条」，其余都走真代码。
    """
    monkeypatch.setattr(CH.WK, "wake_agent", lambda *a, **kw: (True, ""))
    monkeypatch.setattr(CH.AA, "_session_max_message_id", lambda sid: 100)
    monkeypatch.setattr(CH, "_ask_wait_seconds", lambda: 0.01)
    queue = list(answers)

    def _answer(entry, session_id, since_id=0):
        return queue.pop(0) if queue else ("", 0)

    monkeypatch.setattr(CH.AA, "_session_answer", _answer)


def test_ask_delivers_into_the_recipients_own_screen(CH, monkeypatch, db):
    """断点②「答落在另一屏」：问话与答复都要在对方那一屏，不另起一条新会话。"""
    monkeypatch.setattr(CH, "_roster", lambda: _roster_of("Nymo"))
    db.existing = [{"session_id": "chan_nymo_screen"}]
    _stub_answer(monkeypatch, CH, ("还剩测试没补。", 42))
    woken: dict = {}
    monkeypatch.setattr(
        CH.WK, "wake_agent",
        lambda entry, sid, **kw: woken.update(
            entry=entry.name, session_id=sid, expect_answer=kw.get("expect_answer")
        ) or (True, ""),
    )
    out = json.loads(CH.handle_ask({"to": "Nymo", "parts": ["导出排期"]}))
    assert out["ok"] is True and out["session_id"] == "chan_nymo_screen"
    assert "还剩测试没补" in out["answer"]
    assert [row[0] for row in db.appended] == ["chan_nymo_screen"], "问话就落这一屏"
    assert woken["entry"] == "Nymo", "收件人由 resolve_recipient 判一次，交给下层的应是那一行"
    assert woken["session_id"] == "chan_nymo_screen", "叫醒的必须是它那一屏，不是新建的"
    assert woken["expect_answer"] is True, "ask 叫醒时要说清「它在等答复」"



def test_ask_does_not_post_the_question_twice(CH, monkeypatch, db):
    """问话只准落一条：旧的「没落就补发」已删，同一句不会被说成两遍。"""
    monkeypatch.setattr(CH, "_roster", lambda: _roster_of("Nymo"))
    db.existing = [{"session_id": "chan_nymo_screen"}]
    _stub_answer(monkeypatch, CH, ("好", 43))
    out = json.loads(CH.handle_ask({"to": "Nymo", "parts": ["在吗"]}))
    assert out["ok"] is True
    assert len(db.appended) == 1, "投递只做一次——这一趟一次都不许补发"
    assert db.appended[0][0] == "chan_nymo_screen" and db.appended[0][1] == "user"
    assert "在吗" in db.appended[0][2]



def test_ask_opens_a_screen_when_the_recipient_has_none(CH, monkeypatch, db):
    """对方还没有可对话会话：先开一条，问话、叫醒、答复都落这一条。"""
    monkeypatch.setattr(CH, "_roster", lambda: _roster_of("Nymo"))
    _stub_answer(monkeypatch, CH, ("好", 44))
    woken: dict = {}
    monkeypatch.setattr(
        CH.WK, "wake_agent",
        lambda entry, sid, **kw: woken.update(sid=sid) or (True, ""),
    )
    out = json.loads(CH.handle_ask({"to": "Nymo", "parts": ["在吗"]}))
    assert out["ok"] is True
    assert len(db.created) == 1 and db.created[0][2] == "Nymo"
    assert woken["sid"] == db.created[0][0]
    assert out["session_id"] == db.created[0][0]



def test_ask_still_counts_as_delivered_when_it_cannot_be_woken(CH, monkeypatch, db):
    """叫不醒 ≠ 没送到：问话在叫醒之前就落好了，delivered 必须为真。"""
    monkeypatch.setattr(CH, "_roster", lambda: _roster_of("Nymo"))
    db.existing = [{"session_id": "chan_nymo_screen"}]
    monkeypatch.setattr(CH.AA, "_session_max_message_id", lambda sid: 100)
    monkeypatch.setattr(CH.WK, "wake_agent", lambda *a, **kw: (False, "起手就退了"))
    out = json.loads(CH.handle_ask({"to": "Nymo", "parts": ["在吗"]}))
    assert out["ok"] is False and out["delivered"] is True, "话已经送到，这个事实不许被错误吞掉"
    assert len(db.appended) == 1 and db.appended[0][0] == "chan_nymo_screen"
    assert "起手就退" in json.dumps(out, ensure_ascii=False)



def test_ask_timeout_after_the_child_wrote_does_not_double_post(CH, monkeypatch, db):
    """叫醒了却没往那一屏写回答：如实报没答，且不许再补一条问话。"""
    monkeypatch.setattr(CH, "_roster", lambda: _roster_of("Nymo"))
    db.existing = [{"session_id": "chan_nymo_screen"}]
    _stub_answer(monkeypatch, CH)  # 空队列 ⇒ 地板之后没有新回答
    out = json.loads(CH.handle_ask({"to": "Nymo", "parts": ["在吗"]}))
    assert out["ok"] is False and out["delivered"] is True
    assert len(db.appended) == 1, "只许我们那一问，不许补第二遍"
    assert "没往那一屏写回答" in json.dumps(out, ensure_ascii=False)



def test_ask_reaches_a_non_project_row_the_role_gate_refuses(CH, monkeypatch, db):
    """断点①的回归：一个普通 L2（role=principal）say 发得出，ask 也必须问得到。
    旧形状两侧各判一次、名单差一行（差的正是 agenda），0.06 秒就被角色闸拒掉。"""
    monkeypatch.setattr(CH, "_roster", lambda: _roster_of("agenda", role="principal"))
    db.existing = [{"session_id": "chan_agenda_screen"}]
    _stub_answer(monkeypatch, CH, ("收到", 45))
    woken: list = []
    monkeypatch.setattr(
        CH.WK, "wake_agent",
        lambda entry, sid, **kw: woken.append(entry.name) or (True, ""),
    )
    out = json.loads(CH.handle_ask({"to": "agenda", "parts": ["早报排一下"]}))
    assert out["ok"] is True, "不该再被角色闸拒"
    assert woken == ["agenda"]



def test_ask_trace_records_the_wait(CH, monkeypatch, db, trace):
    """ask 的痕比 say 多记等了多久与答案。"""
    monkeypatch.setattr(CH, "_roster", lambda: _roster_of("Nymo"))
    db.existing = [{"session_id": "chan_nymo_screen"}]
    _stub_answer(monkeypatch, CH, ("好", 46))
    CH.handle_ask({"to": "Nymo", "parts": ["在吗"]})
    rows = trace()
    assert len(rows) == 1 and rows[0]["kind"] == "ask"
    assert "waited_seconds" in rows[0] and rows[0]["answer"] == "好"


def test_ask_never_replays_an_answer_from_before_the_question(CH, monkeypatch, db):
    """地板（since_id）必须带到取答案那一刻：地板之后没有新回答就报没答。

    「六问逐字相同」那串 11-24 长文就是拿上一轮的旧稿顶出来的——旧实现按
    ``ORDER BY id DESC LIMIT 1`` 取最后一条 assistant，没有「本次提问之后」这个下界。
    """
    monkeypatch.setattr(CH, "_roster", lambda: _roster_of("Nymo"))
    db.existing = [{"session_id": "chan_nymo_screen"}]
    monkeypatch.setattr(CH.AA, "_session_max_message_id", lambda sid: 9106)
    monkeypatch.setattr(CH.WK, "wake_agent", lambda *a, **kw: (True, ""))
    monkeypatch.setattr(CH, "_ask_wait_seconds", lambda: 0.01)
    seen: dict = {}

    def _answer(entry, session_id, since_id=0):
        seen["since_id"] = since_id
        return ("", 0)  # 旧稿被地板挡住，一条新的都没有

    monkeypatch.setattr(CH.AA, "_session_answer", _answer)
    out = json.loads(CH.handle_ask({"to": "Nymo", "parts": ["在吗"]}))
    assert seen["since_id"] == 9106, "取答案必须带本次提问的地板"
    assert out["ok"] is False
    assert "answer" not in out, "没有新回答就不许回放旧稿"



# ─── 同一只手：--resume 与署名 ─────────────────────────────────────────────


def test_build_child_command_resumes_before_the_subcommand(CH):
    """`--resume` 是顶层旗标（main.py 的 _TOP_LEVEL_VALUE_FLAGS），必须排在 `chat` 之前。"""
    from plobi.agents.registry import AgentEntry

    entry = AgentEntry.from_dict("Nymo", {"role": "principal", "category": "projects"})
    argv, _env, _cwd = CH.AA.build_child_command(entry, "问一句", resume_session="chan_abc")
    assert "--resume" in argv
    assert argv[argv.index("--resume") + 1] == "chan_abc"
    assert argv.index("--resume") < argv.index("chat"), argv
    plain, _, _ = CH.AA.build_child_command(entry, "问一句")
    assert "--resume" not in plain, "不指定就不该凭空加这一段"


def test_current_agent_name_reads_the_session_row_not_the_process_env(CH, monkeypatch, db):
    """断点③：一台后端跑多个会话（裁 180/168），os.environ 谁的值为先就是谁 ⇒ 署名串人。"""
    monkeypatch.setenv("PLOBI_AGENT_ID", "别的会话的身份")
    db.sessions = {"chan_l1": {"session_id": "chan_l1", "agent_id": ""}}
    assert CH.current_agent_name("chan_l1") == "别的会话的身份", "会话那行没落账时仍走兜底"
    db.sessions = {"chan_l1": {"session_id": "chan_l1", "agent_id": "Aura"}}
    assert CH.current_agent_name("chan_l1") == "Aura", "会话有身份就读会话，不吃进程环境"
    assert CH.current_agent_name("") == "别的会话的身份", "没有会话号时才退回进程环境"


# ─── 名册判据 ──────────────────────────────────────────────────────────────


def test_channel_available_follows_the_roster(CH):
    """名册里一条可发的人都没有 ⇒ 零 schema 足迹（Footprint Ladder rung 3）。"""
    assert CH.check_channel_available() is False or CH.check_channel_available() is True
    # 形状按 monkeypatch 版本钉（真名册由 test_l1_mouth_gate 那两条覆盖）
    original = CH.live_names
    try:
        CH.live_names = lambda: []
        assert CH.check_channel_available() is False
        CH.live_names = lambda: ["Nymo"]
        assert CH.check_channel_available() is True
    finally:
        CH.live_names = original
