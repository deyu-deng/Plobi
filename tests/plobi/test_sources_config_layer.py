"""信源的用户那一层：`sources.yaml` 落点 · 回落链 · 拒收密钥 · 面上三个动词的接线。

跑法：``bash scripts/run_tests.sh tests/plobi/test_sources_config_layer.py -q -j 6``
（唯一入口、不传 ``--basetemp``，裁定 87）。

判据钉的是**形状与关系**，不是当前值：哪条信源归哪一类是数据，变了就该改数据，
不该让测试红。所以这里钉的是「落点在共享层」「没写就回落代码」「像密钥的键拒收」
「面上那一格只有一个读入口」「动词返回的是执行方自己那句话」。
"""

from __future__ import annotations

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plobi.collectors import registry, sources_config
from plobi.collectors.sources_api import router


@pytest.fixture(autouse=True)
def shared_home(tmp_path, monkeypatch):
    """把共享根钉到临时家——这一份被二十四个分身共享，测试不许碰真的那份。"""
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path))
    monkeypatch.setattr(sources_config, "sources_path", lambda: tmp_path / "plobi" / "sources.yaml")
    return tmp_path


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestWhereTheFileLands:
    def test_it_lives_in_the_shared_layer_not_a_profile(self, shared_home, monkeypatch):
        """共享层那一格：分身家各存一份开关就是新的漂移源（裁定 42）。

        这里钉的是**解析走哪条路**（`get_shared_plobi_root`），不是钉盘符——盘符
        这台机器上会变，钉它就是一条 change-detector。

        要先摘掉 autouse 那个把 `sources_path` 整个换掉的补丁，否则这一条测的
        是我刚才塞进去的那个假函数自己，不是解析器。
        """
        calls = []

        def fake_shared_root():
            calls.append(1)

            return shared_home

        monkeypatch.undo()
        monkeypatch.setattr("plobi_constants.get_shared_plobi_root", fake_shared_root)

        assert sources_config.sources_path() == shared_home / "plobi" / "sources.yaml"
        assert calls, "没走共享根解析"

    def test_no_file_is_an_empty_config_not_an_error(self, client):
        """一项都没按过 = 空配置（于是每条都回落到代码默认），不是"读不到"。"""
        assert sources_config.load() == {}

    def test_a_missing_file_still_serves_the_read_endpoint(self, client):
        body = client.get("/sources").json()
        assert body["ok"] is True
        assert body["data"]["sources"]


class TestTheFallbackChain:
    """`enabled` 的优先级**不是一个裁定，是一个回落链**——所以下面每一条都钉关系。"""

    def test_unset_falls_back_to_the_spec(self):
        for source_id in registry.source_ids():
            enabled, origin = sources_config.effective_enabled(source_id)
            assert origin == "spec"
            assert enabled is registry.spec(source_id).collecting

    def test_a_written_value_wins_and_says_it_did(self):
        spec_default = registry.spec("wechat").collecting

        sources_config.set_enabled("wechat", not spec_default)

        enabled, origin = sources_config.effective_enabled("wechat")
        assert enabled is not spec_default
        assert origin == "config", "只给布尔而不说它从哪来 ⇒「我明明关了怎么还在跑」永远答不上"

    def test_deleting_the_line_returns_to_the_code_default(self):
        """想回到默认 = 删掉那一行，不用改代码。这是回落链的另一头。"""
        spec_default = registry.spec("wechat").collecting

        sources_config.set_enabled("wechat", not spec_default)
        entries = sources_config.load()
        del entries["wechat"]["enabled"]
        sources_config.save(entries)

        assert sources_config.effective_enabled("wechat") == (spec_default, "spec")

    def test_one_row_never_touches_another(self):
        sources_config.set_enabled("wechat", False)
        sources_config.set_setup_params("timetable", {"ics_path": "D:/x.ics"})

        assert sources_config.setup_params("wechat") == {}
        assert sources_config.effective_enabled("timetable") == (registry.spec("timetable").collecting, "spec")

    def test_setup_params_leave_the_switch_alone(self):
        """首次配置那一格有它自己的动词，不许顺手把开关也翻了。"""
        sources_config.set_setup_params("wechat", {"account": "me"})

        assert sources_config.effective_enabled("wechat") == (registry.spec("wechat").collecting, "spec")


