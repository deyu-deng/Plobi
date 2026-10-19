"""裁 185 那一枚原子：`remaining()`——读得到出数，读不到 `None` + 为什么读不到。

零额度：全部走注入（假 `fetch` / 假 `/healthz` 回体 / 假探针），一次补全都不发，
也不碰真网关与 `$PLOBI_HOME` 里的真账本。
"""

from __future__ import annotations

import json
import time
import urllib.error

import pytest

from plobi.quota import gateway, readout
from plobi.quota.sources import CheapApiSource, HealthStatus, SourceStatus

NOW = time.time()


def _outcome(sources, state=gateway.CatalogState.OK, detail="ok"):
    return gateway.GatewayOutcome(state, sources, detail, NOW)


def _health(accounts=None, state=gateway.CatalogState.OK, detail="health ok"):
    return readout.HealthOutcome(state, accounts or {}, detail, NOW)


def _aigw_source(provider="workbuddy"):
    return {
        "kind": "aigw",
        "model": f"{provider}/glm-5.3",
        "models": [f"{provider}/glm-5.3"],
        "base_url": "http://127.0.0.1:8000/v1",
        "api_key": "k",
        "capabilities": {"stream": True},
        "derived": True,
        "availability": {"live": True, "selectable": True, "live_reason": "ok",
                         "unselectable_reason": "", "models": 1},
    }


class _NoNetwork:
    def __call__(self, *args, **kwargs):
        raise AssertionError("这些测一次网络都不许碰")


# --- as_number：读不到的那格必须是 None，不是 0，也不是"解释一下" -------------------

@pytest.mark.parametrize("value,want", [
    (12, 12.0), (12.5, 12.5), ("14", 14.0), (" 3.75 ", 3.75), (0, 0.0), ("0", 0.0),
])
def test_as_number_accepts_only_things_that_already_are_numbers(value, want):
    got = readout.as_number(value)
    assert got == want
    assert got is not None, "0 是真读数，不许和读不到混成同一个 None"


@pytest.mark.parametrize("value", [
    None, True, False, "", "   ", "null", "unlimited", "N/A",
    float("nan"), float("inf"), {}, [], [1], "1,234",
])
def test_as_number_refuses_to_interpret_anything_else(value):
    assert readout.as_number(value) is None


def test_bool_never_becomes_a_dollar_amount():
    """Python 里 True == 1：状态位被读成"还剩 1 块钱"就是拿猜测冒充读数。"""
    assert readout.as_number(True) is None
    assert readout.as_number(False) is None


# --- 原子本体 ---------------------------------------------------------------------

def test_gateway_rate_limit_number_is_read_but_never_called_money():
    got = readout.remaining(
        "antigravity", kind="aigw",
        accounts=[{"id": "a1", "state": "ok", "fail": 0, "quota": {"remaining": "14"}}],
        probe_state="answered", now=NOW,
    )
    assert got.remaining == 14.0 and got.unit == readout.UNIT_REQUESTS
    assert got.reason == readout.REASON_READ
    assert "不是钱" in got.detail
    assert "x-ratelimit-remaining" in got.evidence["remaining"]


def test_a_dollar_field_outranks_the_request_window():
    got = readout.remaining(
        "antigravity", kind="aigw",
        accounts=[{"id": "a1", "quota": {"remaining": "14", "usd": "2.50"}}],
        probe_state="answered", now=NOW,
    )
    assert got.remaining == 2.5 and got.unit == readout.UNIT_USD


def test_multi_account_reports_the_smallest_and_says_so():
    """合数（求和？取最小？）是产品决定，这一格不许自己发明一种。"""
    got = readout.remaining(
        "antigravity", kind="aigw",
        accounts=[{"id": "a1", "quota": {"remaining": "40"}},
                  {"id": "a2", "quota": {"remaining": "6"}},
                  {"id": "a3", "quota": {"remaining": "9"}}],
        probe_state="answered", now=NOW,
    )
    assert got.remaining == 6.0
    assert "3 家报了这一位" in got.detail and "账号 a2" in got.evidence["remaining"]


def test_a_field_that_is_present_but_not_a_number_stays_none():
    got = readout.remaining(
        "antigravity", kind="aigw",
        accounts=[{"id": "a1", "quota": {"remaining": "unlimited"}}],
        probe_state="answered", now=NOW,
    )
    assert got.remaining is None and got.reason == readout.REASON_NON_NUMERIC
    assert got.unit == ""
    assert "unlimited" in got.evidence["non_numeric"]


