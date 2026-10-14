"""决策面的钉：清单与工具同一判据、四种失败分得开、算不出的钱不许装成 0。

跑法：``bash scripts/run_tests.sh tests/plobi/test_delegation_offload_entry.py -q``
（唯一入口；并行档不传 ``--basetemp``，裁定 87）。全是**关系断言**，不冻任何名单/金额快照。
"""

from __future__ import annotations

import json

import pytest

from plobi.delegation import offload
from plobi.delegation.offload import (
    CAP_PERMISSION_MODE,
    CAP_USAGE_REPORT,
    ExternalAgent,
    OffloadResult,
    RoundLedger,
    availability,
    failure_note,
    selectable,
    summarize_round,
)


def _agent(name="CodeBuddy", **kw):
    base = dict(installed=True, logged_in=True, channel_ready=True,
                permission_flag="--permission-mode bypassPermissions",
                capabilities={CAP_PERMISSION_MODE: True, CAP_USAGE_REPORT: True})
    base.update(kw)
    return ExternalAgent(name=name, **base)


class TestSingleJudgment:
    def test_list_and_tool_read_the_same_availability_function(self, monkeypatch):
        """清单不许自己再判一遍：它必须逐条走 availability（R-072 那个教训的对照钉）。"""
        calls: list[str] = []
        real = offload.availability

        def spy(agent):
            calls.append(agent.name)
            return real(agent)

        monkeypatch.setattr(offload, "availability", spy)
        selectable([_agent("A"), _agent("B")])
        assert calls == ["A", "B"]

    def test_unusable_agent_is_listed_with_its_reason_never_as_usable(self):
        rows = selectable([
            _agent("LoggedOut", logged_in=False),
            _agent("NoChannel", channel_ready=False),
            _agent("Missing", installed=False),
            _agent("Ok"),
        ])
        by_name = {row["name"]: row for row in rows}
        assert by_name["Ok"]["can_use"] is True
        for name in ("LoggedOut", "NoChannel", "Missing"):
            assert by_name[name]["can_use"] is False, name
            assert by_name[name]["why_not"].strip(), name

    def test_probe_failure_is_not_judged_as_logged_out(self):
        """探不到登录态（None）不判成"不能"——悄悄抹掉一个手下才是要修的 bug。"""
        usable, why = availability(_agent("Prism", logged_in=None))
        assert usable is True and why == ""

    def test_four_failures_are_four_different_sentences(self):
        notes = {failure_note(code) for code in offload.FAILURES}
        assert len(notes) == len(offload.FAILURES)
        assert failure_note("brand_new_code").startswith("没记到的失败类型")


class TestCapabilityNotName:
    def test_two_agents_with_same_capabilities_differ_only_by_name(self):
        a = _agent("Alpha")
        b = _agent("Bravo")
        row_a, row_b = selectable([a]), selectable([b])
        assert {k: v for k, v in row_a[0].items() if k != "name"} == \
               {k: v for k, v in row_b[0].items() if k != "name"}

    def test_no_permission_rung_is_said_out_loud_not_assumed(self):
        text = summarize_round([OffloadResult(
            agent="Plain", cwd="/tmp/x", permission_flag="",
            usage={"cost_usd": None}, duration_seconds=3,
        )])
        assert "它没有放开档可传" in text

    def test_declared_capability_set_is_the_only_place_names_live(self):
        assert CAP_PERMISSION_MODE in offload.ALL_CAPABILITIES
        assert not any(getattr(offload, "FAIL_" + k, None) is None
                       for k in ("NOT_LOGGED_IN", "NO_CHANNEL", "TIMEOUT", "BLOCKED_BY_GATE"))


class TestMoneyHonesty:
    def test_unpriced_round_never_shows_zero_dollars(self):
        text = summarize_round([OffloadResult(
            agent="CodeBuddy", cwd="D:/x", permission_flag="--dangerously-skip-permissions",
            artifact_paths=(), usage=None, cost_status="no_price_entry", duration_seconds=12,
        )])
        assert "$0" not in text
        assert "用量未知（cost_status=no_price_entry）" in text

    def test_ledger_total_refuses_to_guess_when_any_leg_is_unpriced(self, tmp_path):
        ledger = RoundLedger(tmp_path)
        ledger.append(OffloadResult(agent="A", cwd="c", permission_flag="",
                                    usage={"cost_usd": 0.02}, cost_status="priced"), brief="x")
        total, status = ledger.round_total_usd()
        assert total == pytest.approx(0.02) and status == "priced"

        ledger.append(OffloadResult(agent="B", cwd="c", permission_flag="",
                                    usage=None, cost_status="unknown"), brief="y")
        total, status = ledger.round_total_usd()
        assert total is None and status == "unknown"

    def test_empty_round_is_reported_as_no_offload(self):
        assert summarize_round([]) == "这一轮没有外包：全程是我自己干的。"


