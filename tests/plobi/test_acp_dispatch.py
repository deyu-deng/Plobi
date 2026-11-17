"""**ACP 派活腿**（11-16）：纯 ACP 客席怎么把一段活接过去。

这一档对着一台**假 ACP server** 跑（真厂商那 8 家里本机一家都没凭据，
真跑必然卡在「它自己说没登录」⇒ 机制只能对假服务器验）。

假服务器照 ACP 的规矩说话（现读官方 SDK 的 ``schema/v1/schema.json``）：

* ``initialize`` → 答；
* ``session/new`` → 答一条会话（**带它自己的档**）；
* ``session/set_mode`` → 答（只有 ``_meta``）；
* ``session/prompt`` → 先发 ``session/update`` 通知（它说的话），
  必要时发 ``session/request_permission``（要授权），最后答 ``stopReason``。

**判据**：协议次序、档怎么切、它说的话怎么收回来、它问授权时我们答不答、
它自己拒了/它自己说没登录时我们原样把话带出去。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent import external_offload as offload  # noqa: E402
from agent import vendor_registry as vr  # noqa: E402

#: 一台**假 ACP server**（照协议说话；行为由 ``FAKE_ACP_CONFIG`` 摆布）。
FAKE_SERVER = r'''
import json
import os
import sys

CONFIG = json.loads(os.environ.get("FAKE_ACP_CONFIG") or "{}")
SESSION_ID = CONFIG.get("session_id", "sess-1")


def send(payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def log(name, body):
    path = CONFIG.get(name)
    if not path:
        return
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)


#: 子进程**看到**的那个环境变量（判据：官方注册表写明要带的那几个有没有真带过去）。
log("env_log", os.environ.get("PLOBI_TEST_ACP_ENV", ""))


while True:
    line = sys.stdin.readline()
    if not line:
        break
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    method = msg.get("method")
    ident = msg.get("id")
    calls = CONFIG.get("calls_log")
    if calls and method:
        with open(calls, "a", encoding="utf-8") as handle:
            handle.write(str(method) + "\n")
    if method is None:
        # 这是客户端对我们那个授权问句的答复。
        log("answer_log", json.dumps(msg, ensure_ascii=False))
        continue
    if method == "initialize":
        # 它**自己宣告**能不能续接（11-17：真厂商那几家宣告 loadSession）。
        caps = {"loadSession": True} if CONFIG.get("load_session") else {}
        send({"jsonrpc": "2.0", "id": ident, "result": {
            "protocolVersion": 1, "agentCapabilities": caps}})
    elif method == "session/new":
        if CONFIG.get("session_error"):
            send({"jsonrpc": "2.0", "id": ident, "error": CONFIG["session_error"]})
            continue
        result = {"sessionId": SESSION_ID}
        modes = CONFIG.get("modes")
        if modes is not None:
            result["modes"] = {
                "currentModeId": CONFIG.get("current", ""),
                "availableModes": modes,
            }
        send({"jsonrpc": "2.0", "id": ident, "result": result})
    elif method == "session/load":
        # 续接那一发：记下我们发了什么，回**同一个**会话号。
        params = msg.get("params") or {}
        log("load_log", json.dumps(params, ensure_ascii=False))
        if CONFIG.get("load_error"):
            send({"jsonrpc": "2.0", "id": ident, "error": CONFIG["load_error"]})
            continue
        send({"jsonrpc": "2.0", "id": ident, "result": {
            "sessionId": params.get("sessionId") or SESSION_ID}})
    elif method == "session/set_mode":
        log("mode_log", json.dumps(msg.get("params") or {}, ensure_ascii=False))
        send({"jsonrpc": "2.0", "id": ident, "result": {}})
    elif method == "session/prompt":
        log("prompt_log", json.dumps(msg.get("params") or {}, ensure_ascii=False))
        if CONFIG.get("prompt_error"):
            send({"jsonrpc": "2.0", "id": ident, "error": CONFIG["prompt_error"]})
            continue
        if CONFIG.get("write_file"):
            with open(CONFIG["write_file"], "w", encoding="utf-8") as handle:
                handle.write("产物")
        send({"jsonrpc": "2.0", "method": "session/update", "params": {
            "sessionId": SESSION_ID,
            "update": {"sessionUpdate": "agent_message_chunk",
                       "content": {"type": "text", "text": CONFIG.get("say", "OK")}}}})
        if CONFIG.get("ask_permission"):
            send({"jsonrpc": "2.0", "id": 9001, "method": "session/request_permission",
                  "params": {"sessionId": SESSION_ID,
                             "toolCall": {"toolCallId": "t1", "title": "写文件"},
                             "options": [
                                 {"optionId": "allow", "name": "准", "kind": "allow_once"},
                                 {"optionId": "reject", "name": "不准", "kind": "reject_once"}]}})
            answer = sys.stdin.readline()
            log("answer_log", answer)
        send({"jsonrpc": "2.0", "id": ident, "result": {
            "stopReason": CONFIG.get("stop_reason", "end_turn"),
            "usage": CONFIG.get("usage", {"inputTokens": 3, "outputTokens": 2})}})
'''


GLM_MODES = [
    {"id": "default", "name": "Ask for permission"},
    {"id": "accept_edits", "name": "Auto-approve edits"},
    {"id": "bypass_permissions", "name": "Bypass all permissions"},
]


def _install_fake_server(tmp_path: Path, monkeypatch, config: dict) -> dict:
    """把通道指向一台**假 ACP server**；回一份「它刚才记了什么」的空壳。"""
    script = tmp_path / "fake_acp_server.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    monkeypatch.setenv("FAKE_ACP_CONFIG", json.dumps(config, ensure_ascii=False))
    monkeypatch.setattr(offload, "resolve_command", lambda agent="glm": str(script))
    monkeypatch.setattr(
        offload, "spawn_argv",
        lambda argv: [sys.executable, str(script), *list(argv)[1:]],
    )
    # 出口这一格与本档无关（也不该让测试去连网）。
    monkeypatch.setattr(offload, "resolve_proxy", lambda: ("", "none"))
    return {"script": script}


@pytest.fixture
def workdir(tmp_path):
    scratch = tmp_path / "offload"
    scratch.mkdir()
    return scratch


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


# ── 续接（11-17）────────────────────────────────────────────────────────

def test_resuming_goes_through_session_load_and_never_creates_a_new_session(
    tmp_path, workdir, monkeypatch,
):
    """给了会话号 ⇒ **续接**那条，不新建（协议级的 ``session/load``）。

    11-17 真跑：opencode／deveco／kilo 的 ACP server 都宣告 ``loadSession``，
    续接后它**记得**上一轮说过什么。
    """
    calls = tmp_path / "calls.txt"
    _install_fake_server(tmp_path, monkeypatch, {
        "say": "接着做完了",
        "load_session": True,
        "calls_log": str(calls),
    })

    result = offload.run_task("接着干", cwd=workdir, agent="glm", session_id="sess-old")

    assert result.ok, result.error
    assert result.text == "接着做完了"
    # 续的是**给的那一条**，不是新开的一条。
    assert result.external_session == "sess-old"
    # 台账那一格分得开：续接 ≠ 新建。
    assert result.external_session_source == "acp.load"
    sent = calls.read_text(encoding="utf-8").split()
    assert "session/load" in sent
    assert "session/new" not in sent


def test_a_server_that_cannot_load_refuses_honestly(tmp_path, workdir, monkeypatch):
    """服务器没宣告 ``session/load`` ⇒ **如实报错**，不假装能续。"""
    _install_fake_server(tmp_path, monkeypatch, {"say": "OK"})

    result = offload.run_task("接着干", cwd=workdir, agent="glm", session_id="sess-old")

    assert result.ok is False
    assert "session/load" in result.error


def test_the_ledger_tells_a_new_dispatch_from_a_resume(tmp_path, workdir, monkeypatch):
    """新建那一趟的来源那一格是 ``acp.sessionId``（**不许**与续接混）。"""
    _install_fake_server(tmp_path, monkeypatch, {"say": "干完了", "load_session": True})

    result = offload.run_task("干活", cwd=workdir, agent="glm")

    assert result.ok, result.error
    assert result.external_session_source == "acp.sessionId"


# ── 整趟派活 ────────────────────────────────────────────────────────────

def test_the_acp_leg_carries_the_task_and_brings_the_answer_back(tmp_path, workdir, monkeypatch):
    """**这一刀的要害**：纯 ACP 客席能接活——它说的话收得回来，产物认得出来。"""
    _install_fake_server(tmp_path, monkeypatch, {
        "say": "干完了",
        "modes": GLM_MODES,
        "current": "default",
        "write_file": "answer.md",
    })

    result = offload.run_task("干活", cwd=workdir, agent="glm")

    assert result.ok, result.error
    assert result.text == "干完了"
    # 会话号是 ACP 给的那一条（屏上「会话」那一格与台账都靠它）。
    assert result.external_session == "sess-1"
    assert result.external_session_source == "acp.sessionId"
    # 产物是它落在工作目录里的那个文件。
    assert any(str(item["path"]).endswith("answer.md") for item in result.artifacts)
    assert result.produced is True
    # 用的哪一档，写在结果里（台账那一格就是它）。
    assert result.permission_flags == ("acp-mode:bypass_permissions",)


def test_the_task_reaches_the_vendor_as_one_prompt(tmp_path, workdir, monkeypatch):
    """提示词**原样**交过去（回执那几行也一起交——回执机制在两条腿上是同一套）。"""
    prompt_log = tmp_path / "prompt.json"
    _install_fake_server(tmp_path, monkeypatch, {
        "say": "OK", "modes": GLM_MODES, "current": "default",
        "prompt_log": str(prompt_log),
    })

    offload.run_task("把课表采完", cwd=workdir, agent="glm")

    payload = json.loads(_read(prompt_log))
    blocks = payload.get("prompt") or []
    assert blocks and blocks[0].get("type") == "text"
    assert "把课表采完" in blocks[0].get("text", "")


def test_the_leg_is_chosen_by_the_table_not_by_the_vendor_name(tmp_path, workdir, monkeypatch):
    """走哪条腿**真源表那一行说了算**：ACP 腿的那几家不进命令行腿。"""
    _install_fake_server(tmp_path, monkeypatch, {
        "say": "OK", "modes": GLM_MODES, "current": "default",
    })
    called = []
    monkeypatch.setattr(
        offload, "_run_child",
        lambda *a, **k: called.append(a) or pytest.fail("命令行腿被调用了"),
    )

    result = offload.run_task("干活", cwd=workdir, agent="glm")

    assert result.ok
    assert called == []


# ── 档 ──────────────────────────────────────────────────────────────────

def test_the_permission_level_is_translated_into_its_own_mode(tmp_path, workdir, monkeypatch):
    """我们要的**归一化档** ⇒ 按表里声明的映射换成它自己那一档。"""
    mode_log = tmp_path / "mode.json"
    _install_fake_server(tmp_path, monkeypatch, {
        "say": "OK", "modes": GLM_MODES, "current": "default",
        "mode_log": str(mode_log),
    })

    result = offload.run_task("干活", cwd=workdir, agent="glm", permission_mode="edit")

    assert result.ok, result.error
    assert json.loads(_read(mode_log)).get("modeId") == "accept_edits"
    assert result.permission_flags == ("acp-mode:accept_edits",)


def test_a_level_it_does_not_have_is_refused_out_loud(tmp_path, workdir, monkeypatch):
    """**主人明说那一档、而它没有** ⇒ 照实说没有，不静默换一档。"""
    _install_fake_server(tmp_path, monkeypatch, {
        "say": "OK", "modes": [{"id": "default", "name": "Ask"}], "current": "default",
    })

    result = offload.run_task("干活", cwd=workdir, agent="glm", permission_mode="edit")

    assert not result.ok
    assert "没有" in result.error and "edit" in result.error


def test_an_agent_mode_vendor_is_not_given_a_permission_level(tmp_path, workdir, monkeypatch):
    """DevEco 那一族的档是 **agent 模式**，不是权限档 ⇒ 照实说对不上，不硬套。"""
    modes = [{"id": "build", "name": "build"}, {"id": "ask", "name": "ask"},
             {"id": "goal", "name": "goal"}, {"id": "plan", "name": "plan"}]
    _install_fake_server(tmp_path, monkeypatch, {
        "say": "OK", "modes": modes, "current": "build",
    })

    result = offload.run_task("干活", cwd=workdir, agent="deveco", permission_mode="read_only")

    assert not result.ok
    assert "agent" in result.error


# ── 授权问句 ────────────────────────────────────────────────────────────

def test_its_permission_question_is_answered(tmp_path, workdir, monkeypatch):
    """它问授权 ⇒ **必须答**，否则它卡在那儿等（这一趟就白等）。"""
    answer_log = tmp_path / "answer.json"
    _install_fake_server(tmp_path, monkeypatch, {
        "say": "OK", "modes": GLM_MODES, "current": "default",
        "ask_permission": True, "answer_log": str(answer_log),
    })

    result = offload.run_task("干活", cwd=workdir, agent="glm")

    assert result.ok, result.error
    answer = json.loads(_read(answer_log))
    assert answer.get("result", {}).get("outcome", {}).get("optionId") == "allow"
    # **它问了几次授权是读数**（不回它，它就卡在那儿等 ⇒ 那条分支要有数）。
    assert result.permission_requests == 1


def test_a_read_only_run_answers_no(tmp_path, workdir, monkeypatch):
    """**要的是只读** ⇒ 它问权限一律不准（不替主人点同意）。"""
    answer_log = tmp_path / "answer.json"
    _install_fake_server(tmp_path, monkeypatch, {
        "say": "OK", "modes": GLM_MODES, "current": "default",
        "ask_permission": True, "answer_log": str(answer_log),
    })

    offload.run_task("看一眼就好", cwd=workdir, agent="glm", permission_mode="read_only")

    answer = json.loads(_read(answer_log))
    assert answer.get("result", {}).get("outcome", {}).get("optionId") == "reject"


# ── 它自己拒了／它自己说没登录 ───────────────────────────────────────────

def test_its_own_refusal_is_reported_in_its_own_words(tmp_path, workdir, monkeypatch):
    """厂商自己拒了 ⇒ **原话带出去**，我们不替它翻译成「失败了」。"""
    _install_fake_server(tmp_path, monkeypatch, {
        "session_error": {"code": -32000, "message": "Authentication required"},
    })

    result = offload.run_task("干活", cwd=workdir, agent="glm")

    assert not result.ok
    assert "Authentication required" in result.error


def test_the_details_buried_in_the_error_data_are_not_dropped(tmp_path, workdir, monkeypatch):
    """ACP 的错误把要紧的话放在 ``data.details`` 里（11-15 实测 GLM 那一支）——
    只取 ``message`` 会把真正有用的那半句丢掉。"""
    _install_fake_server(tmp_path, monkeypatch, {
        "prompt_error": {"code": -32603, "message": "Internal error",
                         "data": {"details": "No API key found."}},
    })

    result = offload.run_task("干活", cwd=workdir, agent="glm")

    assert not result.ok
    assert "No API key found." in result.error


def test_a_refusal_is_not_a_success(tmp_path, workdir, monkeypatch):
    """它拒了这一趟 ⇒ 这一趟**没成**（不许因为收到了答复就算干活了）。"""
    _install_fake_server(tmp_path, monkeypatch, {
        "say": "我不想干这个", "modes": GLM_MODES, "current": "default",
        "stop_reason": "refusal",
    })

    result = offload.run_task("干活", cwd=workdir, agent="glm")

    assert not result.ok
    assert "refusal" in result.error


def test_a_silent_run_that_produced_nothing_is_not_a_success(tmp_path, workdir, monkeypatch):
    """它没回话也没落盘 ⇒ 这一趟没干完（不许当成成功）。"""
    _install_fake_server(tmp_path, monkeypatch, {
        "say": "", "modes": GLM_MODES, "current": "default",
    })

    result = offload.run_task("干活", cwd=workdir, agent="glm")

    assert not result.ok


# ── 工具面那一跳（主人那句话的落点）──────────────────────────────────────

def test_the_tool_face_can_hand_a_task_to_a_pure_acp_guest(tmp_path, workdir, monkeypatch):
    """**主人那句话的落点**：工具面（``external_offload_run``）能把活派给纯 ACP 客席。

    这一档走的是真调用链（名册 → 通道 → 台账），不是直接调通道函数。
    """
    from plobi.delegation.offload import ExternalAgent
    import tools.offload_tools as ot

    _install_fake_server(tmp_path, monkeypatch, {
        "say": "干完了", "modes": GLM_MODES, "current": "default",
    })

    rows = []

    class FakeLedger:
        def append(self, row, *, brief):
            rows.append(row)

    monkeypatch.setattr(ot, "RoundLedger", FakeLedger)
    monkeypatch.setattr(ot, "agent_roster", lambda: [
        ExternalAgent(name="glm", installed=True, logged_in=True, channel_ready=True),
    ])

    body = json.loads(ot._handle_run({"task": "干活", "agent": "glm", "cwd": str(workdir)}))

    assert body["ok"] is True, body
    assert body["agent"] == "glm"
    assert rows and rows[0].agent == "glm"


# ── 多腿并存（11-16 19:0x 主人拍板：「并存，Agent 自己判断」）────────────

def test_one_vendor_can_have_two_legs():
    """**一家的腿可以有多条**（codebuddy：命令行 ＋ ACP 两条都有）。"""
    legs = vr.DISPATCH_LEGS["codebuddy"]

    assert "cli" in legs and "acp" in legs


def test_the_default_leg_is_the_one_that_was_measured():
    """**默认腿＝实测跑通过的那条** ⇒ 不点名时行为一个字不变。"""
    assert vr.PREFER_LEGS["codebuddy"] == "cli"
    # 纯 ACP 客席只有 ACP 那条 ⇒ 它的默认腿只能是 ACP。
    for vendor in ("auggie", "glm", "grok", "kimi-code", "nova", "deveco"):
        assert vr.PREFER_LEGS[vendor] == "acp", vendor
        assert vr.DISPATCH_LEGS[vendor] == ("acp",), vendor


def test_naming_the_leg_picks_it(tmp_path, workdir, monkeypatch):
    """**点名 ⇒ 走那一条**（同一个 vendor，两条腿，两个结果）。"""
    _install_fake_server(tmp_path, monkeypatch, {
        "say": "走的是协议那条", "modes": GLM_MODES, "current": "default",
    })

    result = offload.run_task("干活", cwd=workdir, agent="glm", leg="acp")

    assert result.ok, result.error
    assert result.leg == "acp"
    assert result.text == "走的是协议那条"


def test_naming_a_leg_the_vendor_does_not_have_is_refused_out_loud(tmp_path, workdir, monkeypatch):
    """点名一条**它没有的腿** ⇒ 照实说它有哪几条，不静默换一条。"""
    result = offload.run_task("干活", cwd=workdir, agent="glm", leg="cli")

    assert not result.ok
    assert "没有" in result.error and "cli" in result.error
    assert result.leg == ""


def test_the_leg_is_recorded_on_the_result(tmp_path, workdir, monkeypatch):
    """**这一趟走的是哪条腿**是读数（台账那一格靠它）。"""
    _install_fake_server(tmp_path, monkeypatch, {
        "say": "OK", "modes": GLM_MODES, "current": "default",
    })

    result = offload.run_task("干活", cwd=workdir, agent="glm")

    assert result.leg == "acp"


# ── 真源表那一行 ────────────────────────────────────────────────────────

def test_every_acp_leg_vendor_has_an_entry_and_says_where_it_got_to():
    """走 ACP 腿的那几家：**入口命令**在、**现核到哪一步**写在表里。"""
    legs = [row for row in vr.VENDOR_ROWS if "acp" in row.legs]

    assert legs, "真源表里没有一家登记了 ACP 派活腿"
    for row in legs:
        assert row.binary_name or row.acp_command, f"{row.id}：没有入口命令"
        assert row.acp_note, f"{row.id}：没写清 ACP 那一腿现核到哪一步"
        assert "acp" in vr.DISPATCH_LEGS[row.id]


def test_only_the_acp_leg_vendors_carry_a_mode_map():
    """档位映射只挂在 ACP 腿的那几家身上（命令行的档是另一套）。"""
    for vendor, mode_map in vr.ACP_MODE_MAPS.items():
        assert "acp" in vr.DISPATCH_LEGS.get(vendor, ()), vendor
        assert mode_map.kind in ("permission", "agent"), vendor


def test_adding_one_row_grows_the_dispatch_table_too():
    """**性质**：真源表加一行 ⇒ 派活腿那张表自己长出这一家。"""
    row = vr.VendorRow(
        id="acme",
        binary_name="acme",
        command_env="PLOBI_OFFLOAD_ACME_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_ACME_EXTRA_ARGS",
        offloadable=True,
        legs=("acp",),
        acp_args=("acp",),
    )
    rows = vr.VENDOR_ROWS + (row,)

    assert vr.dispatch_legs(rows)["acme"] == ("acp",)
    assert vr.dispatch_legs(rows) == vr.dispatch_legs(vr.VENDOR_ROWS) | {"acme": ("acp",)}



# ── ACP 入口那一格（11-16 第十刀）──────────────────────────────────────


def test_the_entry_point_can_be_another_executable(monkeypatch):
    """入口是**另一个可执行文件**时，拼出来的就是它。

    官方 ACP 注册表里 agy 那一家是 ``agy_acp_server.exe``（**不是** ``agy acp``）
    ⇒ 这一格就是为它留的。
    """
    contract = vr.VendorContract(id="acme", acp_command=("agy_acp_server.exe",), legs=("acp",))
    monkeypatch.setitem(offload.OFFLOAD_CONTRACT, "acme", contract)
    monkeypatch.setattr(
        offload, "_which_prefer_executable",
        lambda name: r"C:\Program Files\Antigravity\agy_acp_server.exe",
    )

    assert offload.acp_argv("acme") == (r"C:\Program Files\Antigravity\agy_acp_server.exe",)


def test_an_empty_entry_point_keeps_the_old_shape(monkeypatch):
    """新格空 ⇒ **现有一切行为一个字不变**（本机那个二进制 ＋ 旗标）。"""
    contract = vr.VendorContract(id="acme", acp_args=("--acp",), legs=("acp",))
    monkeypatch.setitem(offload.OFFLOAD_CONTRACT, "acme", contract)
    monkeypatch.setattr(offload, "resolve_command", lambda agent="acme": r"C:\x\agy.exe")

    assert offload.acp_argv("acme") == (r"C:\x\agy.exe", "--acp")


def test_an_entry_point_that_is_not_on_this_machine_is_not_invented(monkeypatch):
    """那台可执行文件**这台机器上没有** ⇒ 空元组（调用方照实说「没有它」）。

    **不编一个假命令** —— 编出来的命令会让人以为它跑过了。
    """
    contract = vr.VendorContract(id="acme", acp_command=("agy_acp_server.exe",), legs=("acp",))
    monkeypatch.setitem(offload.OFFLOAD_CONTRACT, "acme", contract)
    monkeypatch.setattr(offload, "_which_prefer_executable", lambda name: "")

    assert offload.acp_argv("acme") == ()


def test_a_guest_whose_entry_point_is_another_executable_can_still_be_sent_work(
    tmp_path, workdir, monkeypatch,
):
    """入口是另一个可执行文件的那一家，照样能把活送过去（走那一格，不走本机那个二进制）。"""
    contract = vr.VendorContract(id="acme", acp_command=("agy_acp_server.exe",), legs=("acp",))
    monkeypatch.setitem(offload.OFFLOAD_CONTRACT, "acme", contract)
    monkeypatch.setitem(offload.DISPATCH_LEGS, "acme", ("acp",))

    script = tmp_path / "fake_acp_server.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    monkeypatch.setenv("FAKE_ACP_CONFIG", json.dumps({"say": "OK"}, ensure_ascii=False))
    monkeypatch.setattr(offload, "_which_prefer_executable", lambda name: str(script))
    monkeypatch.setattr(
        offload, "spawn_argv",
        lambda argv: [sys.executable, str(script), *list(argv)[1:]],
    )
    monkeypatch.setattr(offload, "resolve_proxy", lambda: ("", "none"))

    result = offload.run_task("只回 OK", agent="acme", leg="acp", cwd=str(workdir))

    assert result.ok, result.error
    assert result.leg == "acp"
    assert "OK" in result.text


def test_a_vendor_with_only_an_entry_point_is_not_told_it_has_no_acp_leg(monkeypatch):
    """只有 ``acp_command``（旗标空）的那一家，探针**不许**说它「没法用 ACP 问」。

    这一格存在的理由就是这个：入口命令不是旗标时，只看旗标会把它当成没有入口。
    """
    from plobi.delegation import offload_contract as oc

    contract = vr.VendorContract(id="acme", acp_command=("agy_acp_server.exe",), legs=("acp",))
    monkeypatch.setitem(offload.OFFLOAD_CONTRACT, "acme", contract)
    monkeypatch.setattr(offload, "_which_prefer_executable", lambda name: "")

    answer = oc.probe_acp_login("acme", use_cache=False)

    assert answer.source != "no-acp", answer.detail
    assert answer.value is None


# ── ACP 那一腿要带的环境变量（11-16 第十刀第二步）────────────────────────


def test_the_env_the_registry_writes_down_is_carried_to_the_child(
    tmp_path, workdir, monkeypatch,
):
    """官方注册表在分发项里写明 ``env`` 的那几家 ⇒ 子进程**真的拿到**它。

    Muse 要 ``MUSE_EXPERIMENTAL_ACP=on``、VTCode 要 ``VT_ACP_ENABLED=1``／
    ``VT_ACP_ZED_ENABLED=1``——**不带它，它们根本不开 ACP 那一腿**（旗标对了也不开）。

    那一格写在真源表里 ⇒ 代码里没有按厂商分叉的 if。
    """
    contract = vr.VendorContract(
        id="acme",
        acp_command=("agy_acp_server.exe",),
        legs=("acp",),
        acp_env=(("PLOBI_TEST_ACP_ENV", "on"),),
    )
    monkeypatch.setitem(offload.OFFLOAD_CONTRACT, "acme", contract)
    monkeypatch.setitem(offload.DISPATCH_LEGS, "acme", ("acp",))

    script = tmp_path / "fake_acp_server.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    env_log = tmp_path / "env.txt"
    monkeypatch.setenv(
        "FAKE_ACP_CONFIG",
        json.dumps({"say": "OK", "env_log": str(env_log)}, ensure_ascii=False),
    )
    monkeypatch.setattr(offload, "_which_prefer_executable", lambda name: str(script))
    monkeypatch.setattr(
        offload,
        "spawn_argv",
        lambda argv: [sys.executable, str(script), *list(argv)[1:]],
    )
    monkeypatch.setattr(offload, "resolve_proxy", lambda: ("", "none"))

    result = offload.run_task("只回 OK", agent="acme", leg="acp", cwd=str(workdir))

    assert result.ok, result.error
    assert env_log.read_text(encoding="utf-8").strip() == "on"


def test_the_two_families_that_need_the_env_have_it_registered():
    """真源表里那两家**确实登记了**（照官方注册表写的那几个抄的）。"""
    assert vr.OFFLOAD_CONTRACT["muse-code"].acp_env == (("MUSE_EXPERIMENTAL_ACP", "on"),)
    assert vr.OFFLOAD_CONTRACT["vtcode"].acp_env == (
        ("VT_ACP_ENABLED", "1"),
        ("VT_ACP_ZED_ENABLED", "1"),
    )


def test_a_family_that_needs_no_env_gets_none():
    """不带环境变量的那一家 ⇒ 空（不塞任何东西进子进程）。"""
    assert offload.acp_env("codebuddy") == {}
