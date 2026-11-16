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


# ── 点「+」拉起登录（11-08，WP-OFFLOAD-LOGIN-LAUNCH）─────────────────────────

def test_start_login_launches_the_vendors_own_login_command(monkeypatch):
    """copilot 的 `login --web-flow` 是浏览器 OAuth ⇒ 能一键拉起。

    拉起**不等于**登录完：授权那一下要主人自己在浏览器里点同意。
    """
    import subprocess

    started: dict[str, list[str]] = {}

    def fake_popen(argv, **_kw):
        started["argv"] = list(argv)
        return object()

    monkeypatch.setattr(channel, "resolve_command", lambda agent="copilot": "D:/bin/copilot.cmd")
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    # ACP 那一腿在测试里短路（不许真起 CLI，也不许往仓里写缓存）。
    monkeypatch.setattr(offload_surface, "_probe_login_state", lambda agent: None)

    result = offload_surface.start_login("copilot")

    assert result["started"] is True
    assert "login" in result["command"]
    assert started["argv"][-2:] == ["login", "--web-flow"]


def test_start_login_says_so_when_there_is_no_one_click_login(monkeypatch):
    """kilo 的 `auth login` 要人挑 provider（终端交互）⇒ 拉不起来，如实说，不假装。"""
    monkeypatch.setattr(channel, "resolve_command", lambda agent="kilo": "D:/bin/kilo.cmd")
    monkeypatch.setattr(offload_surface, "_probe_login_state", lambda agent: None)

    result = offload_surface.start_login("kilo")

    assert result["started"] is False
    assert result["reason"], "拉不起来要给一句人话"


def test_start_login_for_an_unknown_agent_is_not_a_crash():
    """名册里没有的一家 ⇒ 一句人话，不抛异常。"""
    result = offload_surface.start_login("不存在的家")

    assert result["started"] is False
    assert result["reason"]


def test_start_login_does_not_launch_it_when_it_is_already_signed_in(monkeypatch):
    """点「+」时先问它自己（ACP 那一腿）：**已经登录着就别再拉一遍登录命令**。

    浏览器 OAuth 那一下要人点同意，白跑一次只是添乱。
    """
    import subprocess

    class _Answer:
        value = True
        source = "acp"
        detail = "它自己的 ACP 腿给我开了一条会话 ⇒ 登录着。"

    launched: list[list[str]] = []

    def fake_popen(argv, **_kw):
        launched.append(list(argv))
        return object()

    monkeypatch.setattr(channel, "resolve_command", lambda agent="copilot": "D:/bin/copilot.cmd")
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(offload_surface, "_probe_login_state", lambda agent: _Answer())

    result = offload_surface.start_login("copilot")

    assert result["started"] is False
    assert "登录" in result["reason"]
    assert launched == [], "已经登录着就不该再拉登录命令"


def test_the_login_evidence_is_ranked_not_guessed():
    """登录态按可信度排队：**厂商亲口说的 > 本地强证据 > 探不到**。

    11-08 21:2x 现读：这一格原来只读「上一趟真跑的证据」⇒ 刚装好的 Copilot CLI／Kilo Code
    永远显示「配置好了」——主人看见一个开关，却根本用不了（屏上假话），而且点不到那个
    「+」去登录。
    """
    # 本地探不到、也没派过活 ⇒ 探不到（不许读成不能）。
    assert offload_surface._login_evidence({}, None)["value"] is None
    # 本地强证据说没登录 ⇒ 就是没登录。
    assert offload_surface._login_evidence({}, False)["value"] is False
    assert offload_surface._login_evidence({}, False)["source"] == "local"
    # 上一趟厂商亲口说的，最可靠：它说没登录，就算本地有痕迹也是没登录。
    assert offload_surface._login_evidence({"answer_excerpt": "Not logged in"}, True)["value"] is False
    assert offload_surface._login_evidence({"answer_excerpt": "Not logged in"}, True)["source"] == "last-run"
    # 它带回过话 ⇒ 登着。
    assert offload_surface._login_evidence({"answer_excerpt": "done"}, None)["value"] is True


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
    # ACP 那一腿的答案在测试里是空的（不许让这台机器上一次的探测结果影响判据）。
    monkeypatch.setattr(offload_surface, "cached_answers", lambda vendors=None: {})
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


# ── 安装事实那一格（用户 11-15 13:0x）────────────────────────────────

def test_every_row_carries_its_install_facts_and_dispatch_readiness():
    """每一行都带 ``install`` 与 ``dispatch_ready``（屏上靠它们决定给不给开关）。"""
    payload = offload_surface.external_agent_rows()

    assert payload["agents"]
    for row in payload["agents"]:
        assert "install" in row
        assert "dispatch_ready" in row
        assert isinstance(row["dispatch_ready"], bool)


def test_every_row_carries_its_accounts_and_the_chosen_one():
    """每一行都带 ``accounts`` 与 ``selected_account``（屏上那一下拉靠它们）。

    **没做多账号的那一族 ⇒ 空清单**（不是"它没有账号"，是"这一族没做多账号"）。
    """
    payload = offload_surface.external_agent_rows()

    for row in payload["agents"]:
        assert "accounts" in row
        assert "selected_account" in row
        assert isinstance(row["accounts"], list)
        for account in row["accounts"]:
            assert account["id"]
            assert account["label"], "读不出名字也要给一个认得出的标识，不许给空白"


def test_the_chosen_account_is_not_guessed_when_nothing_was_chosen():
    """没选 ⇒ **空串**（厂商用它自己当前登录的那个），不许替主人挑一个。"""
    payload = offload_surface.external_agent_rows()
    by_name = {row["name"]: row for row in payload["agents"]}

    for row in payload["agents"]:
        if row["accounts"]:
            continue
        assert by_name[row["name"]]["selected_account"] == ""


def test_the_vendors_that_only_know_how_to_install_are_on_screen_without_a_switch():
    """**只装不派活**的那几家：屏上看得见（给下载引导），但**不给开关**。"""
    payload = offload_surface.external_agent_rows()
    by_name = {row["name"]: row for row in payload["agents"]}

    for vendor in ("amp", "iflow", "aider", "goose", "crush", "droid"):
        assert vendor in by_name, f"{vendor}：屏上名册里没有这一家"
        row = by_name[vendor]
        assert row["dispatch_ready"] is False
        assert row["can_use"] is False
        # 探不到登录态（**不判成没登录**）：这一家还没接派活腿 ⇒ 没实测过登录判据。
        assert row["logged_in"]["value"] is None


def test_the_install_facts_of_a_row_are_the_ones_in_the_source_table():
    """屏上给的那条安装命令，就是真源表里那一行写的（**不许屏上另抄一份**）。"""
    from agent.vendor_registry import INSTALL_SPECS

    payload = offload_surface.external_agent_rows()
    by_name = {row["name"]: row for row in payload["agents"]}

    for vendor, spec in INSTALL_SPECS.items():
        if vendor not in by_name:
            continue
        install = by_name[vendor]["install"]
        assert install is not None
        assert install["command"] == spec.command
        assert install["homepage"] == spec.homepage