class TestLedgerDurability:
    def test_half_written_last_line_is_not_a_record_and_history_is_untouched(self, tmp_path):
        ledger = RoundLedger(tmp_path)
        row = ledger.append(OffloadResult(agent="A", cwd="c", permission_flag="",
                                         usage={"cost_usd": 0.01}, cost_status="priced"),
                            brief="做一次对照表")
        assert json.loads((tmp_path / "offload_rounds.jsonl").read_text(encoding="utf-8").splitlines()[0]) == row

        with (tmp_path / "offload_rounds.jsonl").open("a", encoding="utf-8") as handle:
            handle.write('{"agent": "B", "cut')          # 模拟掉电写半行
        rows = ledger.rows()
        assert [r["agent"] for r in rows] == ["A"]

    def test_artifact_fingerprint_is_recorded_per_output_file(self, tmp_path):
        artifact = tmp_path / "notes.md"
        artifact.write_text("对照表初稿", encoding="utf-8")
        ledger = RoundLedger(tmp_path)
        row = ledger.append(OffloadResult(agent="CodeBuddy", cwd=str(tmp_path),
                                          permission_flag="--permission-mode bypassPermissions",
                                          artifact_paths=(str(artifact),),
                                          usage={"cost_usd": None},
                                          cost_status="no_price_entry"),
                            brief="写对照表")
        assert row["artifacts"][0]["path"] == str(artifact)
        assert len(row["artifacts"][0]["sha256_12"]) == 12
        assert row["permission_flag"] == "--permission-mode bypassPermissions"
        assert row["usage"]["cost_usd"] is None and row["cost_status"] == "no_price_entry"

    def test_ledger_path_is_profile_aware_not_hardcoded(self, monkeypatch, tmp_path):
        from pathlib import Path

        monkeypatch.setattr("plobi_constants.get_plobi_home", lambda: Path(tmp_path) / "home")
        path = RoundLedger().path
        assert str(Path(tmp_path) / "home") in str(path)
        assert path.name == "offload_rounds.jsonl"


class TestFailureTaxonomy:
    @pytest.mark.parametrize("code", offload.FAILURES)
    def test_each_failure_kind_reaches_the_round_summary(self, code):
        text = summarize_round([OffloadResult(agent="agy", cwd="D:/p",
                                              permission_flag="--dangerously-skip-permissions",
                                              failure=code)])
        assert "没成" in text
        assert failure_note(code) in text

    def test_timeout_is_not_reported_as_a_gate_block(self):
        timeout = summarize_round([OffloadResult(agent="X", cwd="c", permission_flag="",
                                                 failure=offload.FAIL_TIMEOUT)])
        gate = summarize_round([OffloadResult(agent="X", cwd="c", permission_flag="",
                                              failure=offload.FAIL_BLOCKED_BY_GATE)])
        assert timeout != gate
        assert "闸" in gate and "闸" not in timeout


class TestSwitchIsReachable:
    """「opt-in」这句话要有面可opt，否则它就是工具描述里的一句谎。

    10-14 现核：`toolsets.py` 里那句 "the owner enables it in `plobi tools`" 写的时候
    `offload` 并不在 `CONFIGURABLE_TOOLSETS` 里 —— 而那张清单才是 `plobi tools` 和桌面
    设置页（`GET /api/tools/toolsets`）唯一的来源。不在册 = 没有一个面能把它打开。
    """

    def test_offload_toolset_is_offered_by_the_switch_surface(self):
        from plobi_cli.tools_config import _get_effective_configurable_toolsets

        rows = {ts_key for ts_key, _, _ in _get_effective_configurable_toolsets()}

        assert "offload" in rows, "开关面里没有它 ⇒ 工具注册了但永远开不了"

    def test_it_is_still_off_until_the_owner_flips_it(self):
        """默认关（裁定 78）：没配过的这台机器上它不该出现在启用集合里。"""
        from plobi_cli.tools_config import _get_platform_tools

        assert "offload" not in _get_platform_tools({}, "cli", include_default_mcp_servers=False)

    def test_flipping_the_switch_resolves_both_tools(self):
        """开了之后两个工具都要真解析出来，否则开关是个空按钮。"""
        from plobi_cli.tools_config import _get_platform_tools
        from toolsets import resolve_toolset

        enabled = _get_platform_tools(
            {"platform_toolsets": {"cli": ["plobi-cli", "offload"]}},
            "cli",
            include_default_mcp_servers=False,
        )

        assert "offload" in enabled
        assert {"external_offload_status", "external_offload_run"} <= set(resolve_toolset("offload"))

    def test_the_desktop_resolver_actually_sees_the_switch(self, tmp_path, monkeypatch):
        """开关拨了要真到得了桌面那一条会话 —— 走的是 `tui_gateway` 那个解析器，不是我的猜测。

        现核链条：`_load_enabled_toolsets()` 先问 `coding_selection()`（**只有用户opt-in focus
        档**才覆盖，默认回 None），否则落到 `_get_platform_tools(cfg, "cli", ...)`。
        这一发用真 import + 临时 `PLOBI_HOME` 跑通整条，而不是只看我那张表。
        """
        home = tmp_path / "plobi"
        home.mkdir()
        (home / "config.yaml").write_text(
            "toolsets:\n  - plobi-cli\n"
            "platform_toolsets:\n  cli:\n    - plobi-cli\n    - offload\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("PLOBI_HOME", str(home))
        monkeypatch.setenv("PLOBI_DESKTOP", "1")
        monkeypatch.delenv("PLOBI_TUI_TOOLSETS", raising=False)
        monkeypatch.chdir(tmp_path)

        from tui_gateway.server import _load_enabled_toolsets

        enabled = _load_enabled_toolsets() or []

        assert "offload" in enabled, "拨了开关，桌面会话仍然看不见外包工具"
