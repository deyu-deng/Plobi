"""微信腿自己管它的采集进程：一轮采集＝一个会话（**起 → 采 → 收**）。

钉住的是**归属**，不是行为细节：

- **自己起的那一个，这一轮结束就收掉**（不留常驻）；
- **借来的那个一动不动**（不是自己起的，不许乱杀——裁定 105）；
- **收只收自己那个 PID**（按 PID 杀它那棵进程树，**不按映像名全局杀**）；
- **「消息在手」那一路不起进程**（消息已经在手，不需要那个进程）。

还有两条是**读源码的守卫**：行为测试证不了「没有别的路」——
全仓除了这一格没有第二处拉起那个采集进程；watchdog 那一族没有自愈了。
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from plobi.collectors.chatlog import process, pipeline

CHATLOG_DIR = Path(__file__).resolve().parents[2] / "plobi" / "collectors" / "chatlog"


class _FakeClient:
    """那个 HTTP 客户端的假身：只答「在不在应答」。"""

    def __init__(self, healthy: bool = False):
        self.up = healthy
        self.calls = 0

    def healthy(self) -> bool:
        self.calls += 1
        return self.up


@pytest.fixture()
def lock_home(tmp_path, monkeypatch):
    """起停锁落到临时目录——不许往真家写。"""
    path = tmp_path / "collect.lock"
    monkeypatch.setattr(process, "lock_path", lambda: path)
    return path


# --------------------------------------------------------------------------- #
# ① 自己起的那一个，这一轮结束就收掉
# --------------------------------------------------------------------------- #


def test_release_stops_the_one_we_started(monkeypatch):
    handle = process.ProcessHandle(True, 4242, "自己起的")
    killed: list[int] = []
    monkeypatch.setattr(process, "kill_tree", lambda pid: killed.append(pid))

    process.release_process(handle)

    assert killed == [4242], "自己起的那一个必须被收掉"


def test_ensure_starts_one_when_nobody_is_listening(lock_home, monkeypatch):
    client = _FakeClient(healthy=False)
    spawned: list[int] = []
    monkeypatch.setattr(process, "spawn_process", lambda: spawned.append(4242) or 4242)
    monkeypatch.setattr(process, "_wait_ready", lambda c, timeout: True)

    handle, _note = process.ensure_process(client)

    assert handle is not None
    assert handle.started_by_us is True
    assert handle.pid == 4242
    assert spawned == [4242], "没人应答时该自己起一个"


def test_ensure_gives_up_when_it_never_answers(lock_home, monkeypatch):
    """起来了但一直没应答 ⇒ **收掉它**，不许留一个半死不活的在那儿占着端口。"""
    client = _FakeClient(healthy=False)
    killed: list[int] = []
    monkeypatch.setattr(process, "spawn_process", lambda: 4242)
    monkeypatch.setattr(process, "_wait_ready", lambda c, timeout: False)
    monkeypatch.setattr(process, "kill_tree", lambda pid: killed.append(pid))

    handle, note = process.ensure_process(client)

    assert handle is None
    assert note
    assert killed == [4242], "起不来应答的那个要收掉，不能留在那儿"


# --------------------------------------------------------------------------- #
# ② 借来的那个一动不动
# --------------------------------------------------------------------------- #


def test_a_borrowed_process_is_never_stopped(monkeypatch):
    killed: list[int] = []
    monkeypatch.setattr(process, "kill_tree", lambda pid: killed.append(pid))

    process.release_process(process.ProcessHandle(False, 0, "已经在跑，借它"))

    assert killed == [], "借来的那个不是我们起的，不许动它"


def test_ensure_borrows_when_somebody_is_already_listening(lock_home, monkeypatch):
    def _boom():
        raise AssertionError("有人在应答时不该再起一个")

    monkeypatch.setattr(process, "spawn_process", _boom)

    handle, _note = process.ensure_process(_FakeClient(healthy=True))

    assert handle is not None
    assert handle.started_by_us is False, "那不是我们起的"


# --------------------------------------------------------------------------- #
# ③ 收只收自己那个 PID（不按映像名全局杀）
# --------------------------------------------------------------------------- #


def test_kill_tree_targets_one_pid_not_an_image_name():
    """按 PID 杀它那棵进程树——**不许按映像名全局杀**（裁定 105：谁起的都杀不得）。"""
    source = inspect.getsource(process.kill_tree)

    assert "/IM" not in source, "按映像名全局杀已经作废，不许回来"
    assert "taskkill" in source and "/PID" in source, "只杀自己那个 PID"


# --------------------------------------------------------------------------- #
# ④ 「消息在手」那一路不起进程
# --------------------------------------------------------------------------- #


def test_the_in_hand_branch_never_starts_the_process():
    """消息已经在手 ⇒ 不探活、不起进程——那正是它「不探活」那条口径的由来。"""
    source = inspect.getsource(pipeline.ChatlogPipeline.collect)
    branch = source.split("if records:", 1)[1].split("else:", 1)[0]

    assert "ensure_process" not in branch, "消息在手那一路不许起那个进程"
    assert "_ingest_records" in branch


# --------------------------------------------------------------------------- #
# ⑤ 全仓没有第二处拉起那个采集进程
# --------------------------------------------------------------------------- #


def test_only_this_module_starts_the_collect_process():
    """微信这一族里，除了这一格，没有第二处拉起那个采集进程。

    读源码的守卫：行为测试证不了「没有别的路」。

    范围只到**这一族**：桌面开机那一份（`apps/desktop/electron/main.ts`）还在拉它，
    那一半排队给桌面那两格（`WP-SOURCE-FIRST-RUN-IMPORT`／`WP-AGENT-SINGLE-GATEWAY`
    在派认领）——见纸上 §2.1 第 3 条。
    """
    offenders: list[str] = []
    for path in sorted(CHATLOG_DIR.glob("*.py")):
        if path.name == "process.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "chatlog_server.ps1" in text or "spawn_process(" in text:
            offenders.append(path.name)

    assert offenders == [], f"采集进程只该由 process.py 起，别处也起了：{offenders}"


def test_the_public_layer_no_longer_starts_it():
    """**公共层不再有 spawn 代码**——它只照声明喊这一族自己的手。

    公共层若还自己拉，就成了**第二个家长**：怎么起、起哪个、收不收，两处各写一份 ⇒
    两处真源（2026-11-20 `WP-WECHAT-LEG-OWNS-PROCESS`）。
    """
    common = (CHATLOG_DIR.parent / "sidecar.py").read_text(encoding="utf-8")

    assert "subprocess" not in common, "公共层不许再自己拉进程"
    assert "Popen" not in common, "公共层不许再自己拉进程"
    assert "sidecar_start" in common, "它只照声明喊那一声"


# --------------------------------------------------------------------------- #
# ⑥ 用户刚打开这一条时那一手：起它，并让它留在那儿
# --------------------------------------------------------------------------- #


def test_activate_starts_it_and_leaves_it(lock_home, monkeypatch):
    """用户 10-23：「我打开还是关闭都是显示未连接」⇒ 开关说了"开"，就得说得出它活没活。

    这一手起完**不收**：它活到用户关掉这一条为止——与「采集那一轮用完即收」是两件事，
    别拿后一条把它连坐了。
    """
    client = _FakeClient(healthy=False)
    monkeypatch.setattr(process, "spawn_process", lambda: 4242)
    monkeypatch.setattr(process, "_wait_ready", lambda c, timeout: True)
    killed: list[int] = []
    monkeypatch.setattr(process, "kill_tree", lambda pid: killed.append(pid))

    ok, note = process.activate(client)

    assert ok and note
    assert killed == [], "这一手起的那一个留在那儿，不收"


# --------------------------------------------------------------------------- #
# ⑦ 那一轮什么时候才起它
# --------------------------------------------------------------------------- #


def _pipeline_with_stubbed_health(monkeypatch, health: dict):
    """一份只用来验「起不起它」的管线（其它那一族的东西全是假的）。"""
    from types import SimpleNamespace

    from plobi.collectors.chatlog import pipeline as pipeline_module
    from plobi.collectors.chatlog.config import CollectorConfig

    pipe = pipeline_module.ChatlogPipeline(
        config=CollectorConfig(), client=SimpleNamespace(), service=SimpleNamespace(),
        seen=SimpleNamespace(), talkers=SimpleNamespace(), confirmer=SimpleNamespace(),
    )
    monkeypatch.setattr(pipeline_module.ChatlogPipeline, "health", lambda self: health)
    return pipe


def test_the_round_starts_it_when_only_the_process_is_missing(monkeypatch):
    """前置三步都齐了、只是它没在跑 ⇒ 这一轮**自己起它**，采完**收掉**。"""
    from plobi.collectors.chatlog import pipeline as pipeline_module

    released: list[object] = []
    monkeypatch.setattr(pipeline_module.process, "ensure_process",
                        lambda client: (process.ProcessHandle(True, 4242, "自己起的"), "自己起的"))
    monkeypatch.setattr(pipeline_module.process, "release_process", lambda handle: released.append(handle))
    monkeypatch.setattr(pipeline_module.ChatlogPipeline, "run_once",
                        lambda self, **kw: pipeline_module.IngestReport(scanned=1))

    pipe = _pipeline_with_stubbed_health(monkeypatch, {
        "ok": False, "detail": "5030 上没人在应答", "state": "not_listening", "binary": None})

    result = pipe.collect()

    assert result["rows_seen"] == 1
    assert len(released) == 1, "自己起的那一个，这一轮结束就收掉"


def test_the_round_does_not_start_it_when_the_key_is_missing(monkeypatch):
    """缺密钥 ⇒ **起它也没用**（引擎拿不到密钥会自己退）——白等一轮不如报那一句真话。"""
    from plobi.collectors.chatlog import pipeline as pipeline_module

    def _boom(*args, **kwargs):
        pytest.fail("前提没齐就不该起它")

    monkeypatch.setattr(pipeline_module.process, "ensure_process", _boom)

    pipe = _pipeline_with_stubbed_health(monkeypatch, {
        "ok": False, "detail": "微信的数据密钥还没拿到", "state": "key_missing", "binary": None})

    with pytest.raises(pipeline_module.ChatlogDead):
        pipe.collect()


def test_the_watchdog_no_longer_self_heals():
    """看门狗没有「自愈」了：那一轮自带起停，没有「让它常驻」这回事。"""
    source = (CHATLOG_DIR / "watchdog.py").read_text(encoding="utf-8")

    assert "chatlog_server.ps1" not in source, "自愈那一格退役了"
    assert "attempt_heal_once" not in source
    assert "heal_command" not in source


def test_the_watchdog_judges_the_round_not_the_process():
    """健康判据＝**那一轮采到没有**，不是「那个进程在不在」。"""
    source = (CHATLOG_DIR / "watchdog.py").read_text(encoding="utf-8")

    assert 'outcome.get("ok")' in source, "判据要是那一轮的结果"