def test_writer_present_but_quiet_is_not_the_same_answer_as_no_writer():
    quiet = readout.remaining("antigravity", kind="aigw",
                              accounts=[{"id": "a1", "quota": {}}],
                              probe_state="answered", now=NOW)
    assert quiet.remaining is None and quiet.reason == readout.REASON_NOT_REPORTED
    nobody = readout.remaining("workbuddy", kind="aigw",
                               accounts=[{"id": "b1", "quota": {}}],
                               probe_state="answered", now=NOW)
    assert nobody.remaining is None and nobody.reason == readout.REASON_NO_WRITER
    assert "antigravity.py:610-613" in nobody.evidence["remaining"]


def test_cheap_api_without_a_balance_writer_says_so_instead_of_guessing():
    unwired = readout.remaining("zhipu-air", kind="cheap_api", now=NOW)
    assert unwired.remaining is None and unwired.reason == readout.REASON_INTERFACE_UNPROBED
    assert "sources.py:150" in unwired.evidence["remaining"]

    class _Wired:
        def balance_probe_wired(self):
            return True

    wired = readout.remaining("zhipu-air", kind="cheap_api", source=_Wired(), now=NOW)
    assert wired.remaining is None and wired.reason == readout.REASON_NOT_REPORTED


def test_probe_or_source_balance_is_taken_as_real_usd_before_anything_else():
    from_probe = readout.remaining(
        "zhipu-air", kind="cheap_api",
        status=SourceStatus("zhipu-air", "cheap_api", HealthStatus.HEALTHY, 3.25, "ok", NOW),
        now=NOW,
    )
    assert from_probe.remaining == 3.25 and from_probe.unit == readout.UNIT_USD

    class _Sourced:
        def remaining_usd(self):
            return 0.5

        def balance_probe_wired(self):
            return True

    from_source = readout.remaining("zhipu-air", kind="cheap_api", source=_Sourced(), now=NOW)
    assert from_source.remaining == 0.5 and from_source.reason == readout.REASON_READ


def test_a_source_that_reports_zero_is_read_as_zero_not_as_unknown():
    class _Broke:
        def remaining_usd(self):
            return 0.0

        def balance_probe_wired(self):
            return True

    got = readout.remaining("zhipu-air", kind="cheap_api", source=_Broke(), now=NOW)
    assert got.remaining == 0.0 and got.unit == readout.UNIT_USD


def test_unknown_kind_is_the_ui_only_answer():
    got = readout.remaining("some-desktop-app", kind="", now=NOW)
    assert got.remaining is None and got.reason == readout.REASON_UI_ONLY
    assert got.unit == ""


# --- /healthz 的读与形状 -----------------------------------------------------------

def test_healthz_url_is_built_off_the_v1_suffix():
    assert readout.healthz_url("http://127.0.0.1:8000/v1") == "http://127.0.0.1:8000/healthz"
    assert readout.healthz_url("http://127.0.0.1:8000/") == "http://127.0.0.1:8000/healthz"
    assert readout.healthz_url("") == ""


class _Resp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _payload(**providers):
    return json.dumps({
        "ok": True, "version": "x",
        "providers": {name: {"capabilities": {"stream": True}, "accounts": rows}
                      for name, rows in providers.items()},
    }).encode("utf-8")


def test_fetch_health_keeps_only_the_four_account_fields():
    got = readout.fetch_health(
        "http://127.0.0.1:8000/v1", "k",
        open_url=lambda request, timeout: _Resp(
            _payload(antigravity=[{"id": "a1", "state": "ok", "fail": 0,
                                   "quota": {"remaining": "14"}, "secret": "dropme"}])),
    )
    assert got.state is gateway.CatalogState.OK
    row = got.accounts["antigravity"][0]
    assert set(row) == {"id", "state", "fail", "quota"}
    assert row["quota"] == {"remaining": "14"}
    assert "dropme" not in json.dumps(got.accounts)


def test_fetch_health_downs_a_refused_port_as_a_third_state():
    def _refused(request, timeout):
        raise urllib.error.URLError("[WinError 10061] 目标计算机积极拒绝")

    got = readout.fetch_health("http://127.0.0.1:8000/v1", "k", open_url=_refused)
    assert got.state is gateway.CatalogState.UNREACHABLE
    assert got.accounts == {} and "unreachable" in got.detail


def test_fetch_health_downs_a_body_without_providers_as_rejected():
    got = readout.fetch_health("http://127.0.0.1:8000/v1", "k",
                               open_url=lambda request, timeout: _Resp(b'{"ok": true}'))
    assert got.state is gateway.CatalogState.REJECTED


def test_fetch_health_sends_the_key_as_a_header_not_in_the_url():
    seen = {}

    def _capture(request, timeout):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        return _Resp(_payload())

    readout.fetch_health("http://127.0.0.1:8000/v1", "sekret", open_url=_capture)
    assert "sekret" not in seen["url"]
    assert seen["auth"] == "Bearer sekret"


