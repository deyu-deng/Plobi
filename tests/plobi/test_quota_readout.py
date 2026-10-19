"""R-108 只读口的测：能不能用 / 今天花了多少 / 还剩多少——**读不到的那格必须是空，不许是估算值**。

全篇零额度：`fetch`、`credits`、`ledger_rows` 三个注入位把外部读数全部替掉，
一次真补全都不发，也不碰 `$PLOBI_HOME` 里的真账本与真流水。
"""

from __future__ import annotations

import json
import os
import time

import pytest

from plobi.quota import gateway, readout
from plobi.quota.sources import HealthStatus, SourceStatus

NOW = time.time()
TODAY_START = readout._today_start(NOW)


class _FakePool:
    """只当读数袋子：readout 用 names()、statuses()，以及注册表里的 kind（不探针）。"""

    def __init__(self, names, statuses=(), sources=None):
        self._names = list(names)
        self._statuses = list(statuses)
        self.sources = dict(sources or {})

    def names(self):
        return list(self._names)

    def statuses(self):
        return list(self._statuses)


def test_kind_comes_from_the_registry_without_forcing_a_probe():
    """没探针时也要说得出这条腿是哪一档——但读口不许为了填这一格去发网络请求。"""

    class _Cheap:
        kind = "cheap_api"

    report = readout.readout(
        pool=_FakePool(["zhipu-air"], sources={"zhipu-air": _Cheap()}),
        fetch=lambda base, key: _outcome({}),
        health=lambda base, key: _health(),
        ledger_rows=[],
        credits=lambda p: {"credits": None, "records": 0, "files": 0, "skipped_no_timestamp": 0, "detail": ""},
        now=NOW,
    )
    leg = report["legs"][0]
    assert leg["kind"] == "cheap_api"
    assert leg["usable"] is None and leg["usable_reason"] == "not_probed"


def _health(accounts=None, state=gateway.CatalogState.OK, detail="health ok"):
    return readout.HealthOutcome(state, accounts or {}, detail, NOW)


def _outcome(sources, state=gateway.CatalogState.OK, detail="ok"):
    return gateway.GatewayOutcome(state, sources, detail, NOW)


def _aigw_source(selectable=True, live=True, live_reason="ok", unselectable=""):
    return {
        "kind": "aigw",
        "model": "workbuddy/glm-5.3",
        "models": ["workbuddy/glm-5.3"],
        "base_url": "http://127.0.0.1:8000/v1",
        "api_key": "k",
        "capabilities": {"stream": True},
        "derived": True,
        "availability": {
            "live": live,
            "selectable": selectable,
            "live_reason": live_reason,
            "unselectable_reason": unselectable,
            "models": 1,
        },
    }


def test_leg_says_it_is_usable_from_the_catalogs_own_flags():
    legs = readout.readout(
        pool=_FakePool(["workbuddy"]),
        fetch=lambda base, key: _outcome({"workbuddy": _aigw_source()}),
        health=lambda base, key: _health(),
        ledger_rows=[],
        credits=lambda p: {"credits": 12.5, "records": 3, "files": 1, "skipped_no_timestamp": 0, "detail": ""},
        now=NOW,
    )["legs"]
    assert len(legs) == 1
    leg = legs[0]
    assert leg["usable"] is True and leg["usable_reason"] == "catalog_selectable"
    assert leg["spent_today"] == 12.5 and leg["spent_unit"] == "credits"
    assert leg["spent_source"] == "vendor_transcript_credit"


