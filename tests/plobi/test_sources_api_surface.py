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
    # 密钥那一档写的是**共享根**那份 `.env`（裁定 89 甲），不是 `PLOBI_HOME` 下面——
    # 所以这里别指望 `set_secret` 落进 tmp：要测写入就照
    # `test_the_value_goes_to_env_and_never_comes_back_out` 那样把 `save_env_value_secure` 换掉
    # （10-26 现核：不换的话值既进不了 tmp、也不该进他机器上那一份）。
    # 钉钉那条有探针，而探针默认去看 `%APPDATA%\DingTalk`（测试运行器只换 HOME，不换 APPDATA）
    # ⇒ 不钉这一格，开一次设置页的测试就会去翻**用户真的钉钉本机库**。
    monkeypatch.setenv("PLOBI_DINGTALK_DATA_DIR", str(tmp_path / "DingTalk"))
    monkeypatch.setenv("PLOBI_DINGTALK_LOCAL_DIR", str(tmp_path / "ding-local"))
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


class TestTheRunNote:
    """「上一轮说明」那一格：绿点盖不住没应答的那一轮（用户 10-27 对着微信那一行问的）。"""

    def test_a_green_pill_with_a_silent_round_still_says_the_round(self):
        assert sources_api.run_note({"state": "not_listening", "detail": "5030 没在应答"},
                                    "connected") == "5030 没在应答"

    def test_a_successful_round_does_not_add_a_line(self):
        assert sources_api.run_note({"state": "ready", "detail": "进了 12 条"}, "connected") == ""

    def test_an_error_round_is_the_old_behaviour_kept(self):
        assert sources_api.run_note({"state": "auth_failed", "detail": "授权码不对"}, "error") == "授权码不对"

    def test_a_round_with_no_sentence_of_its_own_gives_nothing_to_show(self):
        assert sources_api.run_note({"state": "not_listening", "detail": ""}, "disconnected") == ""


class TestThreeStates:
    """用户 10-22：「一个信源就一个开关按钮，就三种形态：未连接和连接，出错了会有一个报错」。"""

    @pytest.mark.parametrize("case, expected", (
        pytest.param(dict(enabled=False, needs_setup=False, last_state="auth_failed",
                          liveness_state="live", has_builder=True, enabled_origin="config"),
                     ("disconnected", "off"), id="自己关着的不算出错"),
        pytest.param(dict(enabled=False, needs_setup=False, last_state="build_failed",
                          liveness_state="never", has_builder=True, enabled_origin="spec"),
                     ("disconnected", "never_enabled"),
                     id="这条默认关着的不许说成他手动停的"),
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

    def test_only_the_error_state_carries_a_sentence(self, client, monkeypatch) -> None:
        """`status_detail` 逐字来自上一轮，且**只在出错态**出现——正常态那一屏不许长出一行工程话。"""
        sources_config.set_enabled("zju", True)
        # 10-26 起 `zju` 的账密在界面上填（裁定 203）⇒ 要先"算填过"才走得到「上一轮失败」这一支。
        # 这一档的主角是三态与那句原话，不是凭据落点，所以按本档既有的打法把这一个前提钉住
        # （`isolated_home` 只钉 `PLOBI_HOME`，密钥墙走的是共享根，往那儿写真值既落不进 tmp 也是脏）。
        monkeypatch.setattr(sources_config, "field_configured", lambda sid, key: True)
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
            "probe", capabilities={key: False for key in registry.CAPABILITY_KEYS}, category="site",
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
            "probe2", capabilities={key: False for key in registry.CAPABILITY_KEYS}, category="site",
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

    def test_a_process_we_cannot_wait_on_is_reported_not_raised(self, monkeypatch) -> None:
        """`terminate()` 收令了，但**等它退出**还要再开一次进程句柄——这台真撞上过 `AccessDenied`。

        旧形状里那一句没接住，于是**用户点一下开关拿到一个 500**：比"收不掉"更坏。
        现在它是"这句我确认不了"（回 False），开关那一条读数继续走它自己的 `listening` 判据。
        """
        import sys
        import types

        class Denied(Exception):
            pass

        class Waiter:
            def terminate(self):
                return None

            def wait(self, timeout=None):
                raise Denied("OpenProcess 被拒")

        fake = types.ModuleType("psutil")
        fake.AccessDenied = Denied
        fake.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
        fake.TimeoutExpired = type("TimeoutExpired", (Exception,), {})
        fake.Process = lambda pid: Waiter()
        monkeypatch.setitem(sys.modules, "psutil", fake)

        assert sidecar._terminate(4242) is False

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

    def test_a_source_bigger_than_the_read_cap_still_reports_its_real_number(
            self, client, monkeypatch) -> None:
        """「池里几条」是**计数**，不是数取回来的行——取行那只手有上限，超了就谎报。

        现场（10-31）：微信池里 1937 条，这一屏写 500 条，因为旧写法把整页取回来再数，
        而 `query()` 的 `MAX_LIMIT` 把页钳在 500。同一颗牙还咬得住另一半：那一族
        `pool_kinds` 是给屏上标签用的名单，按取行做窗口就会漏掉稀有那一类。
        """
        from plobi.pool import PoolItem, land
        from plobi.pool import store as pool_store

        monkeypatch.setattr(pool_store, "MAX_LIMIT", 1)
        land([PoolItem(source="wechat", external_id=f"m{n}", kind="message", title=f"第 {n} 条")
              for n in range(3)])

        row = {r["source"]: r for r in client.get("/api/collect/sources").json()["data"]["sources"]}["wechat"]

        assert row["pool_count"] == 3
        assert row["pool_kinds"] == {"message": 3}


class TestTheDeclaredIntro:
    def test_the_source_declares_its_own_one_line_intro(self, client) -> None:
        """配置卡第三格（用户 11-07 拍「加 summary 声明」）：声明给缺省值，第三方包没填就是空串。

        空串是一个**读数**（这一行不画），不是缺字段——所以键恒在、值可空，两半都要钉住。
        """
        row = {r["source"]: r for r in client.get("/api/collect/sources").json()["data"]["sources"]}

        assert row["wechat"]["summary"] == "本机微信的会话与群聊"
        assert "summary" in row["timetable"]


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
            category="site", site="https://example.test", collecting=False)
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
                                   category="software", probe=broken)
        registry.register(spec, replace=True)

        try:
            result = sources_api._probe_of(registry.spec("probetest"))
        finally:
            registry.unregister("probetest")

        assert result["ready"] is False
        assert result["code"] == "probe_failed"