# --- 表与判断：这一格接上熔断那条路 -------------------------------------------------

def _legs_report(*, accounts=None, state=gateway.CatalogState.OK, legs=None):
    return readout.readout(
        pool=_FakePool(["antigravity", "workbuddy"]),
        fetch=lambda base, key: _outcome({"antigravity": _aigw_source("antigravity"),
                                          "workbuddy": _aigw_source("workbuddy")}),
        health=lambda base, key: _health({"antigravity": accounts or [], "workbuddy": []}, state),
        ledger_rows=[],
        credits=lambda p: {"credits": None, "records": 0, "files": 0,
                           "skipped_no_timestamp": 0, "detail": ""},
        now=NOW,
    )


class _FakePool:
    def __init__(self, names, statuses=(), sources=None):
        self._names = list(names)
        self._statuses = list(statuses)
        self.sources = dict(sources or {})

    def names(self):
        return list(self._names)

    def statuses(self):
        return list(self._statuses)


def test_table_now_carries_a_real_number_for_the_leg_that_has_one():
    report = _legs_report(accounts=[{"id": "a1", "quota": {"remaining": "14"}}])
    by = {l["name"]: l for l in report["legs"]}
    assert by["antigravity"]["remaining"] == 14.0
    assert by["antigravity"]["remaining_unit"] == readout.UNIT_REQUESTS
    assert by["workbuddy"]["remaining"] is None
    assert by["workbuddy"]["remaining_reason"] == readout.REASON_NO_WRITER
    assert report["remaining_readable"] is True
    assert report["remaining_known"] == [{"name": "antigravity", "remaining": 14.0,
                                         "unit": readout.UNIT_REQUESTS}]


def test_unanswered_healthz_leaves_the_structural_reason_and_records_the_state():
    report = _legs_report(state=gateway.CatalogState.UNREACHABLE)
    by = {l["name"]: l for l in report["legs"]}
    assert by["workbuddy"]["remaining"] is None
    assert by["workbuddy"]["remaining_reason"] == readout.REASON_NO_WRITER
    assert by["workbuddy"]["remaining_probe_state"] == "unreachable"
    assert report["remaining_readable"] is False


def test_min_usd_blocks_only_a_leg_with_a_real_dollar_number():
    legs = [
        {"name": "usd-rich", "usable": True, "remaining": 9.0, "remaining_unit": "usd"},
        {"name": "usd-poor", "usable": True, "remaining": 0.1, "remaining_unit": "usd"},
        {"name": "window-only", "usable": True, "remaining": 0.0, "remaining_unit": "requests"},
        {"name": "unknown", "usable": True, "remaining": None, "remaining_unit": ""},
    ]
    report = {"legs": legs, "catalog_state": "ok", "remaining_readable": True}
    decision = readout.verdict(report, min_usd=1.0)
    assert decision["usable"] == ["usd-rich", "window-only", "unknown"]
    assert decision["blocked_by_balance"] == ["usd-poor"]
    assert decision["action"] == "use"
    # 未知与"限流窗口剩余 0 次"都不许被当成钱不够——那是把没读到的东西当结论。
    assert "usd-poor=0.1 usd<1.0" in decision["sentence"]


def test_stop_work_only_when_every_usable_leg_is_known_to_be_broke():
    legs = [{"name": "usd-poor", "usable": True, "remaining": 0.1, "remaining_unit": "usd"}]
    decision = readout.verdict({"legs": legs, "catalog_state": "ok",
                                "remaining_readable": True}, min_usd=1.0)
    assert decision["action"] == "stop_work"
    assert "0.1" in decision["sentence"] and "usd-poor" in decision["sentence"]
    assert readout.exit_code(decision) == 2


def test_a_leg_without_any_number_can_never_trigger_the_breaker():
    legs = [{"name": "workbuddy", "usable": True, "remaining": None, "remaining_unit": ""}]
    decision = readout.verdict({"legs": legs, "catalog_state": "ok",
                                "remaining_readable": False}, min_usd=5.0)
    assert decision["action"] == "use" and decision["blocked_by_balance"] == []


def test_reading_a_leg_does_not_probe_it():
    """原子只读：给它一个假源，不许它为了填余额去发探针（探针可能花钱）。"""
    class _NeverProbe(CheapApiSource):
        def probe(self):  # pragma: no cover - 被调到就是测红
            raise AssertionError("remaining() 不该探针")

    got = readout.remaining("zhipu-air", kind="cheap_api", source=_NeverProbe("zhipu-air"), now=NOW)
    assert got.remaining is None and got.reason == readout.REASON_INTERFACE_UNPROBED
