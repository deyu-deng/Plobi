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
import os
from pathlib import Path

import pytest

from plobi.collectors.chatlog import process, pipeline

CHATLOG_DIR = Path(__file__).resolve().parents[2] / "plobi" / "collectors" / "chatlog"

#: 归属登记落点的**真身**（conftest 会把 `owner_path` 钉进临时区；这一份在钉之前就抓下来）。
_REAL_OWNER_PATH = process.owner_path


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


# --------------------------------------------------------------------------- #
# ⑧ 跨会话的归属：上一轮留下的那个不许变成永久常驻
# --------------------------------------------------------------------------- #
#
# 漏在哪（2026-11-20 实测）：腿起的那一份是**后端的子进程**；后端一死（桌面退出），
# Windows 上子进程不会跟着走 ⇒ `:5030` 上留下一个孤儿。下一个会话的腿探到「有人在应答」
# ⇒ 按裁定 105 当成**别人的**⇒ 借它、一辈子不收 ⇒ 「用完即收」静默地不成立了。
#
# 收法：**归属登记**（谁起的这棵、主人是谁）⇒ 主人没了就**认领** ⇒ 这一轮收掉。


def _write_record(tree_pid: int, owner_pid: int) -> None:
    """造一份归属登记（走产品自己的写入口，不手搓 JSON）。"""
    process._write_owner(tree_pid, owner_pid)


def _patch_probes(monkeypatch, *, tree_alive: bool, owner_alive: bool, is_ours: bool) -> None:
    """把三个探针钉成假件：pid 活不活、主人活不活、命令行是不是我们那一棵。"""
    monkeypatch.setattr(
        process, "_pid_alive",
        lambda pid: tree_alive if pid == 4242 else owner_alive,
    )
    monkeypatch.setattr(process, "_is_our_tree", lambda pid, created=None: is_ours)


def test_the_owner_record_is_written_when_we_start_it(monkeypatch):
    """起它 ⇒ 记下「这棵树的 pid ＋ 起它的那个 pid」——下一个会话认得出它。"""
    client = _FakeClient(healthy=False)
    monkeypatch.setattr(process, "spawn_process", lambda: 4242)
    monkeypatch.setattr(process, "_wait_ready", lambda c, timeout: True)

    process.ensure_process(client)

    record = process._read_owner()
    assert record is not None, "起完不登记 ⇒ 下一个会话认不出来"
    assert record.tree_pid == 4242
    assert record.owner_pid == os.getpid(), "主人是**起它的这个进程**"


def test_an_orphan_from_a_dead_session_is_adopted(lock_home, monkeypatch):
    """主人没了、它还活着、而且确实是 chatlog 那一棵 ⇒ **认领它**（这一轮收掉）。"""
    _write_record(tree_pid=4242, owner_pid=999999)
    _patch_probes(monkeypatch, tree_alive=True, owner_alive=False, is_ours=True)
    killed: list[int] = []
    monkeypatch.setattr(process, "kill_tree", lambda pid: killed.append(pid))

    handle, _note = process.ensure_process(_FakeClient(healthy=True))

    assert handle is not None
    assert handle.started_by_us is True, "那是我们上一轮留下的 ⇒ 该认领"
    assert handle.pid == 4242

    process.release_process(handle)
    assert killed == [4242], "认领来的那个，这一轮用完收掉"


def test_a_live_owner_is_never_adopted(lock_home, monkeypatch):
    """主人还活着 ⇒ 那是**别人正在用的**⇒ 借，一动不动（裁定 105）。"""
    _write_record(tree_pid=4242, owner_pid=999999)
    _patch_probes(monkeypatch, tree_alive=True, owner_alive=True, is_ours=True)
    killed: list[int] = []
    monkeypatch.setattr(process, "kill_tree", lambda pid: killed.append(pid))

    handle, _note = process.ensure_process(_FakeClient(healthy=True))

    assert handle is not None
    assert handle.started_by_us is False, "主人还活着，不许认领"
    process.release_process(handle)
    assert killed == [], "借来的那个不许动"


def test_a_recycled_pid_is_never_adopted(lock_home, monkeypatch):
    """登记里那个 pid 被**别的进程**占用了 ⇒ 不认领（pid 复用的护栏）。

    认错人 ⇒ 会去杀一个与我们无关的进程。命令行对不上 ⇒ 当作不是我们的。
    """
    _write_record(tree_pid=4242, owner_pid=999999)
    _patch_probes(monkeypatch, tree_alive=True, owner_alive=False, is_ours=False)
    killed: list[int] = []
    monkeypatch.setattr(process, "kill_tree", lambda pid: killed.append(pid))

    handle, _note = process.ensure_process(_FakeClient(healthy=True))

    assert handle is not None
    assert handle.started_by_us is False, "命令行不是 chatlog ⇒ 不许认领"
    process.release_process(handle)
    assert killed == []