class TestSecretsNeverLandHere:
    """.env 仍是密钥唯一面——所以这里是**按键名**拦，不是靠正则扫值。"""

    @pytest.mark.parametrize("key", ["password", "api_token", "API-KEY", "cookie", "credential", "secret"])
    def test_a_secret_like_key_is_refused(self, key):
        with pytest.raises(sources_config.SourceConfigError, match="密钥"):
            sources_config.set_setup_params("zju", {key: "whatever"})

    def test_a_scan_does_not_actually_pass(self):
        """拦的是名字不是内容——所以一个**长得不像**的键仍然放得过去。

        这条是**反着钉**的：它说清这一层的防护边界在哪。不是"扫得严"，是"键名
        那一道 + 不落 `.env` 那条纪律"，两道之间那条缝不假装没有。
        """
        sources_config.set_setup_params("zju", {"nickname": "a-real-secret-in-a-plain-field"})

        assert sources_config.setup_params("zju") == {"nickname": "a-real-secret-in-a-plain-field"}

    def test_nested_structures_are_refused(self):
        with pytest.raises(sources_config.SourceConfigError, match="标量"):
            sources_config.set_setup_params("zju", {"a": {"b": 1}})

    def test_a_non_bool_enabled_is_refused(self):
        with pytest.raises(sources_config.SourceConfigError, match="true/false"):
            sources_config.set_enabled("wechat", "yes")

    def test_a_hand_edited_file_is_validated_on_read_too(self):
        """手改过的 yaml 是**不可信输入**：读的时候也要拦，不是只在写的时候。"""
        path = sources_config.sources_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump({"sources": {"zju": {"api_token": "sk-oops"}}}), encoding="utf-8")

        with pytest.raises(sources_config.SourceConfigError, match="密钥"):
            sources_config.load()


class TestTheWriteIsAtomic:
    def test_no_temp_file_is_left_behind(self):
        sources_config.set_enabled("wechat", False)

        leftovers = [p.name for p in sources_config.sources_path().parent.glob(".sources.yaml.*")]

        assert leftovers == []

    def test_a_failed_write_leaves_the_previous_file_intact(self):
        """同目录临时文件 + 原子换名：崩在写一半 = 二十四个分身同时丢开关。"""
        sources_config.set_enabled("wechat", False)
        before = sources_config.sources_path().read_text(encoding="utf-8")

        original = sources_config.save

        def boom(entries):
            raise OSError("disk full")

        sources_config.save = boom
        try:
            with pytest.raises(OSError):
                sources_config.set_setup_params("timetable", {"ics_path": "D:/x.ics"})
        finally:
            sources_config.save = original

        assert sources_config.sources_path().read_text(encoding="utf-8") == before


