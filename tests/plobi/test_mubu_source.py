"""幕布这一条的契约测试：声明、名单走法、正文摊平、本机探针、以及**token 不许出现在读数里**。

全部离线——网络与盘都是注入位（`api` 是一只假对象、`roots` 指一个 tmp 目录）。
真账号的那一趟读数是 2026-10-27 在这台手工跑的（13 篇名单、取回 3 篇正文），
不写进测试里当 fixture：那是他的私人笔记，测试要钉的是**形状**。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plobi.collectors import registry
from plobi.collectors.mubu import localdb
from plobi.collectors.mubu.api import MubuClient, MubuError
from plobi.collectors.mubu.collector import KIND_DOC, MubuCollector, outline

JWT = "eyJhbGciOiJIUzI1NiJ9." + "x" * 120 + ".signature-tail"


def definition(nodes):
    return json.dumps({"customStyle": "{}", "nodes": nodes}, ensure_ascii=False)


def _expected_iso(ms: int) -> str:
    """池里那一格的时间：本地无时区 ISO（与钉钉那侧同一个口径，判据钉的是"按它自己的时间"）。"""
    import datetime as dt

    return dt.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%dT%H:%M:%S")


class FakeApi:
    """四只口的形状：`document_ids` / `folder` / `document` / `profile`。"""

    def __init__(self, *, roots=None, folders=None, docs=None, profile=None, fails=()):
        self.roots = roots or []
        self.folders = folders or {}
        self.docs = docs or {}
        self.profile_payload = profile or {"level": 1}
        self.fails = set(fails)
        self.calls = []

    def document_ids(self, *, limit: int = 500):
        self.calls.append(("roots", limit))
        return self.roots

    def folder(self, folder_id: str):
        self.calls.append(("folder", folder_id))
        if folder_id in self.fails:
            raise MubuError("api_error", "5001")
        return self.folders.get(folder_id, {"documents": [], "folders": []})

    def document(self, doc_id: str):
        self.calls.append(("document", doc_id))
        if doc_id in self.fails:
            raise MubuError("api_error", "5001")
        return self.docs[doc_id]

    def profile(self):
        return self.profile_payload


def doc_row(doc_id, name="机械制图", update=1_780_000_000_000, deleted=0, folder="F1"):
    return {"id": doc_id, "name": name, "updateTime": update, "deleted": deleted, "folderId": folder}


class TestTheDeclaration:
    def test_it_is_registered_on_the_one_axis_as_software(self) -> None:
        assert "mubu" in registry.source_ids()
        assert registry.source_category("mubu") == "software"

    def test_it_asks_the_user_for_nothing(self) -> None:
        """屏幕上这一条没有格子要填：登录态从本机客户端借（用户 10-27「我找不到 token 呀」）。

        注册表会自动补上「采集频率」那一格（每颗旋钮都有），所以判据是**除它以外一格都没有**，
        而不是"元组为空"——填成空是在跟注册表的既定行为较劲。
        """
        spec = registry.spec("mubu")
        assert [field.key for field in spec.setup_fields] == ["cadence"]
        assert spec.capabilities["requires_manual_setup"] is False

    def test_the_capability_flags_say_what_was_measured(self) -> None:
        spec = registry.spec("mubu")
        assert spec.capabilities["history_backfill"] is True
        assert spec.capabilities["incremental_since"] is True
        assert spec.capabilities["authoritative_replace"] is False
        # 附件那一格没实测，留 None——拿 False 冒充"测过了"是这一族不许的形状。
        assert spec.capabilities["supports_media"] is None

    def test_its_cadence_is_one_of_the_grammar(self) -> None:
        assert registry.spec("mubu").cadence == "every 1d"


class TestTheRosterWalk:
    def test_root_returns_only_folders_so_each_one_is_walked(self) -> None:
        api = FakeApi(
            roots=[{"id": "F1", "type": "folder"}, {"id": "F2", "type": "folder"}],
            folders={"F1": {"documents": [doc_row("D1", folder="F1")], "folders": []},
                     "F2": {"documents": [doc_row("D2", folder="F2")], "folders": []}},
            docs={"D1": {"name": "甲", "definition": definition([]), "updateTime": 1},
                  "D2": {"name": "乙", "definition": definition([]), "updateTime": 1}},
        )
        assert sorted(str(o.external_id) for o in MubuCollector(api).pull()) == ["doc:D1", "doc:D2"]

    def test_subfolders_are_walked_too(self) -> None:
        api = FakeApi(
            roots=[{"id": "F1", "type": "folder"}],
            folders={"F1": {"documents": [], "folders": [{"id": "F1a"}]},
                     "F1a": {"documents": [doc_row("D9", folder="F1a")], "folders": []}},
            docs={"D9": {"name": "深处", "definition": definition([]), "updateTime": 1}},
        )
        assert [o.external_id for o in MubuCollector(api).pull()] == ["doc:D9"]

    def test_the_recycle_bin_is_not_a_source(self) -> None:
        api = FakeApi(
            roots=[{"id": "F1", "type": "folder"}],
            folders={"F1": {"documents": [doc_row("D1"), doc_row("D2", deleted=1)], "folders": []}},
            docs={"D1": {"name": "在用", "definition": definition([]), "updateTime": 1}},
        )
        assert [o.external_id for o in MubuCollector(api).pull()] == ["doc:D1"]

    def test_one_folder_that_wont_answer_does_not_kill_the_roster(self) -> None:
        api = FakeApi(
            roots=[{"id": "F1", "type": "folder"}, {"id": "F2", "type": "folder"}],
            folders={"F2": {"documents": [doc_row("D2", folder="F2")], "folders": []}},
            docs={"D2": {"name": "还能读的那一个", "definition": definition([]), "updateTime": 1}},
            fails={"F1"},
        )
        assert [o.external_id for o in MubuCollector(api).pull()] == ["doc:D2"]


class TestTheBodies:
    def test_a_document_lands_as_one_row_with_an_indented_outline(self) -> None:
        api = FakeApi(
            roots=[{"id": "F1", "type": "folder"}],
            folders={"F1": {"documents": [doc_row("D1")], "folders": []}},
            docs={"D1": {
                "name": "车辆工程培养方案",
                "baseVersion": 434,
                "directory": [{"id": "F1", "name": "工程 Engeering"}],
                "updateTime": 1_780_000_000_000,
                "definition": definition([
                    {"id": "a", "text": "<span class=\"bold\">课程内容</span>",
                     "children": [{"id": "b", "text": "理论力学 &nbsp;材料力学", "children": []}]},
                ]),
            }},
        )
        row = MubuCollector(api).pull()[0]
        assert row.kind == KIND_DOC
        assert row.title == "车辆工程培养方案"
        assert row.start_at == _expected_iso(1_780_000_000_000)
        assert row.evidence["node_count"] == 2
        # 行内样式摊平、HTML 实体还原：屏上/池里都不该再有一串 `<span>`。
        assert row.evidence["text"] == "- 课程内容\n  - 理论力学 材料力学"
        assert "<span" not in row.evidence["text"]
        assert row.evidence["path"] == "工程 Engeering"
        assert row.evidence["base_version"] == 434
        assert row.evidence["truncated"] is False

    def test_the_same_document_keeps_the_same_identity(self) -> None:
        api = FakeApi(
            roots=[{"id": "F1", "type": "folder"}],
            folders={"F1": {"documents": [doc_row("D1")], "folders": []}},
            docs={"D1": {"name": "甲", "definition": definition([]), "updateTime": 1}},
        )
        first = MubuCollector(api).pull()[0].external_id
        api.docs["D1"] = {"name": "甲（改过标题）", "definition": definition([]), "updateTime": 2}
        assert MubuCollector(api).pull()[0].external_id == first

    def test_since_only_pulls_what_moved(self) -> None:
        api = FakeApi(
            roots=[{"id": "F1", "type": "folder"}],
            folders={"F1": {"documents": [doc_row("D1", update=1_700_000_000_000),
                                          doc_row("D2", update=1_800_000_000_000)], "folders": []}},
            docs={"D1": {"name": "旧的", "definition": definition([]), "updateTime": 1_700_000_000_000},
                  "D2": {"name": "新的", "definition": definition([]), "updateTime": 1_800_000_000_000}},
        )
        assert [o.external_id for o in MubuCollector(api).pull(since=1_750_000_000_000)] == ["doc:D2"]

    def test_the_first_round_has_a_throttle_not_a_truncation_of_the_roster(self) -> None:
        api = FakeApi(
            roots=[{"id": "F1", "type": "folder"}],
            folders={"F1": {"documents": [doc_row(f"D{i}") for i in range(10)], "folders": []}},
            docs={f"D{i}": {"name": f"第{i}篇", "definition": definition([]), "updateTime": 1}
                  for i in range(10)},
        )
        collector = MubuCollector(api, max_bodies=4)
        assert len(collector.pull()) == 4
        assert "上限 4" in collector.health()["detail"]
        assert "名单：10 篇" in collector.health()["detail"]

    def test_one_document_that_wont_answer_does_not_close_the_switch(self) -> None:
        api = FakeApi(
            roots=[{"id": "F1", "type": "folder"}],
            folders={"F1": {"documents": [doc_row("D1"), doc_row("D2")], "folders": []}},
            docs={"D2": {"name": "读得到的那一篇", "definition": definition([]), "updateTime": 1}},
            fails={"D1"},
        )
        assert [o.external_id for o in MubuCollector(api).pull()] == ["doc:D2"]


class TestTheOutline:
    def test_undecodable_definition_returns_what_it_can(self) -> None:
        assert outline("不是 JSON") == ("不是 JSON", 0)
        assert outline(None) == ("", 0)

    def test_a_deep_tree_is_counted_and_capped(self) -> None:
        body, nodes = outline(definition([{"id": "a", "text": "x" * 50_000}]))
        assert nodes == 1
        assert len(body) <= 40000


class TestTheLocalProbe:
    def _write_users(self, root: Path, payload) -> None:
        collection = root / "mubu_desktop_app_x64-rxdb-1-users"
        collection.mkdir(parents=True)
        (collection / "000005.ldb").write_bytes(json.dumps(payload, ensure_ascii=False).encode())

    def test_no_directory_at_all_is_not_installed(self, tmp_path) -> None:
        assert localdb.probe(roots=[tmp_path / "missing"])["code"] == "client_not_installed"

    def test_installed_but_never_logged_in_says_so(self, tmp_path) -> None:
        self._write_users(tmp_path, {"_id": "u1", "name": "someone"})
        reading = localdb.probe(roots=[tmp_path])
        assert reading["ready"] is False
        assert reading["code"] == "client_not_logged_in"

    def test_a_logged_in_client_is_ready(self, tmp_path) -> None:
        self._write_users(tmp_path, {"_id": "u1", "|u": JWT})
        assert localdb.probe(roots=[tmp_path])["ready"] is True

    def test_the_token_is_the_only_way_in_and_never_comes_back_out(self, tmp_path) -> None:
        """读数里不许有那枚 JWT：`probe` 只报"可借"，不回报文。"""
        self._write_users(tmp_path, {"_id": "u1", "|u": JWT})
        assert JWT not in json.dumps(localdb.probe(roots=[tmp_path]), ensure_ascii=False)
        assert localdb.read_token(roots=[tmp_path]) == JWT


class TestTheRequestShape:
    def test_folder_query_sends_only_the_folder_id(self) -> None:
        """这台实测：多带 `sort`/`keywords`/`source` 里的任何一个，它就回一整套空表。"""
        seen = {}

        def transport(url, payload, headers):
            seen["url"], seen["payload"], seen["headers"] = url, payload, headers
            return {"code": 0, "data": {"documents": [], "folders": []}}

        MubuClient(JWT, transport=transport).folder("F1")
        assert seen["payload"] == {"folderId": "F1"}
        assert seen["url"].startswith("https://api2.mubu.com")
        assert seen["headers"]["jwt-token"] == JWT
        assert seen["headers"]["mubu-desktop"] == "true"

    def test_a_nonzero_code_becomes_one_error_word(self) -> None:
        def transport(url, payload, headers):
            return {"code": 5001, "msg": "system error"}

        with pytest.raises(MubuError) as caught:
            MubuClient(JWT, transport=transport).profile()
        assert caught.value.code == "api_error"

    def test_an_expired_login_says_auth_failed(self) -> None:
        def transport(url, payload, headers):
            return {"code": 4001, "msg": "token invalid"}

        with pytest.raises(MubuError) as caught:
            MubuClient(JWT, transport=transport).profile()
        assert caught.value.code == "auth_failed"

    def test_the_error_never_carries_the_token(self) -> None:
        def transport(url, payload, headers):
            raise MubuError("network_unreachable", "URLError")

        client = MubuClient(JWT, transport=transport)
        with pytest.raises(MubuError) as caught:
            client.profile()
        assert JWT not in str(caught.value)

    def test_health_says_ready_without_reaching_the_roster(self) -> None:
        collector = MubuCollector(FakeApi(profile={"level": 1}))
        reading = collector.health()
        assert reading["ok"] is True and "名单" in reading["detail"]