def test_an_unreadable_probe_means_no_adoption(lock_home, monkeypatch):
    """探针读不到（psutil 不在、权限不够）⇒ **不认领**——宁可让它多活一会儿。"""
    _write_record(tree_pid=4242, owner_pid=999999)
    monkeypatch.setattr(process, "_pid_alive", lambda pid: None)
    monkeypatch.setattr(process, "_is_our_tree", lambda pid: None)
    killed: list[int] = []
    monkeypatch.setattr(process, "kill_tree", lambda pid: killed.append(pid))

    handle, _note = process.ensure_process(_FakeClient(healthy=True))

    assert handle is not None
    assert handle.started_by_us is False, "读不到就不许认领"
    process.release_process(handle)
    assert killed == []


def test_releasing_clears_the_owner_record(monkeypatch):
    """收掉自己那个 ⇒ 那份登记也清掉（别留一条指向死 pid 的账）。"""
    _write_record(tree_pid=4242, owner_pid=os.getpid())
    monkeypatch.setattr(process, "kill_tree", lambda pid: None)

    process.release_process(process.ProcessHandle(True, 4242, "自己起的"))

    assert process._read_owner() is None, "收掉了就得把登记也带走"


def test_a_stale_orphan_is_reaped_before_a_new_one_starts(lock_home, monkeypatch):
    """没人应答、登记里还留着一个我们上一轮留下的 ⇒ 先收掉它，再起我们自己的。"""
    _write_record(tree_pid=4242, owner_pid=999999)
    _patch_probes(monkeypatch, tree_alive=True, owner_alive=False, is_ours=True)
    order: list[str] = []
    monkeypatch.setattr(process, "kill_tree", lambda pid: order.append(f"kill:{pid}"))
    monkeypatch.setattr(process, "spawn_process", lambda: order.append("spawn") or 5151)
    monkeypatch.setattr(process, "_wait_ready", lambda c, timeout: True)

    handle, _note = process.ensure_process(_FakeClient(healthy=False))

    assert handle is not None and handle.pid == 5151
    assert order == ["kill:4242", "spawn"], "先收掉上一轮留下的，再起新的"


def test_the_owner_record_lives_in_the_data_home(monkeypatch):
    """归属登记落在**数据家**底下——不落 C 盘（`D:\\_POLICY`）。"""
    from plobi_constants import get_plobi_home

    # 把 conftest 钉的那一份换回**产品真身**再问它落点（钉住的是临时区，形状看不出来）。
    monkeypatch.setattr(process, "owner_path", _REAL_OWNER_PATH)

    path = process.owner_path()

    assert path == get_plobi_home() / "chatlog" / "collect.owner.json"
    assert path.name == "collect.owner.json"


# --------------------------------------------------------------------------- #
# ⑨ 退出时把自己起的那几棵带走
# --------------------------------------------------------------------------- #
#
# 归属登记管的是「**崩了**留下的」；正常退出这一格由 atexit 管。
# 两条合起来，「用完即收」才在**所有**退出路径上成立——只做前者，桌面退出那一瞬间还漏着。
#
# 与浏览器那一族（`tools/browser_tool.py` 的 owner_pid ＋ orphan reaper）**同一个形状**。


def test_exit_takes_our_own_trees_with_it(monkeypatch):
    """本进程退出 ⇒ 自己起的那几棵**跟着走**（Windows 上子进程不会自己死）。"""
    killed: list[int] = []
    monkeypatch.setattr(process, "kill_tree", lambda pid: killed.append(pid))
    monkeypatch.setattr(process, "_is_our_tree", lambda pid, created=None: True)
    monkeypatch.setattr(process, "_OWNED_TREES", {4242: None, 5151: None})

    process._reap_our_own_on_exit()

    assert sorted(killed) == [4242, 5151]


def test_exit_does_not_touch_a_pid_that_is_not_ours(monkeypatch):
    """pid 会被复用 ⇒ 身份对不上就**不动它**（别在退出路上杀掉一个无关进程）。"""
    killed: list[int] = []
    monkeypatch.setattr(process, "kill_tree", lambda pid: killed.append(pid))
    monkeypatch.setattr(process, "_is_our_tree", lambda pid, created=None: False)
    monkeypatch.setattr(process, "_OWNED_TREES", {4242: None})

    process._reap_our_own_on_exit()

    assert killed == [], "身份对不上就不许动"


def test_the_exit_hook_is_registered():
    """atexit 那一格得真的挂着——不然「正常退出带走」只是纸上的话。"""
    source = inspect.getsource(process)

    assert "atexit.register(_reap_our_own_on_exit)" in source, "退出前那一手没挂上"
    assert "import atexit" in source
