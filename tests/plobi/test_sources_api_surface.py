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
from dataclasses import replace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plobi.collectors import cadence, icons, registry, sidecar, sources_api, sources_config

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """家、图标目录、台账都钉进 tmp，并给每条源**预先放一个 miss 标记**（图标一枚、站名一枚）。

    不放这两枚，`icon_now` / `site_name_now` 会为每条源起一个后台线程去**真打外网**（zdbk.zju.edu.cn
    那种），测试就跑成了网络测试；放了，这一档测的就只是"盘上有就给、没有就回空串并排那一趟"。
    """
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path))
    icons_dir = tmp_path / "icons"
    icons_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PLOBI_SOURCE_ICON_DIR", str(icons_dir))
    registry.ensure_discovered()
    for source_id in registry.source_ids():
        (icons_dir / f"{source_id}.miss").write_text("", encoding="utf-8")
        (icons_dir / f"{source_id}.name.miss").write_text("", encoding="utf-8")


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
                     ("disconnected", "unknown_state"), id="没见过的状态词不许画成绿点也不许说成没跑过"),
        pytest.param(dict(enabled=True, needs_setup=False, last_state="",
                          liveness_state="never", has_builder=True),
                     ("disconnected", "never_ran"), id="台账空的才说没跑过"),
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
        # 10-23 起 `zju` 没有首次配置格了（那两格采集器根本不读，被删）⇒ 点开就是配好的，
        # 于是「上一轮失败」这一支直接可达，不必先填格。
        cadence.mark_run("zju", state="auth_failed", detail="凭据不对：这一轮换不到会话")

        row = self._zju(client)

        assert row["status"] == "error" and row["status_detail"] == "凭据不对：这一轮换不到会话"

        cadence.mark_run("zju", state="ok", detail="凭据不对：这一轮换不到会话")

        assert self._zju(client)["status_detail"] == "", "不处在出错态却把上一轮的错话挂在屏上"

    @staticmethod
    def _zju(client) -> dict:
        return {r["source"]: r for r in client.get("/api/collect/sources").json()["data"]["sources"]}["zju"]

    def test_a_hand_run_records_the_reason_it_just_produced(self, client, monkeypatch) -> None:
        """「立即同步」跑完，那句原话要**跟着记进台账**。

        台账那一格每轮是整行重写的：只给状态词就等于这个动词把自己刚拿到的答案删掉，
        屏上从此只剩一句「连接异常」——而它存在的理由正是回答"现在到底行不行"。
        """
        seen: dict[str, str] = {}

        def mark(source_id, *, state="", detail=""):
            seen.update({"detail": detail, "source": source_id, "state": state})

        def fake_sweep(**kwargs):
            return {"sources": [{"detail": "要人工验证码：这一轮按口径停手", "ok": False,
                                 "source": "zju", "state": "captcha_required"}]}

        monkeypatch.setattr(cadence, "mark_run", mark)
        monkeypatch.setattr("plobi.collectors.sweep.run_sweep", fake_sweep)

        assert client.post("/api/collect/sources/zju/try").status_code == 200
        assert seen == {"detail": "要人工验证码：这一轮按口径停手", "source": "zju", "state": "captcha_required"}


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


class TestTheSwitchCutsTheProcess:
    """用户 10-23：「**微信那个按钮形同虚设……我希望开始和关闭是会真的切断这条信源的采集程序**」。

    钉的是"那颗开关背后确实动了一个进程"，而且**只动声明了端口的那一条**、
    只动听在那个端口上的进程。全部用假件：不真杀、不真拉、不占固定端口。
    """

    def test_apply_follows_the_declaration_not_the_name(self) -> None:
        from types import SimpleNamespace

        probe = SimpleNamespace(sidecar_port=5099, sidecar_start="scripts/nope.ps1")

        # 没声明端口的（微信以外那三条都这样）⇒ 这个模块一个字都不做。
        assert sidecar.apply(SimpleNamespace(sidecar_port=None, sidecar_start=""), enabled=True) is None

        killed: list[int] = []
        off = sidecar.apply(probe, enabled=False, listening=lambda _port: True,
                            finder=lambda _port: [4242], killer=killed.append, spawn=lambda _s: None)
        assert killed == [4242], "关掉必须真的收到那个端口上的进程，而不是只写一个布尔"
        assert off["port"] == 5099

        started: list[str] = []
        on = sidecar.apply(probe, enabled=True, listening=lambda _port: False,
                           finder=lambda _port: [], killer=lambda _pid: False,
                           spawn=started.append, wait_seconds=0.0)
        assert started == ["scripts/nope.ps1"], "打开要按**声明的那条命令**去拉，不按信源名字"
        assert on["action"] == "started_not_listening", "拉起了但端口没应答，就说没应答（不许报成开了）"

        # 已经在跑的不许再拉一遍：一次点击不该起两个服务。
        calls: list[str] = []
        again = sidecar.apply(probe, enabled=True, listening=lambda _port: True,
                              finder=lambda _port: [], killer=lambda _pid: False,
                              spawn=calls.append)
        assert calls == [] and again["action"] == "already_running"

    def test_port_listening_is_a_real_socket_read(self) -> None:
        """这一格不许是"我以为它在"：真开一个 listen 的套接字读一次，关掉再读一次。"""
        import socket

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        try:
            assert sidecar.port_listening(port) is True
        finally:
            listener.close()

        assert sidecar.port_listening(port) is False


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


