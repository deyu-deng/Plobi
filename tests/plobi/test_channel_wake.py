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
PLUGIN_DIR = REPO / "plugins" / "plobi-core"


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

    def poll(self):
        """还活着——`Popen` 之后 `_verify_spawn` 要问这一句。"""
        return None


def _entry(name: str = "agenda"):
    from plobi.agents.registry import AgentEntry

    return AgentEntry.from_dict(name, {"role": "principal"})


@pytest.fixture()
def spawn(WK, tmp_path, monkeypatch):
    """把唤醒要碰的外部东西全指到 tmp；返回记录 Popen 的列表。"""
    monkeypatch.delenv("PLOBI_WAKE", raising=False)
    monkeypatch.setattr(WK, "_wake_dir", lambda: tmp_path / "wake")
    monkeypatch.setattr(WK, "_plobi_root", lambda: tmp_path)
    (tmp_path / "wake").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(WK, "_wake_log_path", lambda: tmp_path / "wake.log")

    starts: list[dict] = []

    def _fake_popen(argv, **kwargs):
        starts.append({"argv": argv, **kwargs})
        return _FakeProc()

    monkeypatch.setattr(WK.subprocess, "Popen", _fake_popen)
    # 观察窗在真机上要 6 秒，测试里缩到可以忽略——判据本身不变。
    monkeypatch.setattr(WK, "WAKE_VERIFY_SECONDS", 0.05)
    # 命令行可跑性（`plobi_cli.plobi_bin.plobi_argv_runnable`）在真机上靠 PATH，
    # 测试机不一定有 `plobi`。这里默认放行，专门那一枚用例自己改成 False。
    monkeypatch.setattr(WK, "_argv_runnable", lambda argv: True)
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
    monkeypatch.setattr(WK, "_plobi_root", lambda: tmp_path)
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


def test_a_host_exe_argv_is_refused_before_spawn(WK, spawn, monkeypatch, tmp_path):
    """宿主 exe + `-m` 这种形状没有解释器去读——不许起进程，更不许报成已叫醒。

    11-23 那五个假 `woke=true` 就是这么来的：argv[0] 是 Electron 壳，
    `CreateProcess` 照样成功，孩子几秒后带着 GPU cache 报错死掉。
    """
    monkeypatch.setattr(WK, "_argv_runnable", lambda argv: False)

    ok, note = WK.wake_agent(_entry(), "sess-9", sender="L1")

    assert ok is False, "起不来的命令不许报成「已叫醒」"
    assert "叫不醒" in note and "待重放" in note
    assert spawn == [], "判定不成立就不该起进程"
    pending = list((tmp_path / "pending" / "wake").glob("*.json"))
    assert len(pending) == 1, "叫不醒的这一条必须留待重放"
    payload = json.loads(pending[0].read_text(encoding="utf-8"))
    assert payload["agent"] == "agenda" and payload["session"] == "sess-9"
    assert payload["reason"], "待重放必须带上为什么没叫醒"


def test_a_child_that_dies_at_once_is_not_reported_as_woken(WK, spawn, monkeypatch, tmp_path):
    """`Popen` 返回 ≠ 活着。立刻退出的孩子必须报失败，并带上日志尾巴。

    这一枚是 11-23 假唤醒的正面回归：壳进程起来了、几秒内死，旧实现照样
    `return True, "已叫醒它"`，还把死 pid 写进 marker，让下一次唤醒判「过期」再假一遍。
    """

    class _DiesAtOnce:
        pid = 5150

        def poll(self):
            return 1

    def _fake_popen(argv, **kwargs):
        (tmp_path / "wake.log").write_text(
            "Unable to move the cache: 拒绝访问 (0x5)\nGpu Cache Creation failed: -2",
            encoding="utf-8",
        )
        return _DiesAtOnce()

    monkeypatch.setattr(WK.subprocess, "Popen", _fake_popen)

    ok, note = WK.wake_agent(_entry(), "sess-10", sender="L1")

    assert ok is False
    assert "退出码 1" in note and "拒绝访问" in note, "死因要能直接看出是什么把进程带走的"
    marker = tmp_path / "wake" / f"{WK._safe_slug('agenda')}.json"
    assert not marker.exists(), "没起来就不许留「它在跑」的标记"
    assert list((tmp_path / "pending" / "wake").glob("*.json")), "叫不醒的要留待重放"


def test_a_queued_wake_rides_along_and_is_cleared_once_it_starts(
    WK, spawn, monkeypatch, tmp_path
):
    """攒下的那条不许躺在磁盘上装死：这一趟要说给它听，真起来了才销账。"""
    monkeypatch.setattr(WK, "_argv_runnable", lambda argv: False)
    first_ok, _ = WK.wake_agent(_entry(), "sess-11", sender="L1")
    assert first_ok is False
    assert len(list((tmp_path / "pending" / "wake").glob("*.json"))) == 1

    monkeypatch.setattr(WK, "_argv_runnable", lambda argv: True)
    ok, _ = WK.wake_agent(_entry(), "sess-12", sender="L1")

    assert ok is True
    prompt = spawn[-1]["argv"][-1]
    assert "没被叫醒" in prompt, "待重放的那几条必须交代给被叫醒的这一轮"
    assert not list((tmp_path / "pending" / "wake").glob("*.json")), "真起来了就该销账"


