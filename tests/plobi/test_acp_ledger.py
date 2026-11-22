"""纯 ACP 客席的会话面与续接（``plobi/delegation/acp_ledger.py``）。

那一档的规矩只有两条，这里逐条盯住：

1. **派进去的活要在会话面上出现**（读不动它们的库，就得我们自己记）；
2. **能不能续不靠猜**：客席宣告过 ``loadSession`` 才说能接，没宣告就照实退。

会话面本身（``external_sessions.all_sessions``）与发货那条腿（``send_message``）
在最后两条里各验一遍：台账不是另开一摊，它得真接在那两处上。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from plobi.delegation import acp_ledger as ledger  # noqa: E402
from plobi.delegation import external_sessions as es  # noqa: E402

SESSION_ID = "7f3c1a2e-9b44-4d0e-8a51-2c6f1d5e9a30"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """钉住的 PLOBI_HOME + 一份空台账（不碰这台机器上真记过的东西）。"""
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path / "home"))
    monkeypatch.delenv(ledger.ENV_LEDGER, raising=False)
    monkeypatch.setenv(ledger.ENV_LEDGER, str(tmp_path / "home" / "acp_sessions.json"))

    return tmp_path


class TestOneLinePerSession:
    def test_a_dispatch_leaves_a_line_the_surface_can_show(self, home):
        """派出去了就得留得下：家、号、目录、第一句、回来的话。"""
        ledger.record("goose", SESSION_ID, project="/work/demo", title="算一下这批",
                      purpose="试算", reply="算完了", source="acp.sessionId")

        rows = ledger.as_sessions()

        assert len(rows) == 1
        row = rows[0]
        assert (row["vendor"], row["id"]) == ("goose", SESSION_ID)
        assert row["title"] == "算一下这批"
        assert row["project"] == "/work/demo"
        assert row["via"] == "acp"
        assert row["turns"] == 1

    def test_saying_again_edits_that_line_instead_of_opening_a_new_one(self, home):
        """接着说改的是同一行：第一句留下的标题不许被后来的话盖掉。"""
        ledger.record("goose", SESSION_ID, project="/work/demo", title="算一下这批",
                      reply="第一句", source="acp.sessionId")
        ledger.record("goose", SESSION_ID, reply="第二句")

        rows = ledger.as_sessions()

        assert len(rows) == 1
        assert rows[0]["title"] == "算一下这批"
        assert rows[0]["turns"] == 2
        assert ledger.find("goose", SESSION_ID)["reply"] == "第二句"

    def test_a_blank_session_id_writes_nothing(self, home):
        """号都没有就别记：记一条空号等于给会话面造一条点不动的假会话。"""
        assert ledger.record("goose", "  ") is None
        assert ledger.as_sessions() == []


class TestResumeIsNotGuessed:
    def test_never_tried_stays_clickable_and_says_so(self, home):
        """没试过的照实标 ``unknown``：号在我们手上，点了才知道接不接得上。"""
        ledger.record("droid", SESSION_ID, project="/work/demo", reply="好的")

        row = ledger.as_sessions()[0]

        assert row["resume_state"] == ledger.RESUME_UNKNOWN
        assert row["sendable"] is True

    def test_one_that_refused_to_load_is_not_dressed_as_sendable(self, home):
        """宣告过没有 ``loadSession`` 的，就别再让它看着像能点。"""
        ledger.record("droid", SESSION_ID, project="/work/demo", resume_state=ledger.RESUME_NO)

        row = ledger.as_sessions()[0]

        assert row["resume_state"] == ledger.RESUME_NO
        assert row["sendable"] is False

    def test_a_ledger_that_cannot_be_read_does_not_take_the_surface_down(self, home):
        """台账坏了是台账的事：会话面返回空，不抛。"""
        path = ledger.ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ 这不是 json", encoding="utf-8")

        assert ledger.as_sessions() == []
        assert ledger.rows() == []


class TestTheSurfaceAndTheSendLegActuallyUseIt:
    def test_the_surface_shows_what_we_dispatched(self, home, monkeypatch):
        """会话面 = 读得动的那些 + 我们自己账上的那些（缺这一格就是「派出去看不见」）。"""
        monkeypatch.setattr(es, "SESSION_SPECS", {})
        es._ALL_CACHE["rows"] = []
        es._ALL_CACHE["at"] = 0.0
        ledger.record("junie", SESSION_ID, project="/work/demo", title="看看这个仓库", reply="看过了")

        rows = es.all_sessions()

        assert [item["id"] for item in rows] == [SESSION_ID]
        assert es._locate("junie", SESSION_ID) is not None

    def test_sending_goes_through_session_load_and_marks_it_ok(self, home, monkeypatch):
        """接着说走的是 ACP ``session/load``，接上了就把这行改判成能续。"""
        ledger.record("junie", SESSION_ID, project="/work/demo", title="看看这个仓库")
        calls: list[dict] = []

        def fake_run_task(task, *, cwd=None, agent=None, session_id="", **_kwargs):
            calls.append({"task": task, "cwd": cwd, "agent": agent, "session_id": session_id})
            return SimpleNamespace(reply="看完了", error="", resumed=True,
                                   external_session=session_id, external_session_source="acp.load")

        monkeypatch.setattr("agent.external_offload.run_task", fake_run_task, raising=False)

        sent = es.send_message("junie", {"id": SESSION_ID, "project": "/work/demo"}, "接着看一下测试")

        assert sent["ok"] is True
        assert sent["data"]["via"] == "acp"
        assert calls == [{"task": "接着看一下测试", "cwd": "/work/demo",
                          "agent": "junie", "session_id": SESSION_ID}]
        assert ledger.find("junie", SESSION_ID)["resume_state"] == ledger.RESUME_OK

    def test_one_that_will_not_load_is_recorded_and_reported_honestly(self, home, monkeypatch):
        """它不接就不许假装接上：报错照实给，这一行改判成 ``no``。"""
        ledger.record("junie", SESSION_ID, project="/work/demo", title="看看这个仓库")

        def fake_run_task(_task, *, session_id="", **_kwargs):
            return SimpleNamespace(reply="", resumed=False,
                                   error="这家没有宣告 loadSession，接不回那条会话",
                                   external_session=session_id, external_session_source="acp.sessionId")

        monkeypatch.setattr("agent.external_offload.run_task", fake_run_task, raising=False)

        sent = es.send_message("junie", {"id": SESSION_ID, "project": "/work/demo"}, "接着说")

        assert sent["ok"] is False
        assert "loadSession" in sent["error"]
        assert ledger.find("junie", SESSION_ID)["resume_state"] == ledger.RESUME_NO
        assert ledger.as_sessions()[0]["sendable"] is False


class TestTheSettingsScreenSeesThemToo:
    def test_a_ledger_only_vendor_gets_a_row_that_says_both_truths(self, home, monkeypatch):
        """读不动它的库、会话号在我们账上——两句都得摆出来，缺一句就是骗人。"""
        monkeypatch.setattr(es, "SESSION_SPECS", {})
        es._ALL_CACHE["rows"] = []
        es._ALL_CACHE["at"] = 0.0
        ledger.record("nova", SESSION_ID, project="/work/demo", title="跑个回归", reply="跑完了")

        rows = {item["vendor"]: item for item in es.session_sources()}

        assert rows["nova"]["readable"] is False
        assert "账上" in rows["nova"]["reason"]
        assert rows["nova"]["sessions"] == 1
        assert rows["nova"]["sendable"] is True

    def test_one_that_refused_to_load_is_listed_as_not_sendable(self, home, monkeypatch):
        monkeypatch.setattr(es, "SESSION_SPECS", {})
        es._ALL_CACHE["rows"] = []
        es._ALL_CACHE["at"] = 0.0
        ledger.record("nova", SESSION_ID, project="/work/demo", resume_state=ledger.RESUME_NO)

        rows = {item["vendor"]: item for item in es.session_sources()}

        assert rows["nova"]["sendable"] is False


def test_the_ledger_file_is_readable_by_a_human(home):
    """这东西坏了要有人能一眼看懂：UTF-8、缩进、中文不转义。"""
    ledger.record("glm", SESSION_ID, project="/work/演示", title="演示一下", reply="好")

    payload = json.loads(ledger.ledger_path().read_text(encoding="utf-8"))

    assert payload["version"] == ledger.VERSION
    assert "演示一下" in ledger.ledger_path().read_text(encoding="utf-8")