def test_remaining_is_empty_on_every_leg_and_says_why_not_read():
    report = readout.readout(
        pool=_FakePool(["workbuddy", "zhipu-air"],
                       [SourceStatus("zhipu-air", "cheap_api", HealthStatus.DEGRADED, None,
                                     "configured but balance probe not wired", NOW)]),
        fetch=lambda base, key: _outcome({"workbuddy": _aigw_source()}),
        health=lambda base, key: _health(),
        ledger_rows=[],
        credits=lambda p: {"credits": None, "records": 0, "files": 0, "skipped_no_timestamp": 0,
                           "detail": "落点读不到"},
        now=NOW,
    )
    assert report["remaining_readable"] is False
    for leg in report["legs"]:
        assert leg["remaining"] is None, leg
        assert leg["remaining_reason"] in readout.REMAINING_REASONS
    by = {l["name"]: l for l in report["legs"]}
    assert by["workbuddy"]["remaining_reason"] == readout.REASON_NO_WRITER
    assert by["zhipu-air"]["remaining_reason"] == readout.REASON_INTERFACE_UNPROBED
    # 「没读到」不能写成 0——0 会被上层读成"额度已耗尽"，而这是台读不到的那格。
    assert by["workbuddy"]["spent_today"] is None
    assert report["totals"]["spent_today_credits"] is None


def test_spent_from_our_own_ledger_counts_only_priced_successful_rows_today():
    rows = [
        {"ok": True, "cost": 0.4, "ts": TODAY_START + 60, "source": "zhipu-air"},
        {"ok": True, "cost": 0.6, "ts": TODAY_START + 90, "source": "zhipu-air"},
        {"ok": False, "cost": 5.0, "ts": TODAY_START + 90, "source": "zhipu-air"},
        {"ok": True, "cost": None, "ts": TODAY_START + 90, "source": "zhipu-air"},
        {"ok": True, "cost": 9.9, "ts": TODAY_START - 86_400, "source": "zhipu-air"},
    ]
    legs = readout.readout(
        pool=_FakePool(["zhipu-air"],
                       [SourceStatus("zhipu-air", "cheap_api", HealthStatus.HEALTHY, 3.0, "ok", NOW)]),
        fetch=lambda base, key: _outcome({}),
        health=lambda base, key: _health(),
        ledger_rows=rows,
        credits=lambda p: {"credits": None, "records": 0, "files": 0, "skipped_no_timestamp": 0, "detail": ""},
        now=NOW,
    )["legs"]
    assert legs[0]["spent_today"] == pytest.approx(1.0)
    assert legs[0]["spent_source"] == "spend_ledger_cost"


def test_unselectable_leg_is_reported_with_the_vendors_reason_not_a_guess():
    legs = readout.readout(
        pool=_FakePool(["workbuddy"]),
        fetch=lambda base, key: _outcome(
            {"workbuddy": _aigw_source(selectable=False, live=False, live_reason="credential_expired",
                                       unselectable="credential_expired")}),
        health=lambda base, key: _health(),
        ledger_rows=[],
        credits=lambda p: {"credits": None, "records": 0, "files": 0, "skipped_no_timestamp": 0, "detail": ""},
        now=NOW,
    )["legs"]
    assert legs[0]["usable"] is False
    assert legs[0]["usable_reason"] == "credential_expired"
    assert readout.verdict({"legs": legs, "catalog_state": "ok", "remaining_readable": False})["action"] == "stop_work"


def test_unreachable_gateway_is_a_third_answer_not_stop_work():
    report = readout.readout(
        pool=_FakePool([]),
        fetch=lambda base, key: _outcome({}, gateway.CatalogState.UNREACHABLE,
                                         "http://127.0.0.1:8000/v1/models unreachable"),
        health=lambda base, key: _health(),
        ledger_rows=[],
        credits=lambda p: {"credits": None, "records": 0, "files": 0, "skipped_no_timestamp": 0, "detail": ""},
        now=NOW,
    )
    decision = readout.verdict(report)
    assert decision["action"] == "probe_again"
    assert "读不到" in decision["sentence"]


def test_verdict_offers_a_leg_when_at_least_one_is_up():
    report = readout.readout(
        pool=_FakePool(["workbuddy", "zhipu-air"],
                       [SourceStatus("zhipu-air", "cheap_api", HealthStatus.UNAVAILABLE, None,
                                     "no credential configured", NOW)]),
        fetch=lambda base, key: _outcome({"workbuddy": _aigw_source()}),
        health=lambda base, key: _health(),
        ledger_rows=[],
        credits=lambda p: {"credits": 4.0, "records": 1, "files": 1, "skipped_no_timestamp": 0, "detail": ""},
        now=NOW,
    )
    decision = readout.verdict(report)
    assert decision["action"] == "use"
    assert decision["usable"] == ["workbuddy"]
    assert decision["remaining_is_known"] is False


