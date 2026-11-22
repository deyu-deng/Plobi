"""提权侧车（方案 B）的门与协议。

侧车最贵的两条性质都不是「能不能起来」，而是：

* **一个管理员进程的门得只有主人能进**——口令不对不许执行、不许回细节；
* **它不在的时候，主进程必须照旧能干普通权限那些活**——侧车是加分项，不是前提。

这两条在真机上很难复核（得有一台装了任务的机器、还得让它恰好不在），所以钉在这里：
口令/管道名的形状、`call` 塌成 `ok=False` 而不抛、`should_delegate` 的三个前提、
`_relay` 的四条分支，以及一次真的过管道的 `ping`。
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from agent.egress import engine, helper, helper_client


def _pipe(tag: str) -> str:
    """和 `helper_client.pipe_name` 同一形状的完整管道名。

    Windows 的 AF_PIPE 只认 `\\\\.\\pipe\\<名>` 这种全名：给裸名时 Listener 直接抛 OSError，
    侧车会以为自己「已经有同僚在岗」而安静退出（实测踩过，症状是 ready 永远不来）。
    """
    name = f"{helper_client.PIPE_PREFIX}-test-{tag}-{os.getpid()}"
    if sys.platform == "win32":
        return rf"\\.\pipe\{name}"
    return str(Path(tempfile.gettempdir()) / f"{name}.sock")


@pytest.fixture(autouse=True)
def _clean_globals():
    """每个用例前后都把进程级的两个开关复位。

    它们会让「这台机器上到底有没有侧车」这件事在用例之间串味（同一个进程跑整套测试），
    而串味的后果是别人那条测试莫名其妙地开始绕管道。
    """
    helper_client.use_pipe_name(None)
    helper_client.mark_serving(False)
    helper_client._last_start_attempt = 0.0
    yield
    helper_client.use_pipe_name(None)
    helper_client.mark_serving(False)
    helper_client._last_start_attempt = 0.0


@pytest.fixture
def home(tmp_path: Path) -> Path:
    (tmp_path / "egress").mkdir(parents=True, exist_ok=True)
    return tmp_path


# ── 管道名与口令 ────────────────────────────────────────────────────────────────


def test_pipe_name_is_stable_per_home_and_never_shared_between_two(tmp_path: Path):
    a = tmp_path / "a"
    b = tmp_path / "b"

    # 同一格家 = 同一个名字：主进程与侧车是靠这个名字相遇的，不稳定就谁也找不着谁。
    assert helper_client.pipe_name(a) == helper_client.pipe_name(a)
    # 两台 profile 各跑一份时不许互相串门：串门的后果是一个侧车替另一份家干活。
    assert helper_client.pipe_name(a) != helper_client.pipe_name(b)
    # 名字里不许出现 PLOBI_* 那种环境变量（AGENTS.md：不新增），只能由 home 算。
    assert helper_client.PIPE_PREFIX in helper_client.pipe_name(a)


def test_token_is_rewritten_every_time_and_read_back_clean(home: Path):
    first = helper_client.issue_token(home)
    second = helper_client.issue_token(home)

    # 32 字节 ⇒ 64 个十六进制字符。每次换新：上一次留下的那枚没有任何价值。
    assert len(first) == helper_client.TOKEN_BYTES * 2
    assert first != second
    # 落盘的是第二枚，且没有把换行带进内存里的那一份。
    assert helper_client.read_token(home) == second
    assert "\n" not in helper_client.read_token(home)


def test_no_token_file_reads_as_empty_not_as_an_exception(tmp_path: Path):
    # 「没口令」与「口令不对」在这里不区分，两者都连不上；区别只该在嘴上。
    assert helper_client.read_token(tmp_path / "nowhere") == ""


# ── call：不抛，只塌 ────────────────────────────────────────────────────────────


def test_call_without_a_token_says_so_and_does_not_raise(home: Path):
    reply = helper_client.call("ping", home=home, timeout=0.5)

    assert reply["ok"] is False
    assert reply["error"].startswith("sidecar-token-missing")
    # 人话那一格必须有内容：这句会直接出现在界面的悬停里。
    assert reply["said"]


def test_call_with_a_token_but_nobody_listening_is_unavailable(home: Path):
    helper_client.issue_token(home)
    helper_client.use_pipe_name(f"plobi-test-nobody-{os.getpid()}")

    reply = helper_client.call("ping", home=home, timeout=0.5)

    assert reply["ok"] is False
    assert reply["error"].startswith("sidecar-unavailable")
    # 失败也带耗时：界面要靠它分辨「连不上」与「连上了但慢」。
    assert "elapsed" in reply


def test_describe_never_raises_when_the_sidecar_is_absent(home: Path):
    helper_client.use_pipe_name(f"plobi-test-absent-{os.getpid()}")

    described = helper_client.describe(home=home)

    assert described["available"] is False
    assert described["token"] is False
    assert described["pipe"]


# ── should_delegate / start ────────────────────────────────────────────────────


def test_should_delegate_refuses_inside_the_sidecar_itself(monkeypatch):
    # 侧车自己调 engine.ensure 时若还想「转给侧车」，就是自己连自己、自己等自己：
    # 表现为一次谁也没动的挂起。这条开关是唯一的防递归手段。
    helper_client.mark_serving(True)
    monkeypatch.setattr(helper_client, "available", lambda **_: True)

    assert helper_client.should_delegate() is False


def test_should_delegate_is_false_when_nobody_answers(monkeypatch):
    monkeypatch.setattr(helper_client, "available", lambda **_: False)
    assert helper_client.should_delegate() is False

    monkeypatch.setattr(helper_client, "available", lambda **_: True)
    assert helper_client.should_delegate() is True


def test_start_gives_up_quietly_when_schtasks_is_missing(monkeypatch, home: Path):
    monkeypatch.setattr(helper_client, "available", lambda **_: False)

    def boom(*_a, **_k):
        raise OSError("schtasks.exe not found")

    monkeypatch.setattr(helper_client.subprocess, "run", boom)

    # 任务没注册（老版本装的、非 Windows 手动跑的）不是错误，只是没有那只手。
    assert helper_client.start(home=home) is False


def test_start_honours_its_cooldown(monkeypatch, home: Path):
    """每次保存网络设置都跑一遍 schtasks 会白白给操作加几百毫秒。"""
    monkeypatch.setattr(helper_client, "available", lambda **_: False)
    calls: list[int] = []

    class Done:
        returncode = 1

    monkeypatch.setattr(helper_client.subprocess, "run", lambda *a, **k: calls.append(1) or Done())

    assert helper_client.start(home=home, wait=0.0) is False
    assert len(calls) == 1
    # 冷却期里的第二次：连 schtasks 都不跑。
    assert helper_client.start(home=home, wait=0.0) is False
    assert len(calls) == 1


# ── 侧车那一侧：命令表与请求校验 ────────────────────────────────────────────────


def test_every_command_is_reachable_and_ping_reports_who_it_is(home: Path):
    assert "ping" in helper.commands()
    assert helper.commands() == tuple(sorted(helper.commands()))

    reply = helper.handle({"cmd": "ping"}, home=home)

    assert reply["ok"] is True
    assert reply["result"]["pid"] == os.getpid()
    # 「起了但没提权」要看得出来：这一格是登录触发的任务没拿到 HIGHEST 时唯一能看出问题的地方。
    assert isinstance(reply["result"]["admin"], bool)
    assert reply["result"]["protocol"] == helper_client.PROTOCOL_VERSION


def test_malformed_requests_are_rejected_without_touching_the_engine(home: Path):
    assert helper.handle("ping", home=home)["error"] == "request-not-a-dict"
    assert helper.handle({"cmd": "rm -rf"}, home=home)["error"].startswith("unknown-command")
    # 不是 dict 的参数是错；空 list 则是「没有参数」——`ping` 本来就不看参数，
    # 为这种形状回一条错只会让老客户端莫名其妙地失效。
    assert helper.handle({"cmd": "ping", "args": "nope"}, home=home)["error"] == "args-not-a-dict"
    assert helper.handle({"cmd": "ping", "args": []}, home=home)["ok"] is True


def test_a_command_that_raises_does_not_take_the_sidecar_down(monkeypatch, home: Path):
    def boom(*_a, **_k):
        raise RuntimeError("引擎起不来")

    monkeypatch.setitem(helper._COMMANDS, "engine.ensure", boom)

    reply = helper.handle({"cmd": "engine.ensure"}, home=home)

    # 一条命令炸了只回 ok=False：侧车炸掉的话，用户下一次拨开关就没有手可用了。
    assert reply["ok"] is False
    assert "引擎起不来" in reply["error"]


# ── 真的过一遍管道 ──────────────────────────────────────────────────────────────


@pytest.mark.skipif(not helper_client.supported(), reason="这个平台上没有这条线")
def test_a_real_ping_over_the_pipe(home: Path):
    pipe = _pipe("live")
    helper_client.use_pipe_name(pipe)
    ready = threading.Event()
    exit_code: list[int] = []

    def run() -> None:
        exit_code.append(helper.serve(home=home, pipe=pipe, ready=lambda _info: ready.set(),
                                      idle_s=0.0, max_requests=1))

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    assert ready.wait(10), "侧车在 10 秒内没能就绪"

    reply = helper_client.ping(home=home, timeout=5.0)

    worker.join(10)
    assert reply["ok"] is True, reply
    assert reply["result"]["pipe"] == pipe
    assert exit_code and exit_code[0] == helper.EXIT_OK


@pytest.mark.skipif(not helper_client.supported(), reason="这个平台上没有这条线")
def test_a_wrong_token_is_refused_before_any_command_runs(home: Path):
    """口令是这条线上唯一的准入：不对就断，一个字节的 pickle 都不解。"""
    pipe = _pipe("auth")
    helper_client.use_pipe_name(pipe)
    ready = threading.Event()
    ran: list[str] = []

    def note(*_a, **_k):
        ran.append("called")
        return {}

    monkey_like = helper._COMMANDS["ping"]
    helper._COMMANDS["ping"] = note
    try:
        worker = threading.Thread(
            target=lambda: helper.serve(home=home, pipe=pipe, ready=lambda _i: ready.set(),
                                        idle_s=0.0, max_requests=2),
            daemon=True,
        )
        worker.start()
        assert ready.wait(10)
        # 侧车握的是新口令；这里故意把手上那枚改成别的。
        token_path = helper_client.token_path(home)
        token_path.write_text("00" * helper_client.TOKEN_BYTES + "\n", encoding="utf-8")

        reply = helper_client.call("ping", home=home, timeout=5.0)

        assert reply["ok"] is False
        assert "sidecar-auth-failed" in reply["error"] or "sidecar-unavailable" in reply["error"]
        assert ran == [], "口令不对时那条命令不许被执行"
    finally:
        helper._COMMANDS["ping"] = monkey_like
        helper_client.use_pipe_name(None)


# ── engine._relay：交不交、交失败怎么办 ──────────────────────────────────────────


def _table():
    from agent.egress.rules import empty_table

    return empty_table()


def test_relay_falls_back_to_this_process_when_the_sidecar_cannot_be_woken(monkeypatch, home: Path):
    monkeypatch.setattr(helper_client, "should_delegate", lambda **_: False)
    monkeypatch.setattr(helper_client, "start", lambda **_: False)

    # None = 「本进程照旧干」。没有提权也照样能起 mixed 端口引擎，TUN 那条路才走不通。
    assert engine._relay("engine.ensure", home=home) is None


def test_relay_does_not_retry_what_the_sidecar_already_failed_at(monkeypatch, home: Path):
    """它可能已经停掉旧引擎、写了记录；本进程再干一遍就是两个进程抢一个端口。"""
    monkeypatch.setattr(helper_client, "should_delegate", lambda **_: True)
    monkeypatch.setattr(
        helper_client,
        "call",
        lambda *a, **k: {"ok": False, "error": "PermissionError: TUN needs elevation", "result": None},
    )

    report = engine._relay("engine.ensure", home=home)

    assert report is not None
    assert report.state == engine.FAILED
    assert "引擎" in report.detail or "TUN" in report.detail


def test_relay_falls_back_only_when_the_handshake_never_happened(monkeypatch, home: Path):
    monkeypatch.setattr(helper_client, "should_delegate", lambda **_: True)
    monkeypatch.setattr(
        helper_client,
        "call",
        lambda *a, **k: {"ok": False, "error": "sidecar-unavailable: OSError: gone", "result": None},
    )

    assert engine._relay("engine.ensure", home=home) is None


def test_relay_hands_the_report_back_when_the_sidecar_did_the_work(monkeypatch, home: Path):
    monkeypatch.setattr(helper_client, "should_delegate", lambda **_: True)
    monkeypatch.setattr(
        helper_client,
        "call",
        lambda *a, **k: {"ok": True, "error": "", "result": {"state": engine.OURS, "detail": "by sidecar",
                                                             "mixed_port": 7895}},
    )

    report = engine._relay("engine.ensure", home=home)

    assert report is not None
    assert report.state == engine.OURS
    assert report.mixed_port == 7895