class TestTheSourceNamesItself:
    """名字归一处（用户 10-24：「网站就显示网站名就行了，不要叫什么"教务"」）。"""

    def test_a_declared_name_beats_the_fetched_one(self, client) -> None:
        """声明是**这条信源自己说的**，站点自报的名只填它没说的那格。"""
        (icons.icon_dir() / "zju.name").write_text("抓来的那个名", encoding="utf-8")

        row = {r["source"]: r for r in client.get("/api/collect/sources").json()["data"]["sources"]}["zju"]

        assert row["display_name"] == "学在浙大"

    def test_a_website_source_without_a_declared_name_gets_the_site_s_own(self, tmp_path) -> None:
        """第三方包**零改动**也有名字：它声明了网站，站自己报了名，屏上就不用落「没记到名字的信源」。"""
        spec = registry.SourceSpec(
            "webtest", capabilities=dict.fromkeys(registry.CAPABILITY_KEYS, False),
            category="website", site="https://example.test", collecting=False)
        registry.register(spec, replace=True)
        (icons.icon_dir() / "webtest.name").write_text("Example 门户", encoding="utf-8")

        try:
            enriched = sources_api._enrich(
                {"source": "webtest", "state": "never", "events": 0, "note": ""},
                {"webtest": True})
        finally:
            registry.unregister("webtest")

        assert enriched["display_name"] == "Example 门户"

    def test_the_fetched_name_is_never_used_for_a_non_website_source(self, client) -> None:
        """`www.dingtalk.com` 的首页 `<title>` 现读是「钉钉，AI时代工作方式」——那是一句营销话。

        消息那一族的名字归界面语言目录（四语各有写法），后端因此**不许**替它报名。
        """
        (icons.icon_dir() / "dingtalk.name").write_text("钉钉，AI时代工作方式", encoding="utf-8")

        row = {r["source"]: r for r in client.get("/api/collect/sources").json()["data"]["sources"]}["dingtalk"]

        assert row["display_name"] == ""


class TestTheProbeReplacesAFalseFault:
    """`error/internal` 那一颗红点，在"这台机器给不出前提"那种现场是**假故障**。"""

    @pytest.mark.parametrize("probe, expected", (
        pytest.param(None, ("error", "internal"), id="没有探针_照旧是我们的缺陷"),
        pytest.param({"ready": True, "code": "local_store_found", "detail": "在位"},
                     ("error", "internal"), id="探针说明白了_红点不许把自己洗绿"),
        pytest.param({"ready": False, "code": "client_not_logged_in", "detail": "没登录过"},
                     ("disconnected", "client_not_logged_in"), id="前提不在_那是未连接"),
    ))
    def test_probe_only_speaks_when_it_says_not_ready(self, probe, expected) -> None:
        assert sources_api.status_of(enabled=True, needs_setup=False, last_state="",
                                     liveness_state="never", has_builder=False,
                                     probe=probe) == expected

    def test_a_source_without_a_probe_carries_no_such_row(self, client) -> None:
        """这一格不是"给每条源都补一个说法"——没声明就一个字都不多。"""
        row = {r["source"]: r for r in client.get("/api/collect/sources").json()["data"]["sources"]}["zju"]

        assert row["probe"] is None

    def test_the_row_carries_what_the_machine_answers(self, client, monkeypatch) -> None:
        """钉钉那一行现在说的是**这台机器的真话**，不是"我们的产品坏了"。"""
        spec = registry.spec("dingtalk")
        registry._SPECS["dingtalk"] = replace(
            spec, probe=lambda: {"ready": False, "code": "client_not_logged_in",
                                 "detail": "客户端装了，但这台机器上它从没登录过"})
        sources_config.set_enabled("dingtalk", True)

        try:
            row = {r["source"]: r for r in client.get("/api/collect/sources").json()["data"]["sources"]}["dingtalk"]
        finally:
            registry._SPECS["dingtalk"] = spec

        assert (row["status"], row["status_code"]) == ("disconnected", "client_not_logged_in")
        assert row["probe"]["ready"] is False
        assert "登录" in row["probe"]["detail"]
        # 行上那句人话仍然来自语言目录（`status_code` 查表），后端那句只进抽屉（裁定 45.4）。
        assert row["status_detail"] == "", "非出错态那一格恒为空，这条不变"

    def test_a_probe_that_raises_is_read_as_unreadable_not_as_a_green_dot(self) -> None:
        def broken() -> dict:
            raise RuntimeError("盘都读不到")

        spec = registry.SourceSpec("probetest", capabilities=dict.fromkeys(registry.CAPABILITY_KEYS, False),
                                   category="system", probe=broken)
        registry.register(spec, replace=True)

        try:
            result = sources_api._probe_of(registry.spec("probetest"))
        finally:
            registry.unregister("probetest")

        assert result["ready"] is False
        assert result["code"] == "probe_failed"
