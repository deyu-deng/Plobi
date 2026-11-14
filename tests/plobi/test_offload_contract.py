"""外包契约表（`plobi/delegation/offload_contract.py`）的判据。

钉住的四条：

1. **一家一行**：名册（``agent/external_offload.EXTERNAL_AGENTS``）里每一家，契约表里都有；
   契约表里也没有名册之外的幽灵。加一家只许改契约表这一处。
2. **ACP 起法照官方注册表**：旗标与注册表里那家一致；官方注册表里没有的那家（codex／claude）
   明写「没法用 ACP 问它」，不编一个旗标。
3. **回跳旗标只有实测过的**：没实测过的那家在契约表里明写「这一家还没有实测过的回跳旗标」。
4. **三态**：它自己给了会话 ⇒ 登录着；它按认证的规矩拒了 ⇒ **它自己说没登录**；
   别的拒（缺 API key 那一族）／没回话 ⇒ **探不到**，不许说成「没登录」。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent import external_offload as channel            # noqa: E402
from plobi.delegation import offload_contract as contract  # noqa: E402


# ── 1. 一家一行 ──────────────────────────────────────────────────────────

def test_every_vendor_in_the_roster_has_a_contract_row():
    assert set(contract.OFFLOAD_CONTRACT) == set(channel.EXTERNAL_AGENTS)


def test_no_ghost_row_outside_the_roster():
    for name in contract.OFFLOAD_CONTRACT:
        assert name in channel.EXTERNAL_AGENTS, f"{name} 不在名册里：契约表不许有幽灵行"


# ── 2. ACP 起法照官方注册表 ──────────────────────────────────────────────

def test_acp_args_match_the_official_registry():
    """11-14 现核的官方注册表（``agentclientprotocol/registry``，Apache-2.0）那几行。"""
    expected = {
        "qoder": ("qoder", ("--acp",)),
        "codebuddy": ("codebuddy-code", ("--acp",)),
        "cline": ("cline", ("--acp",)),
        "gemini": ("gemini", ("--acp",)),
        "opencode": ("opencode", ("acp",)),
        "kiro": ("kiro", ("acp",)),
        "qwen": ("qwen-code", ("--acp",)),
        "copilot": ("github-copilot-cli", ("--acp",)),
        "kilo": ("kilo", ("acp",)),
        "agy": ("antigravity-acp", ("acp",)),
    }
    for name, (registry_id, args) in expected.items():
        spec = contract.contract_for(name)
        assert spec is not None, name
        assert spec.acp_registry_id == registry_id, name
        assert spec.acp_args == args, name


def test_a_vendor_the_registry_does_not_know_has_no_acp_flags():
    """codex / claude 不在官方注册表里 ⇒ 明写「没法用 ACP 问它」，不编一个旗标。"""
    for name in ("codex", "claude"):
        spec = contract.contract_for(name)
        assert spec is not None, name
        assert spec.acp_args == (), name
        assert spec.acp_note, f"{name} 得说清为什么问不了"


# ── 3. 回跳旗标只有实测过的 ──────────────────────────────────────────────

def test_only_the_measured_vendors_get_a_resume_flag():
    got = contract.resume_argv_map()
    assert set(got) == {"codebuddy", "agy", "qoder"}
    for argv in got.values():
        assert "{session_id}" in argv, "回跳旗标里必须留那个唯一的槽位"


def test_a_vendor_without_a_measured_flag_says_so_in_words():
    for name, spec in contract.OFFLOAD_CONTRACT.items():
        if spec.resume_argv:
            continue
        assert spec.resume_note == contract.NO_RESUME_FLAG, name


def test_the_resume_flags_match_the_ones_the_console_actually_runs():
    """控制台那一侧的回跳白名单改读契约表 ⇒ 两边不许分家。"""
    from plobi.console.router import OFFLOAD_RESUME_ARGV

    assert OFFLOAD_RESUME_ARGV == contract.resume_argv_map()


# ── 4. 三态 ──────────────────────────────────────────────────────────────

def test_only_an_auth_wording_counts_as_it_saying_not_signed_in():
    """gemini 11-14 实测：`Gemini API key is missing or not configured.` ⇒ **探不到**。

    那一腿要的是 API key，**不等于**那家软件没登录（它自己的 OAuth 登录可能好着）。
    """
    assert contract._looks_like_auth_refusal(
        {"code": -32000, "message": "Authentication required: Use Qwen Code CLI to authenticate first."}
    ) is True
    assert contract._looks_like_auth_refusal(
        {"code": -32000, "message": "Gemini API key is missing or not configured."}
    ) is False
    assert contract._looks_like_auth_refusal({"code": -32601, "message": "method not found"}) is False


class _FakeStdin:
    def __init__(self):
        self.sent: list[dict] = []

    def write(self, raw: bytes) -> int:
        self.sent.append(json.loads(raw.decode("utf-8")))
        return len(raw)

    def flush(self) -> None:
        pass


class _FakeProc:
    """假的那家 CLI：按脚本回话（或干脆不吭声）。"""

    def __init__(self, replies: list[bytes]):
        self.stdin = _FakeStdin()
        self.stdout = iter(replies)
        self.stderr = iter(())
        self.pid = 4242
        self.killed = False

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout=None) -> int:
        return 0


def _ok_reply(message_id: int, result: dict) -> bytes:
    return (json.dumps({"jsonrpc": "2.0", "id": message_id, "result": result}) + "\n").encode("utf-8")


def _err_reply(message_id: int, code: int, message: str) -> bytes:
    return (json.dumps({
        "jsonrpc": "2.0", "id": message_id,
        "error": {"code": code, "message": message},
    }) + "\n").encode("utf-8")


def _install_fake_cli(monkeypatch, replies: list[bytes]) -> None:
    """把那家的入口换成一只假进程（不真的起 CLI）。"""
    monkeypatch.setattr(channel, "resolve_command", lambda agent="codebuddy": "D:/bin/fake.cmd")
    monkeypatch.setattr(channel, "spawn_argv", lambda argv: list(argv))
    monkeypatch.setattr(channel, "_kill_descendants", lambda proc: "进程树已杀掉")
    monkeypatch.setattr(contract.subprocess, "Popen",
                        lambda *a, **k: _FakeProc(list(replies)))
    # **不许往仓里写缓存**：探测的落盘那一格在测试里直接短路（答案已经拿到了，落盘没有意义）。
    monkeypatch.setattr(contract, "_remember", lambda answer: answer)


def test_a_session_is_the_positive_reading(monkeypatch):
    _install_fake_cli(monkeypatch, [
        _ok_reply(1, {"protocolVersion": 1}),
        _ok_reply(2, {"sessionId": "s-1", "models": {"availableModels": []}}),
    ])
    answer = contract.probe_acp_login("codebuddy", use_cache=False)

    assert answer.value is True
    assert answer.source == "acp"


def test_an_auth_refusal_is_the_vendor_saying_not_signed_in(monkeypatch):
    _install_fake_cli(monkeypatch, [
        _ok_reply(1, {"protocolVersion": 1}),
        _err_reply(2, -32000, "Authentication required: Call authenticate before starting a session"),
    ])
    answer = contract.probe_acp_login("codebuddy", use_cache=False)

    assert answer.value is False
    assert answer.source == "acp-refused"
    assert "Authentication required" in answer.detail


def test_another_refusal_is_unknown_not_a_logout(monkeypatch):
    """缺 API key 那一族 ⇒ **探不到**：不许把「这一腿没给会话」说成「它没登录」。"""
    _install_fake_cli(monkeypatch, [
        _ok_reply(1, {"protocolVersion": 1}),
        _err_reply(2, -32000, "Gemini API key is missing or not configured."),
    ])
    answer = contract.probe_acp_login("gemini", use_cache=False)

    assert answer.value is None
    assert answer.source == "acp-refused-other"


def test_silence_is_unknown_not_a_logout(monkeypatch):
    _install_fake_cli(monkeypatch, [])
    answer = contract.probe_acp_login("qoder", use_cache=False)

    assert answer.value is None
    assert answer.source == "acp-silent"


def test_a_vendor_without_an_acp_leg_is_unknown_not_a_logout(monkeypatch):
    answer = contract.probe_acp_login("codex", use_cache=False)

    assert answer.value is None
    assert answer.source == "no-acp"


def test_a_vendor_that_is_not_installed_is_unknown(monkeypatch):
    monkeypatch.setattr(channel, "resolve_command", lambda agent="codebuddy": "")
    answer = contract.probe_acp_login("codebuddy", use_cache=False)

    assert answer.value is None
    assert answer.source == "not-installed"


def test_the_probe_never_raises(monkeypatch):
    """探针不许把屏带崩：起不来也要成一个读数。"""
    def _boom(*_a, **_k):
        raise OSError("CreateProcess failed")

    monkeypatch.setattr(channel, "resolve_command", lambda agent="codebuddy": "D:/bin/fake.cmd")
    monkeypatch.setattr(channel, "spawn_argv", lambda argv: list(argv))
    monkeypatch.setattr(contract.subprocess, "Popen", _boom)

    answer = contract.probe_acp_login("codebuddy", use_cache=False)
    assert answer.value is None


# ── 5. 取数那一层：ACP 的答复排在最前 ───────────────────────────────────

def test_the_acp_answer_outranks_the_local_trace(monkeypatch):
    """厂商自己说没登录 ⇒ 比「本地有凭据痕迹」可信（kilo 那次就是这样：痕迹说没登录，
    厂商自己给了会话）。"""
    from plobi.delegation import offload_surface

    class _Answer:
        value = False
        source = "acp-refused"
        detail = "Authentication required"

    out = offload_surface._login_evidence({}, True, _Answer())
    assert out["value"] is False
    assert out["source"] == "acp-refused"


def test_an_unknown_acp_answer_falls_through_to_the_old_tiers(monkeypatch):
    """探不到**不算数**：往下落到「上一趟真跑的证据」与「本地痕迹」。"""
    from plobi.delegation import offload_surface

    class _Answer:
        value = None
        source = "acp-silent"
        detail = "它没回话"

    out = offload_surface._login_evidence({"answer_excerpt": "done"}, None, _Answer())
    assert out["value"] is True
    assert out["source"] == "last-run"

    out = offload_surface._login_evidence({}, True, _Answer())
    assert out["value"] is True
    assert out["source"] == "local"
