"""这一屏的契约（`WP-SOURCE-SURFACE`）：三态由后端算、密钥不回显、图标从声明来、池里几条看得见。

跑法：`bash scripts/run_tests.sh tests/plobi/test_sources_api_surface.py -q`

**全用假件，零网络、零额度**：图标那一只 `opener` 是注进去的（裁定 104 二那一格），
池、台账、`.env` 都钉进 tmp 或假写。这一档真正要钉的是四句"屏上从此说得出/说不出什么话"，
不是某个数值的快照。
"""

from __future__ import annotations

import base64
import io
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plobi.collectors import cadence, icons, registry, sources_api, sources_config

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """家、图标目录、台账都钉进 tmp，并给每条源**预先放一个 miss 标记**。

    不放这一枚，`icon_now` 会为每条源起一个后台线程去**真打外网**（zdbk.zju.edu.cn 那种），
    测试就跑成了网络测试；放了，这一档测的就只是"盘上有就给、没有就回空串并排那一趟"。
    """
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path))
    icons_dir = tmp_path / "icons"
    icons_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PLOBI_SOURCE_ICON_DIR", str(icons_dir))
    registry.ensure_discovered()
    for source_id in registry.source_ids():
        (icons_dir / f"{source_id}.miss").write_text("", encoding="utf-8")


@pytest.fixture()
def client(tmp_path) -> TestClient:
    from plobi.agenda import store as agenda_store

    path = tmp_path / "plobi" / "agenda.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    agenda_store.init_db(agenda_store.connect(path))
    app = FastAPI()
    app.include_router(sources_api.router, prefix="/api/collect")

    return TestClient(app)


class TestThreeStates:
    """用户 10-22：「一个信源就一个开关按钮，就三种形态：未连接和连接，出错了会有一个报错」。"""

    @pytest.mark.parametrize("case, expected", (
        pytest.param(dict(enabled=False, needs_setup=False, last_state="auth_failed",
                          liveness_state="live", has_builder=True),
                     ("disconnected", "off"), id="自己关着的不算出错"),
        pytest.param(dict(enabled=True, needs_setup=True, last_state="auth_failed",
                          liveness_state="never", has_builder=True),
                     ("disconnected", "needs_setup"), id="没配好的先说缺配置"),
        pytest.param(dict(enabled=True, needs_setup=False, last_state="captcha_required",
                          liveness_state="never", has_builder=True),
                     ("error", "captcha_required"), id="要人工验证码是出错"),
        pytest.param(dict(enabled=True, needs_setup=False, last_state="throttled",
                          liveness_state="stale", has_builder=True),
                     ("connected", "throttled"), id="节流拦住的不许画成出错"),
        pytest.param(dict(enabled=True, needs_setup=False, last_state="not_due",
                          liveness_state="live", has_builder=True),
                     ("connected", "live"), id="按频率还没到点仍是已连接"),
        pytest.param(dict(enabled=True, needs_setup=False, last_state="we_never_heard_of_this",
                          liveness_state="never", has_builder=True),
                     ("disconnected", "never_ran"), id="没见过的状态词不许画成绿点"),
        pytest.param(dict(enabled=True, needs_setup=False, last_state="",
                          liveness_state="no_store", has_builder=True),
                     ("error", "internal"), id="读不到库是我们的缺陷，不是用户的信源坏了"),
        pytest.param(dict(enabled=None, needs_setup=False, last_state="ok",
                          liveness_state="live", has_builder=True),
                     ("disconnected", "config_unreadable"), id="读不到开关不等于没点"),
    ))
    def test_the_table(self, case, expected) -> None:
        assert sources_api.status_of(**case) == expected

    def test_only_the_error_state_carries_a_sentence(self, client) -> None:
        """`status_detail` 逐字来自上一轮，且**只在出错态**出现——正常态那一屏不许长出一行工程话。"""
        sources_config.set_enabled("zju", True)
        # 先把必填那两格填上：没配好时「缺配置」该赢过「上一轮失败」（见上面那张表），
        # 不填就测不到出错态这一支。
        sources_config.set_setup_params("zju", {"base_url": "https://courses.zju.edu.cn", "account": "3160100000"})
        cadence.mark_run("zju", state="auth_failed", detail="凭据不对：这一轮换不到会话")

        row = self._zju(client)

        assert row["status"] == "error" and row["status_detail"] == "凭据不对：这一轮换不到会话"

        cadence.mark_run("zju", state="ok", detail="凭据不对：这一轮换不到会话")

        assert self._zju(client)["status_detail"] == "", "不处在出错态却把上一轮的错话挂在屏上"

    @staticmethod
    def _zju(client) -> dict:
        return {r["source"]: r for r in client.get("/api/collect/sources").json()["data"]["sources"]}["zju"]