def test_exit_code_is_the_shape_a_scheduled_job_can_act_on():
    """三个码各管一件事；"这次没读数"绝不能压成 2（那会被读成"确认没额度了"）。"""
    assert readout.exit_code({"action": "use"}) == 0
    assert readout.exit_code({"action": "probe_again"}) == 1
    assert readout.exit_code({"action": "stop_work"}) == 2
    assert readout.exit_code({}) == 1


def test_readout_touches_no_network_when_the_four_seams_are_injected(monkeypatch):
    def _no_network(*args, **kwargs):  # 真发一次就算测红
        raise AssertionError("readout 不该自己去找网络")

    monkeypatch.setattr(gateway, "fetch_sources", _no_network)
    monkeypatch.setattr(readout, "credits_today", _no_network)
    monkeypatch.setattr(readout, "fetch_health", _no_network)
    report = readout.readout(
        pool=_FakePool(["workbuddy"]),
        fetch=lambda base, key: _outcome({"workbuddy": _aigw_source()}),
        health=lambda base, key: _health(),
        ledger_rows=[],
        credits=lambda p: {"credits": None, "records": 0, "files": 0, "skipped_no_timestamp": 0, "detail": ""},
        now=NOW,
    )
    assert report["catalog_state"] == "ok"


def test_credits_today_reads_the_clients_own_transcript(tmp_path):
    env = {"CODEBUDDY_CONFIG_DIR": str(tmp_path)}
    projects = tmp_path / "projects" / "d-Data-AppData-plobi"
    projects.mkdir(parents=True)
    lines = [
        {"timestamp": int(TODAY_START * 1000) + 1000, "providerData": {"rawUsage": {"credit": 2.5}}},
        {"timestamp": int(TODAY_START * 1000) + 2000, "providerData": {"rawUsage": {"credit": 1.25}}},
        {"timestamp": int((TODAY_START - 86_400) * 1000), "providerData": {"rawUsage": {"credit": 99.0}}},
    ]
    path = projects / "s1.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    os.utime(path, (TODAY_START + 10, TODAY_START + 10))

    got = readout.credits_today("workbuddy", now=NOW, env=env)
    assert got["credits"] == 3.75, got
    assert got["records"] == 2 and got["files"] == 1
    assert got["skipped_no_timestamp"] == 0
    # 只交记账那几位字段：流水行里有对话正文，这张表不许把它们抄出来。
    assert set(got) == {"provider", "credits", "records", "files", "skipped_no_timestamp", "detail"}


def test_credits_today_reports_why_it_has_no_number(tmp_path):
    got = readout.credits_today("workbuddy", now=NOW, env={"CODEBUDDY_CONFIG_DIR": str(tmp_path / "nope")})
    assert got["credits"] is None and got["detail"]
    unknown = readout.credits_today("some-other-app", now=NOW, env={})
    assert unknown["credits"] is None and "没有为" in unknown["detail"]


def test_catalog_availability_survives_the_grouping_by_provider():
    payload = {"data": [
        {"id": "workbuddy/a", "provider": "workbuddy", "capabilities": {}, "live": False,
         "selectable": False, "live_reason": "credential_expired", "unselectable_reason": "credential_expired"},
        {"id": "workbuddy/b", "provider": "workbuddy", "capabilities": {}, "live": True,
         "selectable": True, "live_reason": "ok"},
        {"id": "mock/echo", "provider": "mock", "capabilities": {}},
    ]}
    sources, seen = gateway.sources_from_catalog(payload, base_url="u", api_key="k")
    assert seen == 3 and list(sources) == ["workbuddy"]
    avail = sources["workbuddy"]["availability"]
    assert avail["selectable"] is True and avail["live"] is True
    assert avail["models"] == 2, "一条活模型不该把整家判死，也不许把 mock 算进来"
