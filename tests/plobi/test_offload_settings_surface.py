"""设置 → 外包 那一屏的行为契约（`WP-OFFLOAD-REPLY` 的界面取数半）。

三条要钉住的事：

1. **登录态只信厂商自己的探针**，不看凭据目录存不存在（10-22 实测：存在性那一套对
   codebuddy/qoder 都报 True，而同一刻 Qoder CLI 自己回的却是「Not logged in」）。
2. **探针自己测歪了不许说成"没登录"**：带着会话的 SDK 变量去敲，子进程会回
   `sdk_invalid_args`（rc 还是 0）——那是"测法不对"，读作 `None` + `probe-broken`。
3. **能不能用只有一处判据**（`availability`），这一屏只搬字节：装没装 / 登没登 /
   最放开档 / 今日几趟多少钱 / 最后一趟说了什么 / 能不能跳回它那屏。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent import external_offload as channel      # noqa: E402
from plobi.delegation import offload_surface       # noqa: E402


class _Done:
    def __init__(self, stdout: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def _fake_run_factory(done: _Done):
    def _run(*_a, **_kw):
        return done
    return _run


def test_the_vendor_itself_saying_not_logged_in_is_a_verdict_not_a_guess(monkeypatch):
    monkeypatch.setattr(channel, "resolve_command", lambda agent="codebuddy": "qodercli")
    monkeypatch.setattr(channel.subprocess, "run",
                        _fake_run_factory(_Done("Not logged in · Please run /login")))
    verdict, source, detail = channel.probe_login("qoder")
    assert verdict is False
    assert source == "probe"
    assert "Not logged in" in detail


def test_a_probe_run_with_our_sdk_env_is_not_read_as_a_logout(monkeypatch):
    """rc=0 但回的是 sdk_invalid_args ⇒ 测法不对（None），不是"没登录"（False）。"""
    monkeypatch.setattr(channel, "resolve_command", lambda agent="codebuddy": "qodercli")
    monkeypatch.setattr(channel.subprocess, "run", _fake_run_factory(
        _Done("Error: sdk_invalid_args: Agent SDK entrypoint env is set but required flags are missing.")))
    verdict, source, detail = channel.probe_login("qoder")
    assert verdict is None
    assert source == "probe-broken"
    assert "sdk_invalid_args" in detail


def test_a_list_of_models_is_the_positive_reading(monkeypatch):
    monkeypatch.setattr(channel, "resolve_command", lambda agent="codebuddy": "qodercli")
    monkeypatch.setattr(channel.subprocess, "run", _fake_run_factory(_Done("qwen3-coder\nclaude-sonnet-4")))
    assert channel.probe_login("qoder")[0] is True


def test_a_vendor_without_a_registered_probe_is_unreadable_not_unusable(monkeypatch):
    monkeypatch.setattr(channel, "resolve_command", lambda agent="codebuddy": "codebuddy")
    verdict, source, _detail = channel.probe_login("nobody")
    assert verdict is None and source == "no-probe"


def test_rows_carry_one_decision_and_the_evidence_behind_it(monkeypatch):
    from datetime import datetime, timezone

    class _Base:
        def __init__(self, name, installed, flag):
            self.name = name
            self.installed = installed
            self.logged_in = True          # 存在性猜的：屏上只当 hint，不当判据
            self.channel_ready = installed
            self.permission_flag = flag
            self.capabilities = {"writes": True}

    monkeypatch.setattr(channel, "resolve_command",
                        lambda agent="codebuddy": "" if agent == "agy" else f"/bin/{agent}")
    monkeypatch.setattr(channel, "permission_flags", lambda agent, mode=None: ("--permission-mode", "default"))
    monkeypatch.setattr(offload_surface, "_version_of", lambda binary, scrub: "9.9.9")
    today_ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    monkeypatch.setattr(offload_surface, "_rows_by_agent", lambda: {
        "qoder": [{"ts": today_ts, "cwd": "D:/Projects/Sindri",
                   "duration_seconds": 9.36, "reply_status": "not_filed",
                   "answer_excerpt": "Not logged in · Please run /login", "external_session": "abc"}],
    })
    monkeypatch.setattr("tools.offload_tools.agent_roster", lambda: [
        _Base("codebuddy", True, "-y"), _Base("qoder", True, ""), _Base("agy", False, ""),
    ])
    payload = offload_surface.external_agent_rows(refresh=True)
    by_name = {row["name"]: row for row in payload["agents"]}

    # 判据只有 availability：没装 / 上一趟自己说没登录 两种都不能用，各自说自己的原因
    assert by_name["agy"]["can_use"] is False and "没装" in by_name["agy"]["why_not"]
    assert by_name["qoder"]["can_use"] is False and "没登录" in by_name["qoder"]["why_not"]
    assert by_name["qoder"]["logged_in"]["value"] is False
    assert by_name["qoder"]["logged_in"]["source"] == "last-run"
    # 存在性猜的那个只当 hint，不当判据（10-22 它绿着骗过一回）
    assert by_name["qoder"]["login_hint"] is True
    # 这一屏只搬字节：最后一趟说了什么、能不能跳回去、今天几趟，都是读数
    assert by_name["qoder"]["today"]["rounds"] == 1
    assert by_name["qoder"]["last_round"]["answer_excerpt"].startswith("Not logged in")
    assert by_name["codebuddy"]["can_use"] is True
    assert "Providers" in payload["note"]
