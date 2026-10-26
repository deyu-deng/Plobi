r"""那份"哪些会话采"的名单，怎么从一条按信源声明走的路由里出与入。

跑法：`bash scripts/run_tests.sh tests/plobi/test_source_conversations_api.py -q`

这一档钉的四句，全是屏上那句人话的判据：

1. **三家分得开**：`ok`（拿到了，0 行也是真话）／`unavailable`（有这层，现在拿不到）／
   `not_supported`（这条压根没有会话这一层）。把它们压成一种"空"，就是下一句"我以为它没东西"
   ——微信那一路今年早上就因为这个报过一次"采集器活着"而端口零监听。
2. **读手抛异常不许变 500**：同一族昨天刚因为 `AccessDenied` 穿过 HTTP 边界，让用户点一下
   开关拿到一个 500。那颗雷的形状是"异常从声明里长出来"，所以这里两条都测（列表抛 / 写抛）。
3. **公共层不认识任何信源的名字**：微信与钉钉共用这一条路由，只因为它们各自声明了那两手；
   第三方包同样。所以这里造一条假信源就能验完整形状，不必借哪一家。
4. **写进去必须读得回来**（`Ledger` 那个 `_conn` 曾经只 `close` 不 `commit`，
   于是排除名单静默消失、下一轮照采——界面上看像是"按了没反应"，是最难查的一种坏）。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plobi.collectors import registry, sources_api, talkers

FAKE = "fakecampus-talkers"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path))
    monkeypatch.setenv("PLOBI_TALKERS_DB", str(tmp_path / "talkers.db"))
    monkeypatch.setenv("PLOBI_DINGTALK_DATA_DIR", str(tmp_path / "DingTalk"))
    monkeypatch.setenv("PLOBI_DINGTALK_LOCAL_DIR", str(tmp_path / "ding-local"))


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(sources_api.router, prefix="/api/collect")
    return TestClient(app)


@pytest.fixture()
def declared():
    """一条声明了那两手的假信源；名单与决定都在测试进程里，不碰盘也不碰侧车。"""
    rows = [{"id": "room-1", "kind": "group", "name": "工程组", "status": "pending"},
            {"id": "p-1", "kind": "private", "name": "李四", "status": "pending"}]
    written: list[tuple[str, str]] = []

    spec = registry.SourceSpec(
        FAKE, capabilities=dict.fromkeys(registry.CAPABILITY_KEYS, False), category="message",
        conversation_scoped=True, conversations=lambda: rows,
        admission=lambda talker, status: written.append((talker, status)))
    registry.register(spec, replace=True)
    try:
        yield rows, written
    finally:
        registry.unregister(FAKE)


def data(response) -> dict:
    return response.json()["data"]


class TestTheThreeAnswers:
    def test_a_source_that_declared_nothing_says_not_supported(self, client) -> None:
        """网站/邮箱那一族没有"会话"这一层——这句要说出来，不许画成一份空名单。"""
        body = data(client.get("/api/collect/sources/zdbk/conversations"))

        assert body["state"] == "not_supported"
        assert body["conversations"] == []
        assert body["reason"]

    def test_an_empty_list_under_ok_is_a_different_sentence(self, client, declared) -> None:
        rows, _ = declared
        rows.clear()

        body = data(client.get(f"/api/collect/sources/{FAKE}/conversations"))

        assert (body["state"], body["conversations"]) == ("ok", [])

    def test_a_source_that_is_not_in_the_registry_is_404_not_an_empty_list(self, client) -> None:
        assert client.get("/api/collect/sources/no-such-source/conversations").status_code == 404

    def test_a_raising_lister_becomes_a_reading_not_a_500(self, client) -> None:
        def boom() -> list:
            raise RuntimeError("侧车没在应答")

        spec = registry.SourceSpec("raiser", capabilities=dict.fromkeys(registry.CAPABILITY_KEYS, False),
                                   category="message", conversation_scoped=True, conversations=boom)
        registry.register(spec, replace=True)

        try:
            response = client.get("/api/collect/sources/raiser/conversations")
            body = data(response)
        finally:
            registry.unregister("raiser")

        assert response.status_code == 200, "读不到是读数，不是服务器错了"
        assert body["state"] == "unavailable" and "侧车没在应答" in body["reason"]

    def test_a_lister_returning_nothing_when_unavailable_does_not_look_empty(self, client) -> None:
        """`unavailable` 与 `ok + 0 行`在界面上落两句不同的话，所以后端必须先分清楚。"""
        spec = registry.SourceSpec("silent", capabilities=dict.fromkeys(registry.CAPABILITY_KEYS, False),
                                   category="message", conversation_scoped=True,
                                   conversations=lambda: (_ for _ in ()).throw(OSError("盘读不到")))
        registry.register(spec, replace=True)

        try:
            assert data(client.get("/api/collect/sources/silent/conversations"))["state"] == "unavailable"
        finally:
            registry.unregister("silent")


class TestDecisions:
    def test_a_decision_is_written_and_comes_back_on_the_same_response(self, client, declared) -> None:
        _, written = declared

        body = data(client.post(f"/api/collect/sources/{FAKE}/conversations",
                                json={"id": "room-1", "status": "excluded"}))

        assert ("room-1", "excluded") in written
        assert body["state"] == "ok", "写完立刻把这份名单带回去，界面不必再读一次"

    def test_an_unknown_source_or_hand_is_refused_with_a_reason(self, client, declared) -> None:
        assert client.post("/api/collect/sources/zdbk/conversations",
                           json={"id": "x", "status": "excluded"}).status_code == 400
        assert client.post(f"/api/collect/sources/{FAKE}/conversations",
                           json={"id": "x", "status": "maybe"}).status_code == 400
        assert client.post(f"/api/collect/sources/{FAKE}/conversations",
                           json={"id": "  ", "status": "excluded"}).status_code == 400

    def test_a_hand_that_cannot_write_says_503_not_ok(self, client) -> None:
        def broken(talker: str, status: str) -> None:
            raise OSError("名册写不进去")

        spec = registry.SourceSpec("writefail", capabilities=dict.fromkeys(registry.CAPABILITY_KEYS, False),
                                   category="message", conversation_scoped=True,
                                   conversations=lambda: [], admission=broken)
        registry.register(spec, replace=True)

        try:
            assert client.post("/api/collect/sources/writefail/conversations",
                               json={"id": "x", "status": "excluded"}).status_code == 503
        finally:
            registry.unregister("writefail")

    def test_a_source_with_only_one_hand_is_refused_at_declaration(self) -> None:
        """只声明"写决定"却不给名单 = 界面上两枚按得动但永远没有行的按钮。"""
        with pytest.raises(ValueError, match="名单从哪来"):
            registry.SourceSpec("onehand", capabilities=dict.fromkeys(registry.CAPABILITY_KEYS, False),
                                category="message", admission=lambda talker, status: None)

    def test_claiming_conversation_scoping_without_a_lister_is_refused(self) -> None:
        with pytest.raises(ValueError, match="给不出名单"):
            registry.SourceSpec("liar", capabilities=dict.fromkeys(registry.CAPABILITY_KEYS, False),
                                category="message", conversation_scoped=True)


class TestTheLedger:
    """通用那只名册。它的全部职责就一句：**写进去的必须读得回来**。"""

    def test_an_undecided_conversation_collects(self) -> None:
        ledger = talkers.Ledger()

        assert ledger.status_of("dingtalk", "room-1") == "pending"
        assert ledger.excluded("dingtalk") == set()

    def test_a_write_survives_a_new_reader_object(self) -> None:
        """这条测的是"提交"本身：`_conn` 只 `close` 不 `commit` 时，插入被回滚，
        而**同一个对象内**读得回来（所以内存里的假测试照样绿）——只有换一个连接才露馅。"""
        talkers.Ledger().set_status("dingtalk", "room-1", "excluded")

        assert talkers.Ledger().excluded("dingtalk") == {"room-1"}
        assert talkers.Ledger().status_of("dingtalk", "room-1") == "excluded"

    def test_the_key_is_per_source(self) -> None:
        """微信与钉钉里同一个号（wxid 形状与 cid 形状会撞）不许互相顶掉。"""
        talkers.Ledger().set_status("dingtalk", "4242", "excluded")

        assert talkers.Ledger().excluded("wechat") == set()

    def test_going_back_to_pending_removes_the_row(self) -> None:
        ledger = talkers.Ledger()
        ledger.set_status("dingtalk", "room-1", "known")

        ledger.set_status("dingtalk", "room-1", "pending")

        assert ledger.counts("dingtalk") == {"known": 0, "excluded": 0, "decided": 0}, \
            "「没定过」不是一行状态，是一行都不该在"

    def test_an_unknown_status_is_refused_rather_than_stored(self) -> None:
        with pytest.raises(ValueError, match="不认识的准入档位"):
            talkers.Ledger().set_status("dingtalk", "room-1", "maybe")