class TestOneReadEntry:
    """面上只该有**一个**地方读到"某条信源现在什么状态"。两个入口 = 两份可能不一致的读数。"""

    def test_the_read_entry_carries_registry_and_config_too(self, client):
        row = {r["source"]: r for r in client.get("/sources").json()["data"]["sources"]}

        for source_id in registry.source_ids():
            spec = registry.spec(source_id)
            assert row[source_id]["category"] == spec.category
            assert row[source_id]["enabled"] == spec.collecting
            assert row[source_id]["has_builder"] is (spec.builder is not None)
            assert row[source_id]["enabled_origin"] in {"config", "spec"}

    def test_the_shape_the_old_consumer_reads_is_unchanged(self, client):
        """既有消费方（日程板上那一横排）读的那几格一个没少、没改名。"""
        row = {r["source"]: r for r in client.get("/sources").json()["data"]["sources"]}

        for field in ("source", "state", "events", "last_created_at", "age_days",
                      "touched_after_last_write", "collecting", "note"):
            assert field in row["zju"], f"既有消费方读的那一格 {field} 没了"

    def test_a_non_collector_source_is_left_alone(self, client):
        """`manual` 那一档允许按名字分支（存储层自己的词表），别把它硬塞进分类。

        它**只在库里真有行时**才出现在读数里（不是注册表派生的那一档）——所以这里
        先种一行进去，钉的是"它在、但没有分类也没有开关"这一条关系。
        """
        from plobi.agenda import store

        connection = store.connect(sources_config.sources_path().parent / "agenda.db")
        store.init_db(connection)
        connection.execute(
            "INSERT INTO events (id, title, start_at, source, created_at, updated_at)"
            " VALUES ('m1', '手写的一条', '2026-10-19 08:00:00', 'manual',"
            " '2026-10-19 08:00:00', '2026-10-19 08:00:00')"
        )
        connection.commit()
        connection.close()

        row = {r["source"]: r for r in client.get("/sources").json()["data"]["sources"]}

        assert "manual" in row
        assert row["manual"]["category"] is None
        assert row["manual"]["enabled"] is None

    def test_every_category_is_listed_even_when_empty(self, client):
        listed = {c["id"] for c in client.get("/sources").json()["data"]["categories"]}

        assert listed == set(registry.SOURCE_CATEGORIES)

    def test_an_unreadable_config_layer_says_so_instead_of_falling_back_silently(self, client, monkeypatch):
        """开关读不到就说读不到——静默回落成代码默认正是"查不出来的那种故障"。"""
        def boom(source_id):
            raise sources_config.SourceConfigError("共享根读不到")

        monkeypatch.setattr(sources_config, "effective_enabled", boom)

        row = {r["source"]: r for r in client.get("/sources").json()["data"]["sources"]}

        assert row["wechat"]["enabled"] is None, "读不到开关时必须回 none，不是回代码默认"


class TestTheVerbs:
    def test_an_unregistered_name_is_404_not_400(self, client):
        """"这个名字没在注册表里"与"你参数填错了"排障方向完全不同，状态码也不同。"""
        assert client.post("/sources/manual/enabled", json={"enabled": True}).status_code == 404
        assert client.post("/sources/nosuch/enabled", json={"enabled": True}).status_code == 404

    def test_toggling_writes_and_the_read_entry_sees_it(self, client):
        assert client.post("/sources/wechat/enabled", json={"enabled": False}).json()["data"]["enabled"] is False

        row = {r["source"]: r for r in client.get("/sources").json()["data"]["sources"]}

        assert row["wechat"]["enabled"] is False
        assert row["wechat"]["enabled_origin"] == "config"

    def test_try_returns_the_executors_own_words(self, client):
        """P6 那一格：面上那行字要么逐字来自 `sweep.py`，要么逐字来自采集器自己。

        钉的是**逐字**——不是"有一句 detail"，而是"那句 detail 就是执行方自己写的
        那一句"。这一层不许转述、不许总结、不许代写。
        """
        from plobi.collectors.sweep import run_sweep

        response = client.post("/sources/dingtalk/try")
        row = response.json()["data"]

        direct = run_sweep(sources=("dingtalk",))["sources"][0]

        assert row["state"] == direct["state"]
        assert row["detail"] == direct["detail"]

    def test_try_on_a_source_with_no_builder_says_that_rather_than_inventing_a_reason(self, client):
        row = client.post("/sources/dingtalk/try").json()["data"]

        assert row["state"] == "no_builder"
        # 那句话要说"没东西构造得出来"，不许退化成"没数据"——那正是 `no_builder`
        # 当年被藏起来的原因（真原因问不到下一层）。
        assert "构造" in row["detail"]

    def test_setup_refuses_a_secret_key_with_400(self, client):
        response = client.post("/sources/zju/setup", json={"params": {"api_token": "sk-x"}})

        assert response.status_code == 400
        assert "密钥" in response.json()["detail"]


class TestNoSourceNameBranchInTheCommonLayer:
    """公共层不许按信源名字分支（判据 4）。这一层是**公共层**。"""

    def test_the_api_module_names_no_source_at_all(self):
        source = (registry.__file__.replace("registry.py", "sources_api.py"))
        text = open(source, encoding="utf-8").read()

        for source_id in ("wechat", "zju", "timetable", "dingtalk"):
            assert source_id not in text, f"sources_api.py 里写着 {source_id}"