class TestSecretsAreWriteOnly:
    def test_a_secret_key_must_be_declared_as_one(self) -> None:
        """像密钥的键**必须**声明成 `kind="secret"`；反过来声明成密钥却起个不像的名字也拦。"""
        with pytest.raises(registry.UnknownSetupField):
            registry.SetupField(key="api_token", label="Token", kind="text")
        with pytest.raises(registry.UnknownSetupField):
            registry.SetupField(key="note", label="Note", kind="secret")

        assert registry.SetupField(key="password", label="Password", kind="secret").kind == "secret"

    def test_the_value_goes_to_env_and_never_comes_back_out(self, monkeypatch) -> None:
        """存进 `.env`、`sources.yaml` 里没有它、payload 只回「设过没有」——这才是"星号"那一格的牙齿。"""
        written: dict[str, str] = {}
        monkeypatch.setattr("plobi_cli.config.save_env_value_secure",
                            lambda key, value: written.__setitem__(key, value))
        registry.register(registry.SourceSpec(
            "probe", capabilities={key: False for key in registry.CAPABILITY_KEYS}, category="website",
            builder=lambda: object(),
            setup_fields=(registry.SetupField(key="password", label="Password", kind="secret", required=True),)),
            replace=True)
        monkeypatch.setattr(sources_config, "secret_configured",
                            lambda sid: {"password": bool(written.get("PLOBI_SOURCE_PROBE_PASSWORD"))})

        sources_config.set_setup_params("probe", {"password": "hunter2"})

        assert written == {"PLOBI_SOURCE_PROBE_PASSWORD": "hunter2"}, "值该走 .env 那面墙"
        assert "hunter2" not in json.dumps(sources_config.setup_params("probe")), "不许落进 sources.yaml"
        assert sources_config.secret_configured("probe") == {"password": True}

        sources_config.set_setup_params("probe", {"password": ""})

        assert written["PLOBI_SOURCE_PROBE_PASSWORD"] == "hunter2", \
            "掩码框留空＝不改；把「留空」当「清空」，一次没填就把凭据弄没了"

    def test_a_required_secret_already_set_does_not_ask_again(self, monkeypatch) -> None:
        registry.register(registry.SourceSpec(
            "probe2", capabilities={key: False for key in registry.CAPABILITY_KEYS}, category="website",
            builder=lambda: object(),
            setup_fields=(registry.SetupField(key="password", label="Password", kind="secret", required=True),)),
            replace=True)
        monkeypatch.setattr(sources_config, "secret_configured", lambda sid: {"password": True})

        params = sources_config.setup_params("probe2")
        missing = [f.key for f in registry.spec("probe2").setup_fields
                   if f.required and not str(params.get(f.key) or "").strip()
                   and not sources_config.secret_configured("probe2").get(f.key)]

        assert missing == [], "凭据早就配好了还显示「去设置」，那是一颗按了回到原地的按钮"


