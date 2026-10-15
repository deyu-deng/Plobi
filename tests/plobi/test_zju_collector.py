"""判据 5「学在浙大真出数据」的钉：登录链形状、三格失败话、幂等落库、凭据不回显。

跑法：``bash scripts/run_tests.sh tests/plobi/test_zju_collector.py -q``
（唯一入口、不传 ``--basetemp``，裁定 87）。

**一档都不打真网络、一档都不叫真模型**：``transport`` 是构造必填位，测试一律塞假的（裁定 104 二
——"默认可回退到真外部调用"那种构造就是判据 7 三次三种结果的根因）。真账号那一次端到端是
交账里的另一件事，不在测试里，因为它不可重复、还会给站方送失败计数。

金标准那两条密文不是我从记忆里抄的：拿 node 跑**站方自己的** ``security.js`` 当参照算出来的
（假模数、假口令，与学校无关），用来钉住两处会静默坏掉的形状——每块补满 4 个十六进制字符、
以及分块以空格连接。少了前一半就是"密码明明对却报认证失败"，且每次约 1/16 概率撞。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from plobi.agenda.service import AgendaService
from plobi.collectors import registry
from plobi.collectors.zju import auth
from plobi.collectors.zju.collector import ZjuCollector, to_local_naive, to_occurrences
from plobi.collectors.zju.config import ZjuConfig, read_state
from plobi.collectors.zju.status import (
    FAILURE_STATES,
    SOURCE_STATES,
    STATE_AUTH_FAILED,
    STATE_CAPTCHA_REQUIRED,
    STATE_DISABLED,
    STATE_LOGIN_CHANGED,
    STATE_NETWORK_UNREACHABLE,
    STATE_NO_CREDENTIAL,
    STATE_READY,
    STATE_THROTTLED,
    ZjuUnavailable,
    note_for,
)

# 假模数与假口令：与学校、与用户都没有一个字的关系（金标准由 node 跑站方 security.js 得出）。
GOLDEN_MODULUS = (
    "f0123456789abcdef9741ac2d5b3f0e68c1d7e2b4903a5f66aa55cc33ee7711990b1e2d3c4b5a6978"
    "1234567890abcdeffedcba09876543210123456789abcde"
)
GOLDEN_EXPONENT = "10001"

FAKE_USER = "99901234567"
FAKE_PASSWORD = "fake-passwd-42"

LOGIN_HTML = (
    '<html><body><form id="fm1" action="/cas/login?service=https%3A%2F%2Fcourses.zju.edu.cn%2Flogin">'
    '<input type="hidden" name="execution" value="1AB2CD34-exec-token" />'
    '<input type="hidden" id="uuid" value="2706125183131686506" />'
    "</form></body></html>"
)
BAD_PASSWORD_HTML = '<html><body><div class="errors">用户名或密码错误</div></body></html>'
APP_HOME_HTML = "<html><body>学在浙大</body></html>"

TODOS_JSON = json.dumps(
    {
        "todo_list": [
            {
                "id": 1182287,
                "title": "实验报告一",
                "course_id": 99659,
                "course_code": "cs1001",
                "course_name": "大学物理",
                "course_type": 1,
                "end_time": "2027-01-05T15:59:00Z",
                "is_locked": False,
                "is_student": True,
                "prerequisites": [],
                "type": "homework",
            },
            {
                "id": 1182288,
                "title": "被前置锁住的作业",
                "course_id": 99660,
                "course_code": "cs1002",
                "course_name": "线性代数",
                "course_type": 1,
                "end_time": "2026-10-18T15:55:00Z",
                "is_locked": True,
                "is_student": True,
                "prerequisites": [{"activity_id": 1, "title": "看视频"}],
                "type": "homework",
            },
        ]
    },
    ensure_ascii=False,
)


class FakeTransport:
    """按 URL 派发的假传输，并记下每一次调用（含 method / 表单体）。"""

    def __init__(self, *, routes=None, fail_on=None):
        self.routes = routes or {}
        self.fail_on = tuple(fail_on or ())
        self.calls: list[tuple[str, str]] = []
        self.posted_bodies: list[dict] = []

    def _respond(self, url: str):
        if url in self.fail_on:
            raise auth.TransportError(f"假断线：{url}")
        for key, value in self.routes.items():
            if url.startswith(key):
                if callable(value):
                    value = value(url)
                return auth.HttpResponse(status=value[0], url=value[1], text=value[2])
        return auth.HttpResponse(status=404, url=url, text="{}")

    def get(self, url: str) -> auth.HttpResponse:
        self.calls.append(("GET", url))
        return self._respond(url)

    def post_form(self, url, data, *, referer=None) -> auth.HttpResponse:
        self.calls.append(("POST", url))
        self.posted_bodies.append(dict(data))
        return self._respond(url)

    # 便利读数
    def posted(self) -> bool:
        return any(method == "POST" for method, _ in self.calls)

    def asked_api_user(self) -> bool:
        return any("api/user" in url for _, url in self.calls)


def ok_routes(kaptcha="false"):
    return {
        auth.LOGIN_ENTRY_URL: (200, auth.LOGIN_ENTRY_URL, LOGIN_HTML),
        auth.KAPTCHA_URL: (200, auth.KAPTCHA_URL, kaptcha),
        auth.PUBKEY_URL: (
            200,
            auth.PUBKEY_URL,
            json.dumps({"modulus": GOLDEN_MODULUS, "exponent": GOLDEN_EXPONENT}),
        ),
        auth.CAS_ORIGIN + "/cas/login": (200, auth.COURSES_ORIGIN + "/login", APP_HOME_HTML),
        auth.TODOS_URL: (200, auth.TODOS_URL + "?ticket=ST-1234-abcdef", TODOS_JSON),
    }


@pytest.fixture
def zju_paths(tmp_path, monkeypatch):
    """把配置与状态都钉进 tmp_path —— 不碰真家目录（跑测试会删真注册表那条教训同族）。"""
    config_file = tmp_path / "zju.json"
    monkeypatch.setenv("PLOBI_ZJU_CONFIG", str(config_file))
    return config_file


def make_collector(*, transport, config=None, credentials=(FAKE_USER, FAKE_PASSWORD), now=None):
    """构造位：**transport 只能是关键词**，所以任何一档测试都点得出自己注入的是假的。"""
    return ZjuCollector(
        transport=transport,
        config=config if config is not None else ZjuConfig.load(),
        credentials=credentials,
        now=(now or (lambda: 1_800_000_000.0)),
    )


class TestPasswordEncryptionAgainstTheSiteOwnJs:
    def test_leading_zero_block_is_padded_to_four_hex_digits(self) -> None:
        # 这一条就是那条 1/16 概率的雷：块顶上是 0x011f… 时，不补零会少一个字符，
        # 站方按 4 个一组解回去 ⇒ 密文错位 ⇒ "密码对，却说用户名或密码错误"。
        assert auth.encrypt_password("test123", GOLDEN_MODULUS, GOLDEN_EXPONENT) == (
            "011f0855820888f237d98659b1bbf27c1d00a002a8ccb9fe04b53b4c018c75ee1a648ac90"
            "7e8464fa52e1e0bc88ac567f2416fce4d7f8027fc8aa7c3d56cd431"
        )

    def test_long_password_splits_into_chunks_joined_by_space(self) -> None:
        cipher = auth.encrypt_password("x" * 70, GOLDEN_MODULUS, GOLDEN_EXPONENT)
        blocks = cipher.split(" ")
        assert len(blocks) == 2, "70 个码元 > chunkSize(62) ⇒ 必须两块"
        assert all(len(block) % 4 == 0 for block in blocks)

    def test_chunk_size_follows_the_modulus_instead_of_being_hardcoded(self) -> None:
        wide = "f" + "0" * 255  # 1024 位模数 ⇒ chunkSize = 2 * (64 - 1) = 126 个码元
        short_blocks = auth.encrypt_password("y" * 200, GOLDEN_MODULUS, GOLDEN_EXPONENT).split(" ")
        wide_blocks = auth.encrypt_password("y" * 200, wide, GOLDEN_EXPONENT).split(" ")
        assert len(short_blocks) == 4  # 200 / 62 → 4 块（补齐到 248）
        assert len(wide_blocks) == 2  # 200 / 126 → 2 块（补齐到 252）


class TestAuthenticationIsJudgedByTheBusinessEndpoint:
    def test_success_never_asks_api_user_because_403_is_not_logout(self) -> None:
        transport = FakeTransport(routes=ok_routes())
        state, detail, rows = auth.authenticate(transport, FAKE_USER, FAKE_PASSWORD)
        assert state == STATE_READY
        assert len(rows) == 2
        assert not transport.asked_api_user(), "拿 /api/user 的 403 判掉线会每轮重登"
        assert "ticket=" not in detail, "一次性票据不许进状态文件"

    def test_captcha_required_stops_before_posting(self) -> None:
        transport = FakeTransport(routes=ok_routes(kaptcha="true"))
        state, detail, rows = auth.authenticate(transport, FAKE_USER, FAKE_PASSWORD)
        assert state == STATE_CAPTCHA_REQUIRED
        assert rows == []
        assert not transport.posted(), "站方要人工验证码时再 POST 就是白送一次失败计数"

    def test_network_failure_is_its_own_cell_not_a_password_error(self) -> None:
        transport = FakeTransport(routes=ok_routes(), fail_on=(auth.LOGIN_ENTRY_URL,))
        state, detail, _ = auth.authenticate(transport, FAKE_USER, FAKE_PASSWORD)
        assert state == STATE_NETWORK_UNREACHABLE
        assert "假断线" in detail

    def test_site_credential_phrase_maps_to_auth_failed(self) -> None:
        routes = ok_routes()
        routes[auth.TODOS_URL] = (200, auth.CAS_ORIGIN + "/cas/login", LOGIN_HTML)
        routes[auth.CAS_ORIGIN + "/cas/login"] = (200, auth.CAS_ORIGIN + "/cas/login", BAD_PASSWORD_HTML)
        state, _, rows = auth.authenticate(FakeTransport(routes=routes), FAKE_USER, FAKE_PASSWORD)
        assert state == STATE_AUTH_FAILED
        assert rows == []

    def test_unrecognisable_response_is_page_shape_change_not_password_error(self) -> None:
        routes = ok_routes()
        routes[auth.TODOS_URL] = (200, auth.CAS_ORIGIN + "/cas/login", LOGIN_HTML)
        routes[auth.CAS_ORIGIN + "/cas/login"] = (200, auth.COURSES_ORIGIN + "/weird", APP_HOME_HTML)
        state, _, _ = auth.authenticate(FakeTransport(routes=routes), FAKE_USER, FAKE_PASSWORD)
        assert state == STATE_LOGIN_CHANGED, (
            "认不出回话时不许冒充「用户名或密码不对」——那会把人推去改一个没错的密码"
        )

    def test_alive_session_without_form_is_ready_not_relogin(self) -> None:
        routes = {
            auth.LOGIN_ENTRY_URL: (200, auth.COURSES_ORIGIN + "/home", APP_HOME_HTML),
            auth.TODOS_URL: (200, auth.TODOS_URL, TODOS_JSON),
        }
        transport = FakeTransport(routes=routes)
        state, _, rows = auth.authenticate(transport, FAKE_USER, FAKE_PASSWORD)
        assert state == STATE_READY and len(rows) == 2
        assert not transport.posted(), "会话还活着就别再打一次 CAS"


class TestFailureVocabulary:
    def test_the_three_ruled_cells_exist_and_say_different_things(self) -> None:
        notes = {state: note_for(state) for state in FAILURE_STATES}
        assert len(set(notes.values())) == 3
        assert "站方不区分" in notes[STATE_AUTH_FAILED], (
            "zjuam 对「账号不存在」与「密码错」给同一句话，实现不许拆两格"
        )
        assert "代理" in notes[STATE_NETWORK_UNREACHABLE], "可达性随代理/TUN 翻面，这句必须带着"

    def test_unknown_state_does_not_invent_a_sentence(self) -> None:
        assert "没记到" in note_for("suddenly_fine")

    def test_every_declared_state_has_its_own_sentence(self) -> None:
        assert len({note_for(state) for state in SOURCE_STATES}) == len(SOURCE_STATES)


class TestRowMapping:
    def test_homework_lands_as_ddl_at_local_naive_due_time(self) -> None:
        rows = json.loads(TODOS_JSON)["todo_list"]
        items, dropped = to_occurrences(rows)
        assert dropped == []
        first = items[0]
        assert first.source == "zju" and first.kind == "ddl"
        assert first.external_id == "1182287"
        assert first.evidence["uid"] == "1182287", "去重键放 evidence，表里没有 external-id 列"
        assert first.end_at is None, "ddl 是点位事件"
        # UTC 15:59 → 本地无时区（这台 +08）；不转就会每次 pull 都被 ingest 判成"被改动"
        assert first.start_at == to_local_naive("2027-01-05T15:59:00Z")
        assert "Z" not in first.start_at and "+" not in first.start_at
        assert first.evidence["due"] == "2027-01-05T15:59:00Z", "原值留着才是可对账的证据"

    def test_locked_homework_is_data_not_an_error(self) -> None:
        rows = json.loads(TODOS_JSON)["todo_list"]
        items, dropped = to_occurrences(rows)
        assert len(items) == 2 and dropped == []
        locked = [item for item in items if item.evidence["is_locked"]][0]
        assert locked.evidence["prerequisite_count"] == 1

    def test_unrecognised_rows_are_counted_never_silently_dropped(self) -> None:
        rows = [
            {"id": 1, "type": "news", "is_student": True, "title": "t", "end_time": "2026-10-11T00:00:00Z"},
            {"id": 2, "type": "homework", "is_student": False, "title": "t", "end_time": "2026-10-11T00:00:00Z"},
            {"id": 3, "type": "homework", "is_student": True, "title": "", "end_time": "2026-10-11T00:00:00Z"},
            {"id": 4, "type": "homework", "is_student": True, "title": "t", "end_time": "2026-10-11"},
            {"type": "homework", "is_student": True, "title": "t", "end_time": "2026-10-11T00:00:00Z"},
        ]
        items, dropped = to_occurrences(rows)
        assert items == []
        assert [entry["reason"] for entry in dropped] == [
            "type!=homework",
            "not_student",
            "missing_title_or_end_time",
            "bad_end_time",
            "missing_id",
        ]

    def test_naive_timestamp_is_refused_instead_of_guessed(self) -> None:
        with pytest.raises(ValueError):
            to_local_naive("2026-10-11T09:00:00")


class TestCollectorContract:
    def test_pull_raises_instead_of_returning_an_empty_list(self, zju_paths) -> None:
        # 采集器先打业务端点、再走 CAS，所以要断的是"两条路都断了"。
        transport = FakeTransport(routes=ok_routes(), fail_on=(auth.TODOS_URL, auth.LOGIN_ENTRY_URL))
        collector = make_collector(transport=transport)
        with pytest.raises(ZjuUnavailable) as caught:
            collector.pull()
        assert caught.value.state == STATE_NETWORK_UNREACHABLE

    def test_missing_credentials_are_their_own_cell(self, zju_paths) -> None:
        collector = make_collector(
            transport=FakeTransport(routes=ok_routes()), credentials=None
        )
        report = collector.pull_report()
        assert report.state == STATE_NO_CREDENTIAL
        assert "没配" in report.note
        assert collector.health()["state"] == STATE_NO_CREDENTIAL

    def test_disabled_switch_is_not_shown_as_zero_homework(self, zju_paths) -> None:
        transport = FakeTransport(routes=ok_routes())
        collector = make_collector(transport=transport, config=ZjuConfig(enabled=False))
        assert collector.pull_report().state == STATE_DISABLED
        assert not transport.calls, "开关关着就连学校都不该碰"

    def test_throttle_gives_the_next_round_no_business_hit(self, zju_paths) -> None:
        clock = {"now": 1_800_000_000.0}
        transport = FakeTransport(routes=ok_routes())
        collector = make_collector(
            transport=transport,
            config=ZjuConfig(min_minutes_between_pulls=45, jitter_minutes=0),
            now=lambda: clock["now"],
        )

        assert collector.pull_report().state == STATE_READY
        first_calls = len(transport.calls)
        clock["now"] += 10 * 60  # 10 分钟后
        report = collector.pull_report()
        assert report.state == STATE_THROTTLED
        assert len(transport.calls) == first_calls, "没到最短间隔就一发都不该打"
        assert read_state()["last_state"] == STATE_READY, "上一轮的读数不能被这一轮抹掉"

        clock["now"] += 40 * 60  # 累计 50 分钟 > 45
        assert collector.pull_report().state == STATE_READY

    def test_jitter_never_shortens_the_floor_and_breaks_a_fixed_beat(self) -> None:
        config = ZjuConfig(min_minutes_between_pulls=30, jitter_minutes=10)
        floor = 30 * 60
        assert not any(config.pull_allowed_now(0.0, floor - 1) for _ in range(200)), (
            "最短间隔之内一律不许打 —— 抖动只准加在窗口上，不准往下咬"
        )
        assert all(config.pull_allowed_now(0.0, floor + 10 * 60 + 1) for _ in range(200)), (
            "min + jitter 之外必须一律允许，否则上限就成了无限期"
        )
        middle = [config.pull_allowed_now(0.0, floor + 5 * 60) for _ in range(200)]
        assert any(middle) and not all(middle), "窗口中段带随机 ⇒ 轮询不是固定节拍（站方看到的不是同步的锤子）"
        strict = ZjuConfig(min_minutes_between_pulls=30, jitter_minutes=0)
        assert all(strict.pull_allowed_now(0.0, 31 * 60) for _ in range(50))
        assert not any(strict.pull_allowed_now(0.0, 29 * 60) for _ in range(50))

    def test_second_pull_of_the_same_session_lands_idempotently(self, zju_paths) -> None:
        service = AgendaService(zju_paths.parent / "agenda.db")
        transport = FakeTransport(routes=ok_routes())
        collector = make_collector(
            transport=transport, config=ZjuConfig(min_minutes_between_pulls=0, jitter_minutes=0)
        )
        first = collector.collect(service=service)
        assert first["created"] == 2 and first["state"] == STATE_READY
        clock_skipped = collector.collect(service=service)
        assert clock_skipped["created"] == 0, "同一批发第二遍不许多出条目"
        assert clock_skipped["unchanged"] + clock_skipped["updated"] == 2
        pending = service.list_pending()
        assert {event.source for event in pending} == {"zju"}

    def test_apply_mode_is_read_from_config_not_hardcoded(self, zju_paths) -> None:
        service = AgendaService(zju_paths.parent / "agenda.db")
        collector = make_collector(
            transport=FakeTransport(routes=ok_routes()),
            config=ZjuConfig(apply="confirmed", min_minutes_between_pulls=0, jitter_minutes=0),
        )
        result = collector.collect(service=service)
        assert result["apply"] == "confirmed"
        assert service.list_pending() == [], "配置说坐实就别再造待确认卡"

    def test_unknown_apply_keeps_running_current_posture_and_says_so(self, tmp_path) -> None:
        path = tmp_path / "zju.json"
        path.write_text(json.dumps({"apply": "confrmed"}), encoding="utf-8")  # 少打一个字母
        config = ZjuConfig.load(path)
        assert config.apply == "pending", "拼错不许静默跳到另一档"
        assert config.apply_as_written == "confrmed", "面上要说得出「你写的 confrmed 不认识」"

    def test_health_without_probe_touches_no_network(self, zju_paths) -> None:
        transport = FakeTransport(routes=ok_routes())
        collector = make_collector(transport=transport)
        assert collector.health()["state"] == STATE_THROTTLED
        assert transport.calls == [], "面上每次刷新都打一遍 CAS 就是我们不承诺的流量"
        assert collector.health(probe=True)["ok"] is True


class TestCredentialsStayOutOfEveryProducedString:
    def test_plaintext_password_is_never_sent_and_never_reported(self, zju_paths) -> None:
        # 直接走认证链：采集器那侧先打业务端点，会话热着就不 POST，拿不到表单体。
        transport = FakeTransport(routes=ok_routes())
        state, detail, rows = auth.authenticate(transport, FAKE_USER, FAKE_PASSWORD)
        assert state == STATE_READY and len(rows) == 2

        body = transport.posted_bodies[0]
        assert body["username"] == FAKE_USER, "用户名是站方表单的字段，不是我们外泄"
        assert body["password"] != FAKE_PASSWORD, "口令必须是密文"
        assert FAKE_PASSWORD not in body["password"]
        assert all(chunk != FAKE_PASSWORD for chunk in body["password"].split(" "))

        readings = [state, detail, note_for(state), json.dumps(read_state(), ensure_ascii=False)]
        collector = make_collector(transport=FakeTransport(routes=ok_routes()))
        collector.pull_report()
        readings.append(json.dumps(read_state(), ensure_ascii=False))
        assert not any(FAKE_PASSWORD in text for text in readings), "口令不许进状态文件、面上那句话或日志串"
        assert not any("ticket=" in text for text in readings), "一次性票据不许落盘"

    def test_credentials_come_from_the_one_true_source(self, monkeypatch) -> None:
        from plobi_cli import config as cli_config

        asked: list[str] = []

        def fake_get(key):
            asked.append(key)
            return {
                auth.CREDENTIAL_USER_KEY: FAKE_USER,
                auth.CREDENTIAL_PASSWORD_KEY: FAKE_PASSWORD,
            }.get(key)

        monkeypatch.setattr(cli_config, "get_env_value_prefer_dotenv", fake_get)
        assert auth.read_credentials() == (FAKE_USER, FAKE_PASSWORD)
        assert asked == [auth.CREDENTIAL_USER_KEY, auth.CREDENTIAL_PASSWORD_KEY]

    def test_the_package_does_not_open_a_second_credential_read_path(self, monkeypatch) -> None:
        """真源说没有，就不许退回去自己读 ``os.environ``（裁定 89 甲 / 裁定 110 二）。

        抄第二份读路就会有两份不一致的"真源"——判据 7 那个静默回落是同族病。
        """
        from plobi_cli import config as cli_config

        monkeypatch.setattr(cli_config, "get_env_value_prefer_dotenv", lambda key: None)
        monkeypatch.setenv(auth.CREDENTIAL_USER_KEY, FAKE_USER)
        monkeypatch.setenv(auth.CREDENTIAL_PASSWORD_KEY, FAKE_PASSWORD)
        assert auth.read_credentials() is None, "shell 里有值不等于配了凭据：读路只有那一条"


class TestRegistryDeclaresMeasuredBitsOnly:
    def test_zju_bits_carry_what_was_measured_and_nothing_else(self) -> None:
        caps = registry.spec("zju").capabilities
        assert caps["history_backfill"] is True, "实测：一发就回 48 条"
        assert caps["supports_media"] is False, "实测：11 个字段全文本/布尔/数组"
        assert caps["requires_manual_setup"] is True
        assert caps["incremental_since"] is False, "我们这侧不认 since —— 每轮全量 + 按 uid 幂等"

    def test_unmeasured_cancel_policy_still_refuses_to_be_guessed(self) -> None:
        with pytest.raises(registry.CapabilityUnmeasured):
            registry.require("zju", "authoritative_replace")

    def test_zju_is_registered_and_now_has_an_executor(self) -> None:
        """这一格 10-15 翻过一次：从前记 False 的理由是"没有触发方"，现在执行方补上了。

        旧读数不许留在钉子里 —— 但它讲的那件事仍然是规矩：**光有开关不算在采**，
        所以两条分开钉：开关（用户 2026-10-15 13:0x 拍的"每天自动采，新条目进待确认"）
        与构造入口（`SourceSpec.builder`，缺它 `sweep` 会报 `no_builder` 而不是静默）。
        """
        assert "zju" in registry.source_ids()
        assert "zju" in registry.collecting_sources(), "开关这一格现在是被点过的读数，不是默认值"
        assert callable(registry.spec("zju").builder), "点了开关却构造不出采集器 = 仍然不会在采"


class TestNoSilentFallbackInTestConstructions:
    @staticmethod
    def _call_spans(source: str, needle: str) -> list[str]:
        """按**平衡括号**取出每一处调用的原文——行数会骗人（这条线已经为它交过一次学费）。"""
        spans: list[str] = []
        candidates = []
        cursor = 0
        while True:
            index = source.find(needle, cursor)
            if index < 0:
                break
            cursor = index + 1
            # 定义行不算调用点（`def make_collector(*, transport, …)` 里没有 transport= 这种写法）。
            if source[:index].rstrip().endswith("def"):
                continue
            # 跳过"把名字写在字符串里"的那些（本档自己就在字符串里写了它）：
            # 真调用的下一个字符不会是引号。
            if source[index + len(needle)] in ('"', "'"):
                continue
            candidates.append(index)

        for index in candidates:
            depth = 0
            for cursor in range(index + len(needle) - 1, len(source)):
                char = source[cursor]
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                    if depth == 0:
                        spans.append(source[index : cursor + 1])
                        break
        return spans

    def test_every_construction_of_the_collector_injects_a_transport(self) -> None:
        """裁定 104 二：构造里能悄悄够到真外部调用的，测试必须显式注入假的。"""
        source = Path(__file__).read_text(encoding="utf-8")
        direct = self._call_spans(source, "ZjuCollector(")
        assert direct, "一处构造都没有？那这条守卫就是死的"
        for call in direct:
            assert "transport=" in call, call[:120]

        helpers = self._call_spans(source, "make_collector(")
        assert len(helpers) > 1, "本档全靠 helper 构造，helper 一处都没有就说明测试没跑起来"
        for call in helpers:
            assert "transport=" in call, f"这处构造没点名注入 transport：{call[:100]}"

    def test_the_collector_has_no_default_transport(self) -> None:
        """真身那一格：**没有默认 transport**，所以产品接线时必须自己点名走网络。"""
        import inspect

        signature = inspect.signature(ZjuCollector.__init__)
        assert signature.parameters["transport"].default is inspect.Parameter.empty
