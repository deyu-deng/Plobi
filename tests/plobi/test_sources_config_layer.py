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
        sources_config.set_setup_params("timetable", {"ics_path": "D:/x.ics"})

        assert sources_config.effective_enabled("timetable") == (registry.spec("timetable").collecting, "spec")


class TestSecretsNeverLandHere:
    """.env 仍是密钥唯一面。这一层有**两道**拦，两道拦的是不同的东西。"""

    def test_the_declaration_layer_refuses_to_declare_one_in_the_first_place(self):
        """第一道，**写声明的时候**：一条信源压根没法声明出一个像密钥的字段。

        这是更强的一道，因为它挡在源头——不是"填进去被拦"，是"没有那个格子"。
        """
        with pytest.raises(registry.UnknownSetupField, match="密钥"):
            registry.SetupField(key="api_token", label="Token")

        with pytest.raises(registry.UnknownSetupField):
            registry.SetupField(key="PASSWORD", label="Password")

    def test_a_secret_like_field_is_only_allowed_declared_as_a_secret(self):
        """拦的从「长得像密钥」改成「**没声明成 `kind='secret'` 却长得像密钥**」（10-22 那一改）。

        邮箱那一族是第一条真用上这一档的信源（`password` 走 `.env`、屏上打星、读数里只有
        "设过没有"），所以原来那句"任何源都不许出现像密钥的字段"当场过期：它挡的一直是
        **没声明**的那一支。声明过那一支由另外两条各自钉着——`set_secret` 只写 `.env`，
        `sources_api._enrich` 只回 `secret_configured` 那本布尔账；下面那条运行时的守卫也原样留着。
        """
        for source_id in registry.source_ids():
            for setup in registry.spec(source_id).setup_fields:
                if registry.is_secret_like(setup.key):
                    assert setup.kind == "secret", f"{source_id}.{setup.key} 长得像密钥却没声明成 secret"
                # 声明成密钥的那一格，配置层那一份文件里**不许有它的值**。
                if setup.kind == "secret":
                    assert setup.key not in (sources_config.entry(source_id) or {})

    def test_the_runtime_guard_still_refuses_a_hand_filled_secret(self, client):
        """第二道，**运行时**：手改过的 yaml 或将来改了声明，那一道还在。

        这不是重复——第一道挡"声明"，第二道挡"文件"。两者之间那道缝写在这里，
        不假装没有。
        """
        response = client.post("/sources/zju/setup", json={"params": {"api_token": "sk-x"}})

        assert response.status_code == 400
        assert "密钥" in response.json()["detail"]

    def test_a_scan_does_not_actually_pass(self):
        """**反着钉**：这一层拦的是名字不是内容。

        所以一个**名字不像**的键仍然放得过去——那道缝存在，这里说清它在哪。
        另一道纪律是「值里那个东西根本不该走这条路」（密钥归 .env），两道之间
        不是靠正则能补上的。
        """
        # 10-23 起 `zju` 那两格已删（采集器根本不读它们，用户当场问了「填了会发生什么」）。
        # 这一条要证的是"**值**里那个东西不被扫"，所以借一条有普通格的源：`timetable.ics_path`。
        sources_config.set_setup_params("timetable", {"ics_path": "a-real-secret-in-a-plain-field"})

        assert sources_config.setup_params("timetable")["ics_path"] == "a-real-secret-in-a-plain-field"

    def test_nested_structures_are_refused(self, client):
        response = client.post("/sources/timetable/setup", json={"params": {"ics_path": {"a": 1}}})

        assert response.status_code in (400, 422)

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


class TestSetupIsValidatedAgainstTheDeclaration:
    """表单是照 ``setup_fields`` 画出来的，所以**存进去的东西与画出来的必然一致**。

    不这么做就会长出一个"界面上没有、文件里有"的键——而那一行从此问不到是谁配的。
    """

    def test_the_declaration_comes_from_the_package_not_from_a_table(self):
        # `cadence` 那一格由注册表统一给（`registry.CADENCE_FIELD`），每个包引一次 ——
        # 所以"声明来自包而不是表"这条仍然成立，而且更成立：连 label 都不在四个包里各写一遍。
        assert sources_config.declared_setup_keys("timetable") == ("ics_path", "cadence")
        # 微信首次配置要的是扫码与密钥，两样都不在这一层 —— 所以那一族**仍然**是空的，
        # 只剩每源都有的频率那一格（裁定 191 二）。它不是"又填了一格配置"，是"这一条终于可以被
        # 用户安排成每天几点采"。第三方包同理：不引 `CADENCE_FIELD` 就只有它自己那几格。
        assert sources_config.declared_setup_keys("wechat") == ("cadence",)
        assert sources_config.declared_setup_keys("dingtalk") == ("cadence",)

    def test_a_key_the_source_never_declared_is_refused(self):
        with pytest.raises(sources_config.SourceConfigError, match="没声明"):
            sources_config.set_setup_params("wechat", {"account": "me"})

    def test_a_missing_required_field_is_refused(self):
        with pytest.raises(sources_config.SourceConfigError, match="还缺"):
            sources_config.set_setup_params("timetable", {})

    def test_a_declared_field_lands(self):
        assert sources_config.set_setup_params("timetable", {"ics_path": "D:/x.ics"}) == {"ics_path": "D:/x.ics"}


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

    def test_a_non_collector_source_does_not_take_a_row(self, client):
        """`manual` 那一档**不进这一屏**——它是存储层的来源档位，不是一条信源。

        10-22 改判。旧形状是"键给全、值全 `None`"，理由是"界面别靠 `key in row` 判存在"；
        现场把它打回来了：屏上角标写「信源 6」而只画得出 5 行，因为 `manual` 没有分类、
        画不出行，却被 `sources.length` 数了进去。**一个数不上号的角标比一个隐式契约贵**，
        所以判据回到最简：这一屏列的就是注册表里有的那些条。
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

        payload = client.get("/sources").json()["data"]
        listed = [r["source"] for r in payload["sources"]]

        assert "manual" not in listed, "库里有的来源档位被当成一条信源列出来了"
        assert sorted(listed) == sorted(registry.source_ids()), "角标数与行数必须同源，不许各数一份"
        # 那一档在库里**仍然存在**，只是不归这一屏说：日程库那一侧的读数没被动过
        assert payload["sources"], "剔掉它不许顺手把整屏剔空"

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