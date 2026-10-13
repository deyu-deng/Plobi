"""外包工具的测（WP-L2-OUTSOURCE-ENTRY 判据 1/3/4 + 硬边界）。

全部离线：通道 `run_task` 一律注入假的，**不起真子进程、不花钱**。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Tuple

import pytest

from plobi.delegation.offload import (
    FAIL_BLOCKED_BY_GATE,
    FAIL_NO_CHANNEL,
    FAIL_NOT_LOGGED_IN,
    FAIL_TIMEOUT,
    availability,
    selectable,
)
from tools import offload_tools as ot


@dataclass
class FakeChannelResult:
    agent: str = "codebuddy"
    cwd: str = "D:/Projects/Demo"
    permission_flags: Tuple[str, ...] = ("-y", "--permission-mode=bypassPermissions")
    sandboxed: bool = False
    exit_code: int | None = 0
    duration_s: float = 16.4
    argv: tuple = ()
    usage: Dict[str, Any] = field(default_factory=lambda: {"input_tokens": 56255, "output_tokens": 168})
    cost: float | None = None
    cost_status: str = "usage_reported_without_price"
    artifacts: tuple = ({"path": "D:/Projects/Demo/today.py"},)
    text: str = "done"
    ok: bool = True
    error: str = ""


@pytest.fixture
def roster_one_usable(monkeypatch):
    """一台"CodeBuddy 在、agy 没登录"的名册（这台 10-13 的真实形状）。"""
    real = ot._probe_logged_in

    def probe(name):
        return real(name) if name == "codebuddy" else False

    monkeypatch.setattr(ot, "_probe_logged_in", probe)
    return ot.agent_roster()


class TestTheListNeverPretends:
    def test_unprobed_agent_is_listed_but_said_so(self, roster_one_usable):
        rows = {r["name"]: r for r in selectable(roster_one_usable)}

        assert rows["agy"]["can_use"] is False
        assert "没登录" in rows["agy"]["why_not"]

    def test_status_and_availability_are_the_same_judgement(self, roster_one_usable):
        """判据 3：清单不许另造第二条判据——逐行对回 `availability()`。"""
        rows = {r["name"]: r for r in selectable(roster_one_usable)}

        for agent in roster_one_usable:
            usable, why = availability(agent)
            assert rows[agent.name]["can_use"] is usable
            assert rows[agent.name]["why_not"] == why

    def test_status_handler_agrees_with_the_roster(self, monkeypatch):
        monkeypatch.setattr(ot, "agent_roster", lambda: [])

        body = json.loads(ot._handle_status({}))

        assert body["agents"] == []
        assert "点了必失败" in body["note"]


class TestRefusalsAreSeparateSentences:
    @pytest.mark.parametrize("error, code", [
        ("agy did not finish within 900s", FAIL_TIMEOUT),
        ("external agent is not logged in", FAIL_NOT_LOGGED_IN),
        ("oauth2 token refresh failed", FAIL_NOT_LOGGED_IN),
        ("no drivable command on this machine", FAIL_NO_CHANNEL),
        ("cannot start agy.exe: refused", FAIL_NO_CHANNEL),
        ("refusing to offload inside the repository tree: Code", FAIL_BLOCKED_BY_GATE),
        ("cwd does not exist: D:/nope", FAIL_BLOCKED_BY_GATE),
    ])
    def test_each_failure_kind_maps_to_its_own_code(self, error, code):
        assert ot.classify_failure(error) == code

    def test_an_unknown_failure_is_not_forced_into_a_known_one(self):
        assert ot.classify_failure("exit 0 but no artifact was written") == ""

    def test_unusable_agent_is_refused_before_the_channel_is_touched(self, monkeypatch, roster_one_usable):
        def explode(*a, **k):
            raise AssertionError("不该起真子进程")

        monkeypatch.setattr(ot, "agent_roster", lambda: roster_one_usable)
        import agent.external_offload as channel
        monkeypatch.setattr(channel, "run_task", explode)

        body = json.loads(ot._handle_run({"task": "把这份报表整理成周报", "agent": "agy"}))

        assert body["ok"] is False and "没登录" in body["error"]
        assert body["asked"].startswith("把这份报表")


class TestASuccessfulOffloadReportsEverything:
    def _run(self, monkeypatch, tmp_path, result=None):
        captured: Dict[str, Any] = {}
        import agent.external_offload as channel

        def fake_run_task(task, *, cwd=None, agent=None, permission_mode=None, timeout=900.0):
            captured.update({"task": task, "cwd": str(cwd), "agent": agent,
                             "permission_mode": permission_mode})
            return result or FakeChannelResult()

        rows: list = []

        class FakeLedger:
            def append(self, row, *, brief):
                rows.append((row, brief))

        monkeypatch.setattr(channel, "run_task", fake_run_task)
        monkeypatch.setattr(channel, "default_workdir", lambda: tmp_path / "offload")
        monkeypatch.setattr(ot, "RoundLedger", FakeLedger)
        monkeypatch.setattr(ot, "agent_roster", _usable_roster)
        body = json.loads(ot._handle_run({"task": "把 demo 的 today.py 写完", "cwd": "D:/Projects/Demo"}))
        return body, captured, rows

    def test_payload_names_agent_directory_permission_and_product(self, monkeypatch, tmp_path):
        body, captured, _rows = self._run(monkeypatch, tmp_path)

        assert body["ok"] is True
        assert body["agent"] == "codebuddy"
        assert body["cwd"] == "D:/Projects/Demo"
        assert "bypassPermissions" in body["permission_flag"]
        assert body["artifacts"][0]["path"].endswith("today.py")
        assert len(body["artifacts"][0]["sha256_12"]) in (12, len("unreadable"))
        assert "交给 codebuddy" in body["report"] and "bypassPermissions" in body["report"]

    def test_unmeasurable_money_stays_null_and_says_so(self, monkeypatch, tmp_path):
        body, _c, _r = self._run(monkeypatch, tmp_path)

        assert body["cost"] is None
        assert body["cost_status"] == "usage_reported_without_price"
        assert "未知" in body["report"], "算不出金额要写未知，不许写 0（R-073）"

    def test_every_run_writes_exactly_one_ledger_row(self, monkeypatch, tmp_path):
        _b, _c, rows = self._run(monkeypatch, tmp_path)

        assert len(rows) == 1
        row, brief = rows[0]
        assert row.agent == "codebuddy" and brief.startswith("把 demo")

    def test_a_zero_artifact_run_is_reported_with_the_channels_own_sentence(self, monkeypatch, tmp_path):
        bad = FakeChannelResult(ok=False, exit_code=0, artifacts=(),
                                error="exit 0 but no artifact was written — the agent did not actually do the work")
        body, _c, _r = self._run(monkeypatch, tmp_path, result=bad)

        assert body["ok"] is False
        assert "artifact" in body["raw_error"]
        assert body["error"], "失败必须有一句话，不能空着"


def _usable_roster():
    from plobi.delegation.offload import ExternalAgent

    return [ExternalAgent(name="codebuddy", installed=True, logged_in=True,
                          channel_ready=True, permission_flag="-y --permission-mode=bypassPermissions",
                          capabilities={"writes": True})]


class TestOptInAndPolicy:
    def test_offload_toolset_exists_and_is_not_reachable_by_default(self):
        from toolsets import TOOLSETS, _PLOBI_CORE_TOOLS

        assert "offload" in TOOLSETS
        assert set(TOOLSETS["offload"]["tools"]) == {"external_offload_status", "external_offload_run"}
        for name in TOOLSETS["offload"]["tools"]:
            assert name not in _PLOBI_CORE_TOOLS, "进了核心包就等于默认开（裁定 78）"
        assert not [k for k, v in TOOLSETS.items() if "offload" in (v.get("includes") or [])], \
            "别的 toolset include 它 = 换个口子默认开"

    def test_missing_cwd_does_not_land_on_the_system_drive(self, monkeypatch, tmp_path):
        """不传 cwd 时落到 Plobi 家那一档（这台 D 盘），不许悄悄写进 C 盘临时区。"""
        captured = {}
        import agent.external_offload as channel

        monkeypatch.setattr(channel, "run_task",
                            lambda task, **kw: captured.update(cwd=str(kw.get("cwd"))) or FakeChannelResult())
        monkeypatch.setattr(channel, "default_workdir", lambda: tmp_path / "plobi-home-offload")
        monkeypatch.setattr(ot, "RoundLedger", lambda *a, **k: type("L", (), {"append": lambda s, r, *, brief: None})())
        monkeypatch.setattr(ot, "agent_roster", _usable_roster)

        ot._handle_run({"task": "x"})

        # 契约是"没给 cwd 就交给 default_workdir()"——那一格才是策略真源：
        # 它落 Plobi 家（这台 D 盘），不是 tempfile.gettempdir()（C 盘），
        # 见 agent/external_offload.py::default_workdir 的注释。
        assert captured["cwd"] == str(tmp_path / "plobi-home-offload")