def test_another_agents_queue_is_not_touched(WK, spawn, monkeypatch, tmp_path):
    """欠账是按人记的：叫醒乙不许把甲的那条销掉，也不许塞进乙的提示词。"""
    monkeypatch.setattr(WK, "_argv_runnable", lambda argv: False)
    WK.wake_agent(_entry("agenda"), "sess-13", sender="L1")

    monkeypatch.setattr(WK, "_argv_runnable", lambda argv: True)
    ok, _ = WK.wake_agent(_entry("namer"), "sess-14", sender="L1")

    assert ok is True
    assert len(list((tmp_path / "pending" / "wake").glob("*.json"))) == 1, "别人的账不许销"
    assert "没被叫醒" not in spawn[-1]["argv"][-1], "别人的欠账不关它的事"


class _FakeDB:
    def __init__(self):
        self.appended = []

    def latest_conversational_session_for_agent(self, agent_id):
        return "sess-window"

    def create_session(self, session_id, *, source=None, agent_id=None):
        return session_id

    def append_message(self, session_id, *, role=None, content=None, asked_by=None, kind=None, reply_to=None):
        self.appended.append((session_id, role, content, asked_by))
        return "m1"


def test_handle_say_hands_the_new_session_to_the_waker(CH, monkeypatch, tmp_path):
    """跨模块那一行：唤醒拿到的必须是刚投递的那一屏，不是随便一条。"""
    # 裁定 216 之后 say 会开授权票：这里指到 tmp，别写真家。
    monkeypatch.setenv("PLOBI_TICKET_DB", str(tmp_path / "tickets.db"))
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

    entry = AgentEntry.from_dict("agenda", {"role": "principal"})
    monkeypatch.setattr(CH, "_deliver_or_fail", lambda to, parts: (entry, "一句话", None))
    seen: list[tuple] = []

    def _fake_wake(e, sid, sender, **kwargs):
        seen.append((e.name, sid))
        return True, "ok"

    monkeypatch.setattr(CH.WK, "wake_agent", _fake_wake)

    out = json.loads(CH.handle_say({"to": "agenda", "parts": ["一句话"]}, session_id="caller-session"))

    assert out["ok"] is True
    assert out["session_id"] == "sess-window"
    assert seen == [("agenda", "sess-window")], "唤醒必须拿到刚投递的那一屏"


def test_a_failed_wake_does_not_turn_say_into_an_error(CH, monkeypatch, tmp_path):
    from plobi.agents.registry import AgentEntry

    monkeypatch.setenv("PLOBI_TICKET_DB", str(tmp_path / "tickets.db"))
    entry = AgentEntry.from_dict("agenda", {"role": "principal"})
    monkeypatch.setattr(CH, "_session_db", lambda: _FakeDB())
    monkeypatch.setattr(CH, "_trace", lambda record: None)
    monkeypatch.setattr(CH, "_deliver_or_fail", lambda to, parts: (entry, "一句话", None))
    monkeypatch.setattr(CH.WK, "wake_agent", lambda e, sid, sender, **kw: (False, "叫不醒它：没家"))

    out = json.loads(CH.handle_say({"to": "agenda", "parts": ["一句话"]}))

    assert out["ok"] is True, "话已经送到了，叫不醒不等于 say 失败"
    assert out["woke"] is False
    assert "没家" in out["wakeNote"]


def test_a_round_already_running_blocks_the_wake(WK, spawn, tmp_path):
    """cron 正在那一屏跑：不许再叫醒（两轮同屏＝两份钱 + 两条打架的历史）。"""
    import plobi_round_lock as RL

    held = RL.acquire("agenda", tmp_path, holder="cron:j1")
    try:
        ok, note = WK.wake_agent(_entry(), "sess-1", sender="L1")
        assert ok is False
        assert "正在跑另一轮" in note
        assert spawn == []
    finally:
        RL.release(held)


def test_a_failed_spawn_gives_the_round_back(WK, tmp_path, monkeypatch):
    import plobi_round_lock as RL

    monkeypatch.delenv("PLOBI_WAKE", raising=False)
    monkeypatch.setattr(WK, "_wake_dir", lambda: tmp_path / "wake")
    monkeypatch.setattr(WK, "_plobi_root", lambda: tmp_path)
    (tmp_path / "wake").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(WK, "_wake_log_path", lambda: tmp_path / "wake.log")
    monkeypatch.setattr(
        WK.AA, "build_child_command", lambda entry, prompt, asked_by="l1", resume_session=None: (["plobi"], {}, None)
    )

    def _boom(argv, **kwargs):
        raise OSError("no such binary")

    monkeypatch.setattr(WK.subprocess, "Popen", _boom)

    assert WK.wake_agent(_entry(), "sess-1", sender="L1")[0] is False

    again = RL.acquire("agenda", tmp_path)
    assert again is not None, "起不来必须把跑道还回去"
    RL.release(again)


