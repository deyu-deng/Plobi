"""信源活性探针：只许报"最后一次真落库在什么时候"，不许把"配置齐了"说成"在跑"。

跑法：`bash scripts/run_tests.sh tests/plobi/test_collect_liveness.py -q`（唯一入口，不传 `--basetemp`）。
全是关系断言：给定最后写入时刻 ⇒ 状态与那句总结必须怎么变，不冻任何真实读数。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plobi.collectors import liveness
from plobi.collectors.liveness import (
    STATE_LIVE,
    STATE_NEVER,
    STATE_NO_STORE,
    STATE_STALE,
    note_for,
    read_source_liveness,
)

NOW = datetime.now()
#: ↑ 原来这里硬编 `datetime(2026, 10, 15, 9, 0)`。判活走的是**相对**关系（几天前算 stale），
#: 而 `TestEndpoint` 那一档调的是 HTTP 路由——路由内部读的是**真钟**，注入不进去。
#: 于是这条测试从 10-18 09:00 起就必然红（今天现红：`assert 'stale' == 'live'`）。
#: 参照系改成"跑测试的那一刻"之后，断言的还是同一个关系，只是不再过保质期。


def _store(tmp_path, rows):
    db = tmp_path / "agenda.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE events (source TEXT, created_at TEXT, updated_at TEXT)")
    conn.executemany("INSERT INTO events VALUES (?,?,?)", rows)
    conn.commit()
    conn.close()
    return db


def _row(source, days_ago):
    ts = (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")
    return (source, ts, ts)


def by_source(data):
    return {s["source"]: s for s in data["sources"]}


class TestStates:
    def test_recent_write_is_live_old_one_is_stale(self, tmp_path):
        data = read_source_liveness(
            _store(tmp_path, [_row("timetable", 0.01), _row("zju", 4.4)]), now=NOW,
            switches={"timetable": True, "zju": True})
        got = by_source(data)

        assert got["timetable"]["state"] == STATE_LIVE
        assert got["zju"]["state"] == STATE_STALE
        assert "1 条还在进新条目" in data["verdict"]

    def test_a_registered_source_that_never_landed_still_gets_a_row(self, tmp_path):
        """信源在注册表里就必须出现在读数里 —— 静默省略就是"看着像没有这个信源"。"""
        data = read_source_liveness(_store(tmp_path, [_row("timetable", 0)]),
                                    now=NOW, expected_sources=("timetable", "wechat"),
                                    switches={"timetable": True, "wechat": True})
        got = by_source(data)

        assert got["wechat"]["state"] == STATE_NEVER
        assert got["wechat"]["events"] == 0

    def test_everything_stale_says_the_chain_is_not_running(self, tmp_path):
        data = read_source_liveness(_store(tmp_path, [_row("zju", 9), _row("manual", 5)]), now=NOW,
                                     switches={"zju": True, "manual": True})

        assert data["verdict"] == "每一条信源都超时没进新条目 —— 没有东西进来（本地改动不算）"

    def test_missing_store_refuses_to_speak(self, tmp_path):
        data = read_source_liveness(tmp_path / "不在这里.db", now=NOW, expected_sources=("zju",))
        got = by_source(data)

        assert got["zju"]["state"] == STATE_NO_STORE
        assert "什么都不能说" in data["verdict"]

    def test_unreadable_store_is_no_store_not_zero_rows(self, tmp_path):
        broken = tmp_path / "agenda.db"
        broken.write_text("这不是 sqlite 文件", encoding="utf-8")

        data = read_source_liveness(broken, now=NOW, expected_sources=("zju",))

        assert by_source(data)["zju"]["state"] == STATE_NO_STORE
        assert "读不动" in data["verdict"]

    def test_a_local_edit_does_not_make_a_stale_source_look_live(self, tmp_path):
        """这台 2026-10-15 量到的反例，钉成回归：`updated_at` 新 ≠ 还在采。

        现场形状：`timetable` 240 行的 `created_at` 全停在 10-09，只有**一行**当天 08:43 被确认，
        于是 `MAX(updated_at)` 是"0.01 天前"。按 updated 判就把"有人在动板"报成"有人在采"。
        """
        db = tmp_path / "agenda.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE events (source TEXT, created_at TEXT, updated_at TEXT)")
        conn.executemany("INSERT INTO events VALUES (?, ?, ?)", [
            ("timetable", (NOW - timedelta(days=30)).isoformat(timespec="seconds"),
             (NOW - timedelta(days=30)).isoformat(timespec="seconds")),
            ("timetable", (NOW - timedelta(days=29)).isoformat(timespec="seconds"),
             (NOW - timedelta(minutes=5)).isoformat(timespec="seconds")),
        ])
        conn.commit()
        conn.close()

        row = by_source(read_source_liveness(db, now=NOW))["timetable"]

        assert row["state"] == STATE_STALE                 # 判活只认最后一次进新条目
        assert row["touched_after_last_write"] is True     # 但面上要说得出"板被人动过"
        assert row["events"] == 2

    def test_a_fresh_row_is_not_reported_as_edited_after_write(self, tmp_path):
        """没有本地改动时不编那一格：created 与 updated 同刻 ⇒ 只说进了新条目。"""
        row = by_source(read_source_liveness(_store(tmp_path, [_row("timetable", 0)]), now=NOW))["timetable"]

        assert row["state"] == STATE_LIVE
        assert row["touched_after_last_write"] is False


class TestCollectingSwitch:
    """那一格只报"注册表点没点开关"，并把读不到与没点分两种颜色说。"""

    def test_a_stale_source_whose_switch_is_off_is_named_in_the_verdict(self, tmp_path):
        data = read_source_liveness(_store(tmp_path, [_row("zju", 9)]), now=NOW, switches={"zju": False})

        assert by_source(data)["zju"]["collecting"] is False
        assert "连采集开关都没点" in data["verdict"] and "zju" in data["verdict"]

    def test_an_unregistered_source_is_not_reported_as_switched_off(self, tmp_path):
        """`manual` 这种不在注册表里的条目 ⇒ 那一格是 `None`，不许被算进"没点开关"。

        读不到（`None`）说成假，就是下一颗假绿的形状。
        """
        data = read_source_liveness(_store(tmp_path, [_row("manual", 9)]), now=NOW, switches={})
        row = by_source(data)["manual"]

        assert row["collecting"] is None
        assert "开关" not in data["verdict"]

    def test_the_switch_comes_from_the_registry_not_a_copy_in_here(self, tmp_path, monkeypatch):
        import types

        from plobi.collectors import registry

        monkeypatch.setattr(registry, "source_ids", lambda: ("timetable", "zju"))
        monkeypatch.setattr(registry, "spec",
                            lambda sid: types.SimpleNamespace(collecting=(sid == "timetable")))

        data = read_source_liveness(_store(tmp_path, [_row("timetable", 9), _row("zju", 9)]), now=NOW)
        got = by_source(data)

        assert got["timetable"]["collecting"] is True
        assert got["zju"]["collecting"] is False


class TestNotes:
    @pytest.mark.parametrize("state", (STATE_LIVE, STATE_STALE, STATE_NEVER, STATE_NO_STORE))
    def test_every_state_has_a_sentence(self, state):
        assert note_for(state) != state
        assert note_for(state).strip()

    def test_an_unknown_state_is_not_invented(self):
        note = note_for("green")

        assert "没记到" in note and "green" in note


class TestEndpoint:
    def test_sources_route_lives_under_the_existing_prefix(self, tmp_path, monkeypatch):
        """不开新前缀（§3.3），且探针走的是真读路：把 PLOBI_HOME 指到临时库上。"""
        import plobi_constants
        from plobi.collectors.chatlog.collect_api import router

        _store(tmp_path, [_row("timetable", 0), _row("zju", 6)])
        monkeypatch.setattr(plobi_constants, "get_plobi_home", lambda: tmp_path)
        (tmp_path / "plobi").mkdir(exist_ok=True)
        (tmp_path / "plobi" / "agenda.db").write_bytes((tmp_path / "agenda.db").read_bytes())

        app = FastAPI()
        app.include_router(router, prefix="/api/collect")
        body = TestClient(app).get("/api/collect/sources").json()

        assert body["ok"] is True
        got = by_source(body["data"])
        assert got["timetable"]["state"] == STATE_LIVE
        assert got["zju"]["state"] == STATE_STALE

    def test_liveness_never_invents_a_registry_source(self, tmp_path, monkeypatch):
        from plobi.collectors import registry
        from plobi.collectors.liveness_api import router

        _store(tmp_path, [_row("timetable", 0)])
        monkeypatch.setattr(registry, "source_ids", lambda: ("timetable", "wechat", "dingtalk"))

        app = FastAPI()
        app.include_router(router)
        body = TestClient(app).get("/sources").json()

        assert set(by_source(body["data"])) == {"timetable", "wechat", "dingtalk"}