class TestIconsComeFromTheDeclaration:
    def test_no_site_means_no_request(self) -> None:
        assert icons.icon_data_url("timetable", "", opener=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("不该被调用"))) == ""

    def test_fetched_once_then_read_from_disk(self) -> None:
        hits: list[str] = []

        class Reply(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def opener(request, timeout=None):
            url = getattr(request, "full_url", str(request))
            hits.append(url)

            return Reply(PNG) if url.endswith(".ico") else Reply(b"<html><head></head></html>")

        (icons.icon_dir() / "zdbk.miss").unlink(missing_ok=True)   # fixture 标过 miss，这一条要的是真去抓
        first = icons.icon_data_url("zdbk", "https://zdbk.zju.edu.cn", opener=opener)
        after_first = len(hits)
        second = icons.icon_data_url("zdbk", "https://zdbk.zju.edu.cn", opener=opener)

        assert first.startswith("data:image/png;base64,") and first == second
        # 第一次最多两跳（首页找 <link rel=icon> → 退 /favicon.ico），**第二次一跳都不许有**：
        # 每次开设置页都去请求一遍外部站点，那是把「看一眼」变成对第三方服务器的轮询。
        assert after_first <= 2, hits
        assert len(hits) == after_first, f"盘上已经有了还去打网络：{hits}"

    def test_a_site_without_an_icon_is_remembered_as_a_miss(self) -> None:
        hits: list[str] = []

        class Reply(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def opener(request, timeout=None):
            hits.append(getattr(request, "full_url", str(request)))

            return Reply(b"<html>not an icon</html>")

        assert icons.icon_data_url("nosite", "https://example.test", opener=opener) == ""

        class Dead(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def loud(request, timeout=None):
            raise AssertionError("miss 记过之后不该再打第二次")

        assert icons.icon_data_url("nosite", "https://example.test", opener=loud) == ""
        assert hits, "第一次至少要真试过"

    def test_a_broken_site_never_breaks_the_page(self) -> None:
        def opener(request, timeout=None):
            raise OSError("连不上")

        assert icons.icon_data_url("broken", "https://down.test", opener=opener) == ""


    def test_the_screen_never_waits_for_an_icon(self, client, monkeypatch) -> None:
        """第一次没有图标就回空串并**后台去抓**：为一张图把那一屏卡 8 秒是另一类故障。"""
        started = []
        monkeypatch.setattr(icons, "icon_data_url",
                            lambda source_id, site, **kw: started.append((source_id, site)) and "")
        # 这一条用的是一个**没被预先标 miss** 的名字（上面那个 fixture 只给注册表里那几条标）
        (icons.icon_dir() / "fresh.miss").unlink(missing_ok=True)

        assert icons.icon_now("fresh", "https://fresh.test") == "", "不许同步等网络"
        for _ in range(40):
            if started:
                break
            __import__("time").sleep(0.05)

        assert started == [("fresh", "https://fresh.test")], "排的那一趟抓的是它自己声明的那个站"
        row = {r["source"]: r for r in client.get("/api/collect/sources").json()["data"]["sources"]}["zdbk"]

        assert row["icon"] == "" and row["site"] == "https://zdbk.zju.edu.cn"


class TestPoolCountsReachTheScreen:
    def test_the_screen_can_say_how_many_rows_landed_in_the_pool(self, client) -> None:
        """旧屏上只有日程库那个 `events` 计数 ⇒ 按裁 193 只进池的教务永远是 0。"""
        from plobi.pool import PoolItem, land

        land([PoolItem(source="zdbk", external_id="class:1", kind="class", title="高数"),
              PoolItem(source="zdbk", external_id="term:1", kind="term", title="2026 秋")])

        row = {r["source"]: r for r in client.get("/api/collect/sources").json()["data"]["sources"]}["zdbk"]

        assert row["pool_count"] == 2
        assert row["pool_kinds"] == {"class": 1, "term": 1}
        assert row["events"] == 0, "两个数是两句话，不许合成一个"