def test_say_refuses_a_reply_to_that_is_not_in_that_screen(CH, monkeypatch):
    """引用一条不在那一屏里的消息：当场拒，不挂空引用。"""
    from plobi.agents.registry import AgentEntry

    entry = AgentEntry.from_dict("agenda", {"role": "principal"})

    class DB:
        def latest_conversational_session_for_agent(self, _a):
            return "sess-window"

        def get_messages(self, _sid):
            return [{"id": 1, "content": "在的"}]

        def append_message(self, *_a, **_k):
            raise AssertionError("不该写入")

    monkeypatch.setattr(CH, "_session_db", lambda: DB())
    monkeypatch.setattr(CH, "_trace", lambda record: None)
    monkeypatch.setattr(CH, "_deliver_or_fail", lambda to, parts: (entry, "话", None))

    out = json.loads(CH.handle_say({"to": "agenda", "parts": ["话"], "reply_to": "999"}))

    assert out["ok"] is False
    assert "找不到要引用的" in out["error"]


def test_say_carries_a_reply_to_that_exists(CH, monkeypatch):
    from plobi.agents.registry import AgentEntry

    entry = AgentEntry.from_dict("agenda", {"role": "principal"})
    seen: list[dict] = []

    class DB:
        def latest_conversational_session_for_agent(self, _a):
            return "sess-window"

        def get_messages(self, _sid):
            return [{"id": 4091, "content": "在的"}]

        def append_message(self, _sid, **kwargs):
            seen.append(kwargs)
            return "m-quote"

    monkeypatch.setattr(CH, "_session_db", lambda: DB())
    monkeypatch.setattr(CH, "_trace", lambda record: None)
    monkeypatch.setattr(CH, "_deliver_or_fail", lambda to, parts: (entry, "话", None))
    monkeypatch.setattr(CH.WK, "wake_agent", lambda *a, **k: (False, "测试里不叫醒"))

    out = json.loads(CH.handle_say({"to": "agenda", "parts": ["话"], "reply_to": "4091"}))

    assert out["ok"] is True
    assert seen and seen[-1]["reply_to"] == "4091"


def _live_screen(tmp_path, reply: dict):
    """摆一个「那一屏还开着」的现场：回环端口 + 一份 pid/port/token 登记。

    不 mock `_inline_ask` 而是真开一个 socket——这一条要验的正是
    「说不说得上话」，把那一层 mock 掉就等于什么都没验。
    """
    import socket as _socket
    import threading

    srv = _socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    heard: list[dict] = []

    def _serve() -> None:
        try:
            conn, _ = srv.accept()
            with conn:
                buf = b""
                while b"\n" not in buf:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                heard.append(json.loads(buf.split(b"\n", 1)[0].decode("utf-8")))
                conn.sendall((json.dumps(reply, ensure_ascii=False) + "\n").encode("utf-8"))
        finally:
            srv.close()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    folder = tmp_path / "wake" / "live-acp"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{os.getpid()}.json").write_text(
        json.dumps({"pid": os.getpid(), "port": port, "token": "tok-inline", "agent": "agenda"}),
        encoding="utf-8",
    )
    return heard, thread


def test_a_live_screen_takes_the_turn_instead_of_a_second_process(WK, spawn, tmp_path):
    """那一屏开着就让它自己跑——这话不能变成第二个进程，更不能变成两份钱。"""
    heard, thread = _live_screen(tmp_path, {"ok": True, "mode": "inline"})

    ok, note = WK.wake_agent(_entry(), "sess-42", sender="L1")
    thread.join(timeout=3)

    assert ok is True
    assert "原地接着干" in note
    assert spawn == [], "活着的屏接着跑，就不许再起一个进程"
    assert heard and heard[0]["session_id"] == "sess-42"
    assert heard[0]["token"] == "tok-inline"
    assert heard[0]["prompt"].startswith(
        WK.wake_prompt("L1", None)
    ), "内联与起进程两条腿的提示词必须同源"


def test_a_screen_that_does_not_own_the_session_still_gets_a_real_process(WK, spawn, tmp_path):
    """开着的屏不认这一屏（不是它的会话）⇒ 老实回话，照旧起进程，别硬塞。"""
    heard, thread = _live_screen(tmp_path, {"ok": False, "reason": "no_session"})

    ok, note = WK.wake_agent(_entry(), "sess-42", sender="L1")
    thread.join(timeout=3)

    assert heard, "先得问过那一屏"
    assert ok is True and "4321" in note
    argv = spawn[0]["argv"]
    assert argv[argv.index("--resume") + 1] == "sess-42"
