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
    if method is None:
        # 这是客户端对我们那个授权问句的答复。
        log("answer_log", json.dumps(msg, ensure_ascii=False))
        continue
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": ident, "result": {
            "protocolVersion": 1, "agentCapabilities": {}}})
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


# ── 真源表那一行 ────────────────────────────────────────────────────────

def test_every_acp_leg_vendor_has_an_entry_and_says_where_it_got_to():
    """走 ACP 腿的那几家：**入口命令**在、**现核到哪一步**写在表里。"""
    legs = [row for row in vr.VENDOR_ROWS if row.dispatch_leg == "acp"]

    assert legs, "真源表里没有一家登记了 ACP 派活腿"
    for row in legs:
        assert row.binary_name, f"{row.id}：没有入口命令"
        assert row.acp_note, f"{row.id}：没写清 ACP 那一腿现核到哪一步"
        assert row.id in vr.DISPATCH_LEGS


def test_only_the_acp_leg_vendors_carry_a_mode_map():
    """档位映射只挂在 ACP 腿的那几家身上（命令行的档是另一套）。"""
    for vendor, mode_map in vr.ACP_MODE_MAPS.items():
        assert vr.DISPATCH_LEGS.get(vendor) == "acp", vendor
        assert mode_map.kind in ("permission", "agent"), vendor


def test_adding_one_row_grows_the_dispatch_table_too():
    """**性质**：真源表加一行 ⇒ 派活腿那张表自己长出这一家。"""
    row = vr.VendorRow(
        id="acme",
        binary_name="acme",
        command_env="PLOBI_OFFLOAD_ACME_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_ACME_EXTRA_ARGS",
        offloadable=True,
        dispatch_leg="acp",
        acp_args=("acp",),
    )
    rows = vr.VENDOR_ROWS + (row,)

    assert vr.dispatch_legs(rows)["acme"] == "acp"
    assert vr.dispatch_legs(rows) == vr.dispatch_legs(vr.VENDOR_ROWS) | {"acme": "acp"}