class TestTheUnconfiguredShape:
    """「没配过」那一格（11-08）。

    用户 11-08 原话：「用户明明什么都没有配置。钉钉、幕布和微信左边就从加号变成开关了。
    甚至微信还一直在报错。」根因：**「配好了没有」这一格对那三条根本不存在**——它们的
    `setup_fields` 里只有不 required 的 `cadence`，于是 `setup_complete` 算出来是
    `all([])`＝**恒真**：屏上画成开关，采集侧那道闸（11-05 加的）也形同虚设。

    这一组钉的是新口径的三个落点：**判据在后端且下发到屏上**、**那一步随声明下来**、
    **产品能代做的那一步有个动作**。
    """

    def test_the_verdict_travels_to_the_screen_from_one_place(self, client, monkeypatch) -> None:
        """判据住在后端并**下发**——前端从前自己算一遍（只看 required 的那几格），
        于是那三条被恒判成「配好了」，加号就变成了开关。"""
        monkeypatch.delenv("CHATLOG_DATA_KEY", raising=False)
        rows = {r["source"]: r for r in client.get("/api/collect/sources").json()["data"]["sources"]}

        assert rows["wechat"]["needs_setup"] is True, "没配好就要说没配好"

    def test_a_source_with_nothing_to_configure_is_not_blocked(self, client) -> None:
        """没声明 `setup_ready` 的源按必填格判（邮箱那一族：配齐了就不是「没配过」）。"""
        sources_config.set_secret("email_qq", "password", "an-app-token")
        sources_config.set_setup_params("email_qq", {"address": "me@example.test"})

        rows = {r["source"]: r for r in client.get("/api/collect/sources").json()["data"]["sources"]}

        assert rows["email_qq"]["needs_setup"] is False

    def test_the_step_the_user_must_do_travels_to_the_screen(self, client) -> None:
        """「装软件／登录」那一步随声明下来（`SourceSpec.setup_guide`）——
        用户 11-08：「如果用户点击配置了就引导用户登录或者下载软件」。"""
        rows = {r["source"]: r for r in client.get("/api/collect/sources").json()["data"]["sources"]}

        for sid in ("dingtalk", "mubu", "wechat"):
            guide = rows[sid]["setup_guide"]

            assert guide is not None, f"{sid} 没有可填的格子，它那一步就靠这一格说"
            assert guide["title"]
            assert guide["action"]
            assert guide["url"].startswith("http")

    def test_prepare_runs_the_step_the_product_can_do_itself(self, client) -> None:
        """微信那枚数据密钥是产品自己取的（裁定 203 口径 2）：这一格把它跑掉。"""
        spec = registry.spec("wechat")
        registry._SPECS["wechat"] = replace(spec, sidecar_prepare=lambda: (True, "密钥到手了"))

        try:
            body = client.post("/api/collect/sources/wechat/prepare").json()["data"]
        finally:
            registry._SPECS["wechat"] = spec

        assert body["ok"] is True
        assert body["detail"] == "密钥到手了"

    def test_a_source_with_nothing_to_prepare_says_so(self, client) -> None:
        """没有产品能代做的那一步时，如实说「得他自己做」，不编一句。"""
        body = client.post("/api/collect/sources/mubu/prepare").json()["data"]

        assert body["ok"] is False
        assert body["detail"], "得说清楚这一步得他自己在客户端里做"
