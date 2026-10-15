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

NOW = datetime(2026, 10, 15, 9, 0)


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
            _store(tmp_path, [_row("timetable", 0.01), _row("zju", 4.4)]), now=NOW)
        got = by_source(data)

        assert got["timetable"]["state"] == STATE_LIVE
        assert got["zju"]["state"] == STATE_STALE
        assert "1 条还在写" in data["verdict"]

    def test_a_registered_source_that_never_landed_still_gets_a_row(self, tmp_path):
        """信源在注册表里就必须出现在读数里 —— 静默省略就是"看着像没有这个信源"。"""
        data = read_source_liveness(_store(tmp_path, [_row("timetable", 0)]),
                                    now=NOW, expected_sources=("timetable", "wechat"))
        got = by_source(data)

        assert got["wechat"]["state"] == STATE_NEVER
        assert got["wechat"]["events"] == 0

    def test_everything_stale_says_the_chain_is_not_running(self, tmp_path):
        data = read_source_liveness(_store(tmp_path, [_row("zju", 9), _row("manual", 5)]), now=NOW)

        assert data["verdict"] == "每一条信源都超时没进新东西 —— 采集没有在跑"

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

    def test_updated_at_decides_liveness_even_without_new_rows(self, tmp_path):
        """重拉会刷新 updated_at：只按 created_at 判会把"活着但没变更"误报成停了。"""
        db = tmp_path / "agenda.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE events (source TEXT, created_at TEXT, updated_at TEXT)")
        conn.execute("INSERT INTO events VALUES (?, ?, ?)",
                     ("timetable", (NOW - timedelta(days=30)).isoformat(timespec="seconds"),
                      (NOW - timedelta(minutes=5)).isoformat(timespec="seconds")))
        conn.commit()
        conn.close()

        assert by_source(read_source_liveness(db, now=NOW))["timetable"]["state"] == STATE_LIVE


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
