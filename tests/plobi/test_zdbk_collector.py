"""`zdbk`（本科教务）这一条的钉子：全用假 transport，**一档都不打真网络、零额度**。

跑法：`bash scripts/run_tests.sh tests/plobi/test_zdbk_collector.py -q`

这一档真正要钉的是裁定 194 的那句话——**一条信源＝一个网站**：同一个采集器一次拉回
课表／考试／成绩／主修／实践分好几种 `kind`，而不是五种数据各一把采集器。
外加三条"不许静默"：撞验证码就停、凭据没配就不碰学校、信封形状不对不许说成"没数据"。

RSA 那对模数与指数是假值（与学校无关），沿用 `test_zju_collector.py` 那一族的做法：
`encrypt_password` 的补零规则要真跑一遍才知道有没有错，而它错了就是"密码明明对却报认证失败"。
"""

from __future__ import annotations

import json
from typing import Optional

import pytest

from plobi.agenda.service import AgendaService
from plobi.collectors import registry
from plobi.collectors.zdbk.collector import (ZdbkCollector, ZdbkConfig, collapse_of, to_occurrences)
from plobi.collectors.zdbk.status import (
    STATE_AUTH_FAILED,
    STATE_CAPTCHA_REQUIRED,
    STATE_NO_CREDENTIAL,
    ZdbkUnavailable,
)
from plobi.collectors.zju import auth

GOLDEN_MODULUS = (
    "f0123456789abcdef9741ac2d5b3f0e68c1d7e2b4903a5f66aa55cc33ee7711990b1e2d3c4b5a6978"
    "1234567890abcdeffedcba09876543210123456789abcde"
)
GOLDEN_EXPONENT = "10001"

CAS_FORM_HTML = (
    '<html><body><form id="fm1" action="https://zjuam.zju.edu.cn/cas/login?service=x">'
    '<input type="hidden" name="execution" value="exe-token-1" />'
    '<input type="hidden" id="uuid" value="u-1" /></form></body></html>'
)
MENU_HTML = (
    '<html><body><form><input type="hidden" name="su" id="su" value="3160100000"/></form>'
    "</body></html>"
)
KB_JSON = json.dumps({"kbList": [
    {"xkkh": "2026A100001-1-2", "kcb": "高等数学（上）<br>王老师<br>玉泉X-214",
     "xqj": "1", "djj": "2", "skcd": "2", "xxq": "秋", "jszgh": "9876543",
     "pageable": {}, "queryModel": {}, "userModel": {}, "totalResult": "65"},
    {"xkkh": "2026A100002-3-1", "kcb": "电工电子学<br>李老师<br>教三201",
     "xqj": "3", "djj": "1", "skcd": "2", "xxq": "秋", "jszgh": "1234567"},
],
    # 10-22 第五轮探查量的：节次时间**只在 `sjkList` 里**（实测 2 行，`sjkcmc` 27 个中文字符），
    # 课表行里一个钟点都没有
    "sjkList": [{"sjkcmc": "第1-2节（08:00-09:40）", "rsdzjs": "1", "jgpxzd": "1"},
                {"sjkcmc": "第3-4节（10:00-11:40）", "rsdzjs": "1", "jgpxzd": "2"}],
    "kbcdList": [{"kbcd": "5"}], "xnm": "2026", "xqm": "12", "xh": "3160100000",
    "xm": "假学生", "xy": "计算机学院", "zy": "计算机科学与技术"}, ensure_ascii=False)
EXAM_JSON = json.dumps({"items": [
    {"row_id": "ex-1", "xkkh": "2026A100001-1-2", "kcmc": "高等数学（上）",
     "kssj": "2026-01-06 09:00:00", "jgpxzd": "1", "userModel": {}},
], "totalCount": 1, "totalPage": 1}, ensure_ascii=False)
GRADE_JSON = json.dumps({"items": [
    {"row_id": "gr-1", "xkkh": "2026A100009-2-1", "kcmc": "大学物理", "cj": "88", "jd": "3.9",
     "kssj": "2026-01-08 14:00:00", "xf": "3"},
], "totalCount": 1, "totalPage": 1}, ensure_ascii=False)
MAJOR_JSON = json.dumps({"items": [
    # 10-22 量的形状：主修 238 行里 **217 行没有 `xkkh`**（计划内课程不是选课记录），
    # 但它们都带 `xn`/`xq`/`xqmc` ⇒ 学期那一格是从这里归并出来的
    {"row_id": "1", "kcdm": "CS1002G", "kcmc": "电子工程训练", "xf": "2",
     "xn": "2025", "xq": "1", "xqmc": "秋冬学期"},
    {"row_id": "2", "kcdm": "PHIL0702G", "kcmc": "大学物理（甲）Ⅱ", "xf": "5",
     "xn": "2025", "xq": "2", "xqmc": "春夏学期"},
    {"row_id": "3", "xkkh": "2025A100003-1-1", "kcdm": "MATH0101G", "kcmc": "高等数学（上）",
     "xf": "4", "xn": "2025", "xq": "1", "xqmc": "秋冬学期"},
], "totalCount": 3, "totalPage": 1}, ensure_ascii=False)
PRACTICE_HTML = (
    "<html><table>"
    "<tr><th>项目名称</th><th>分值</th></tr>"
    "<tr><td>志愿服务</td><td>2</td></tr>"
    "<tr><td>创业讲座</td><td>1</td></tr>"
    "</table></html>"
)


class FakeTransport:
    def __init__(self, routes: dict[str, tuple[int, str]], *, fail_on: tuple[str, ...] = ()):
        self.routes = routes
        self.fail_on = fail_on
        self.calls: list[tuple[str, str]] = []

    def _respond(self, method: str, url: str) -> auth.HttpResponse:
        self.calls.append((method, url))
        if any(needle in url for needle in self.fail_on):
            raise auth.TransportError(f"假断线：{url}")
        for needle, (status, text) in self.routes.items():
            if needle in url:
                return auth.HttpResponse(status=status, url=url, text=text)
        return auth.HttpResponse(status=404, url=url, text="{}")

    def get(self, url: str) -> auth.HttpResponse:
        return self._respond("GET", url)

    def post_form(self, url, data, *, referer=None) -> auth.HttpResponse:
        return self._respond("POST", url)


def healthy_transport(**extra) -> FakeTransport:
    routes = {
        auth.ZDBK_SSO_ENTRY: (200, CAS_FORM_HTML),
        auth.CAS_ORIGIN + "/cas/login": (200, CAS_FORM_HTML),
        auth.KAPTCHA_URL: (200, "false"),
        auth.PUBKEY_URL: (200, json.dumps({"modulus": GOLDEN_MODULUS, "exponent": GOLDEN_EXPONENT})),
        auth.ZDBK_MENU_URL: (200, MENU_HTML),
        "xskbcx_cxXsKb": (200, KB_JSON),
        "kscx_cxXsgrksIndex": (200, EXAM_JSON),
        "xscjcx_cxXscjIndex": (200, GRADE_JSON),
        "xszgkc_cxXsZgkcIndex": (200, MAJOR_JSON),
        "dessktcx_cxDessktcxIndex": (200, PRACTICE_HTML),
    }
    routes.update(extra)
    return FakeTransport(routes)


def make_collector(transport, *, pauses: Optional[list] = None, **kwargs) -> ZdbkCollector:
    """``sleep`` 一律注成记录器：礼貌延时那一格**不许让测试真等**，但歇了几次要数得出。

    ``spread`` 固定取区间下沿 ⇒ "歇没歇、歇多久"是一个确定的读数，不是每次跑出一个不同的数。
    """
    log = pauses if pauses is not None else []
    kwargs.setdefault("sleep", log.append)
    kwargs.setdefault("spread", lambda low, high: low)
    return ZdbkCollector(transport=transport, credentials=("3160100000", "fake-passwd-42"),
                         config=ZdbkConfig(year="2026", season="12", include_practice=True,
                                           min_minutes_between_pulls=0, jitter_minutes=0),
                         **kwargs)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """配置与节流状态都钉进 tmp_path —— 不然测试会读/写**真的** `zdbk_state.json`，
    而那份文件里现在就有今天 08:15 那一轮的时间戳（读到位就直接被判成节流）。"""
    monkeypatch.setenv("PLOBI_ZDBK_CONFIG", str(tmp_path / "zdbk.json"))


@pytest.fixture()
def pool_db(tmp_path, monkeypatch):
    from plobi.pool import store as pool_store

    db = tmp_path / "pool.db"
    monkeypatch.setattr(pool_store, "path", lambda: db)
    return db


class TestOneSiteManyKinds:
    """裁 194 的牙：一条信源采一个网站，`kind` 区分种类。"""

    def test_pull_returns_everything_this_site_gives(self) -> None:
        items = make_collector(healthy_transport()).pull()

        kinds = {item.kind for item in items}
        assert {"class", "exam", "grade", "major", "practice"} <= kinds, kinds
        assert all(item.source == "zdbk" for item in items)

    def test_practice_rows_come_from_the_html_table(self) -> None:
        items = [item for item in make_collector(healthy_transport()).pull() if item.kind == "practice"]

        assert len(items) == 2, "表头那行不算条目"
        assert items[0].title == "志愿服务"
        assert items[0].evidence["cells"] == ["志愿服务", "2"]

    def test_class_is_a_week_template_and_gets_no_invented_datetime(self) -> None:
        """`occurred_at` 空是一个**读数**：这一家给的是星期×节次，学期起始日没实测。"""
        item = next(i for i in make_collector(healthy_transport()).pull() if i.kind == "class")

        assert item.start_at == ""
        assert item.evidence["week_template"] is True
        assert (item.evidence["weekday"], item.evidence["start_slot"]) == ("1", "2")
        assert item.evidence["kind_label"] == "class"

    def test_title_is_the_first_segment_of_the_br_string(self) -> None:
        # 身份是**格子坐标**（选课号 + 小学期 + 星期 + 第几节），不是光秃秃的选课号
        item = next(i for i in make_collector(healthy_transport()).pull()
                    if i.external_id.startswith("class:2026A100001-1-2"))

        assert item.title == "高等数学（上）"
        assert "王老师" in item.evidence["kcb"]        # 教师与地点不丢，只是不当列


class TestIdentityIsTheSitesOwnId:
    def test_identity_key_is_recorded_and_not_the_title(self) -> None:
        items = [i for i in make_collector(healthy_transport()).pull() if i.kind == "exam"]

        # 10-22 探查量的：考试那一格 `xkkh`＋`kssj` 是 40/40（学校给 40 行、去重还是 40）；
        # 光 `xkkh` 那一天也给 40/40，但 08:15 那一轮它只落过 15 条 ⇒ 用更严的那一组
        assert items[0].external_id == "exam:2026A100001-1-2:2026-01-06 09:00:00"
        assert items[0].evidence["identity_key"] == "xkkh+kssj"

    def test_two_kinds_sharing_one_school_id_are_not_the_same_row(self) -> None:
        """`pool_items` 的唯一键是 `(source, external_id)`——**`kind` 不在键里**。

        现读（10-22 量的）：主修 238 行里 217 行连 `xkkh` 都没有，唯一能唯一化它们的只有
        正方那个**页内序号** `row_id`（值是纯数字）。所以"考试第 7 行"和"主修第 7 行"在学校给的
        id 空间里是同一个字符串：不加一层命名空间，后落的那条会把前一条原地盖掉——
        池里少一行，而且一声不响。
        """
        exam = to_occurrences("exam", [{"row_id": "7", "kcmc": "高等数学",
                                       "kssj": "2026-01-06 09:00:00"}])[0]
        major = to_occurrences("major", [{"row_id": "7", "kcmc": "线性代数"}])[0]

        assert exam.external_id != major.external_id
        assert (exam.source, exam.external_id) != (major.source, major.external_id)
        assert exam.evidence["identity_key"] == "row-sha1", "序号不许当身份：退内容哈希要说得出"

    def test_renamed_course_keeps_the_same_identity(self) -> None:
        rows = [{"xkkh": "keep-me", "kcb": "旧名字<br>王老师<br>X-214", "xqj": "1", "djj": "1", "skcd": "2"}]
        before = to_occurrences("class", rows)[0]
        after = to_occurrences("class", [dict(rows[0], kcb="新名字<br>王老师<br>X-214")])[0]

        assert before.external_id == after.external_id
        assert before.external_id.startswith("class:keep-me")


class TestIdentityIsEverySlotNotEveryCourse:
    """10-22 真数据量出来的 bug：65 行 `kbList` 只进池 3 条。

    一门课一周上几次就有几个格子，而 `xkkh` 对同一门课是**同一个值**——拿它当身份，
    这一门课的几次课就在池里互相覆盖（还让每轮的 `updated` 数在 108/147 之间跳）。
    身份必须是格子坐标。10-22 第五轮探查又量了一格：`xkkh+xxq+xqj+djj` 是 **64/65**，
    少的那一条是同一格换过一次上法（`skcd` 连上节数不同）⇒ 坐标要带上 `skcd` 才 65/65。
    """

    def test_the_same_course_in_several_slots_is_several_rows(self) -> None:
        kb = [{"xkkh": "K1", "kcb": "高数<br>王老师<br>X-1", "xqj": day, "djj": slot,
               "skcd": "2", "xxq": "1"} for day in ("1", "3", "5") for slot in ("1", "3")]

        items = to_occurrences("class", kb)

        assert len(items) == len(kb)
        assert len({item.external_id for item in items}) == 6
        assert items[0].evidence["identity_key"] == "xkkh+xxq+xqj+djj+skcd"


class TestThrottleIsReal:
    """`min_minutes_between_pulls` 这一格以前只是写着——那等于没有。"""

    def test_a_second_round_inside_the_window_does_not_touch_the_site(self) -> None:
        from plobi.collectors.zdbk import state as zdbk_state

        transport = healthy_transport()
        clock = {"now": 1_800_000_000.0}
        collector = ZdbkCollector(transport=transport, credentials=("3160100000", "p"),
                                  config=ZdbkConfig(min_minutes_between_pulls=45, jitter_minutes=0),
                                  now=lambda: clock["now"], sleep=lambda seconds: None)
        collector.pull()
        before = len(transport.calls)

        with pytest.raises(ZdbkUnavailable) as raised:
            collector.pull()

        assert raised.value.state == "throttled"
        assert len(transport.calls) == before, "节流没到点就连学校都不该碰"
        assert zdbk_state.read_last_pull_at() is not None

    def test_the_same_course_data_across_the_window_is_idempotent(self, pool_db) -> None:
        from plobi.pool import stats as pool_stats

        clock = {"now": 1_800_000_000.0}
        first = ZdbkCollector(transport=healthy_transport(),
                              credentials=("3160100000", "p"),
                              config=ZdbkConfig(min_minutes_between_pulls=0, jitter_minutes=0),
                              now=lambda: clock["now"], sleep=lambda seconds: None)
        made = first.collect()["created"]
        clock["now"] += 3600
        second = ZdbkCollector(transport=healthy_transport(),
                               credentials=("3160100000", "p"),
                               config=ZdbkConfig(min_minutes_between_pulls=0, jitter_minutes=0),
                               now=lambda: clock["now"], sleep=lambda seconds: None)
        again = second.collect()

        assert made > 0 and again["created"] == 0
        assert again["updated"] == 0, "同一批数据第二轮不该改任何一行（改了就是身份或内容有抖动）"
        assert pool_stats()["total"] == made


class TestNothingIsSilent:
    def test_captcha_stops_the_round_without_recognising_anything(self) -> None:
        transport = healthy_transport(**{"xskbcx_cxXsKb": (200, '{"code":"captcha_error"}')})

        with pytest.raises(ZdbkUnavailable) as raised:
            make_collector(transport).pull()

        assert raised.value.state == STATE_CAPTCHA_REQUIRED
        assert not [c for c in transport.calls if "kscx_cxXsgrksIndex" in c[1]], \
            "撞验证码之后还继续打其它端点＝把「要人工」这句话淹掉了"

    def test_missing_credentials_touch_no_network(self) -> None:
        transport = healthy_transport()
        collector = ZdbkCollector(transport=transport, credentials=None,
                                  credential_reader=lambda: None)

        with pytest.raises(ZdbkUnavailable) as raised:
            collector.pull()

        assert raised.value.state == STATE_NO_CREDENTIAL
        assert transport.calls == []

    def test_unrecognised_envelope_is_auth_failed_not_an_empty_list(self) -> None:
        transport = healthy_transport(**{"kscx_cxXsgrksIndex": (200, "<html>不是 JSON</html>")})

        with pytest.raises(ZdbkUnavailable) as raised:
            make_collector(transport).pull()

        assert raised.value.state == STATE_AUTH_FAILED

    def test_network_failure_is_its_own_cell(self) -> None:
        transport = healthy_transport()
        transport.fail_on = (auth.ZDBK_MENU_URL,)

        with pytest.raises(ZdbkUnavailable) as raised:
            make_collector(transport).pull()

        assert raised.value.state == "network_unreachable"


class TestLandingIsThePool:
    def test_collect_lands_the_pool_and_writes_no_agenda_rows(self, pool_db, tmp_path) -> None:
        from plobi.agenda import store
        from plobi.pool import stats as pool_stats

        service = AgendaService(tmp_path / "agenda.db")
        result = make_collector(healthy_transport()).collect()

        assert result["source"] == "zdbk" and result["created"] > 0
        assert result["counts"]["class"] == 2 and result["counts"]["practice"] == 2
        assert pool_stats()["by_source"].get("zdbk") == result["created"]
        conn = store.connect(service.db_path)
        try:
            assert store.list_events(conn, source="zdbk", include_cancelled=True) == [], \
                "采集器手里没有落日程的那把工具"
        finally:
            conn.close()

    def test_two_rounds_do_not_grow_the_pool(self, pool_db) -> None:
        from plobi.pool import stats as pool_stats

        first = make_collector(healthy_transport()).collect()
        second = make_collector(healthy_transport()).collect()

        assert first["created"] > 0
        assert second["created"] == 0, "同一批拉第二遍不许多出条目"
        assert pool_stats()["total"] == first["created"]


class TestTheDailyRoundCanDriveIt:
    """`sweep._collect_one` 怎么调这一条，就照那样钉一遍。

    签名对不上不是偶发：`sweep` 传 `service=`，而池不需要它——收下不用比不接这个参数贵，
    前者是一行读数，后者是每天一轮的红（`raised`）。
    """

    def test_sweep_drives_collect_with_the_service_kwarg(self, pool_db, tmp_path) -> None:
        from plobi.collectors import sweep

        service = AgendaService(tmp_path / "agenda.db")

        row = sweep._collect_one("zdbk", make_collector(healthy_transport()), service=service)

        assert row["ok"] is True, row["detail"]
        assert row["state"] == "ready" and row["created"] > 0


class TestRegisteredShape:
    def test_registered_as_a_website_with_all_bits_declared(self) -> None:
        spec = registry.spec("zdbk")

        assert spec.category == "website"
        assert spec.capabilities["history_backfill"] is True
        assert spec.capabilities["authoritative_replace"] is False, \
            "按学期查的端点，缺席不等于取消——这句是实测的结论"
        assert [f.key for f in spec.setup_fields] == ["cadence"]

    def test_the_package_never_calls_the_agenda_seam(self) -> None:
        import re
        from pathlib import Path

        package = Path(registry.__file__).parent / "zdbk"
        offenders = [f"{file.name}:{number}"
                     for file in sorted(package.glob("*.py"))
                     for number, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1)
                     if not line.lstrip().startswith("#") and re.search(r"\bingest_occurrences\s*\(", line)]

        assert offenders == [], f"教务这一条又去直落日程了：{offenders}"


class TestPolitenessBetweenRequests:
    """参照件里唯一值得照抄的那一格：Python 那个 CLI 把礼貌延时做成了参数
    （`celekli.py:641 --time-coeff`，默认 1.5/5 秒）。

    为什么这一格要有牙齿：`pull()` 一次要打**六七个**请求（课表＋考试＋成绩＋主修＋学号那一页
    ＋实践分那一页），全挤在同一秒里就是拿站方的私有接口当自己的数据库刷。注释里写"我们很礼貌"
    不算实现，数得出来的歇才算。
    """

    def test_every_request_after_the_session_gets_one_pause(self) -> None:
        pauses: list[float] = []
        transport = healthy_transport()

        make_collector(transport, pauses=pauses).pull()

        urls = [url for _method, url in transport.calls]
        boundary = next(i for i, url in enumerate(urls) if "index_initMenu" in url)   # 登录探针那一次
        after = urls[boundary + 1:]
        assert len(pauses) == len(after), f"歇了 {len(pauses)} 次但打了 {len(after)} 个请求：{after}"
        assert all(1.5 <= pause <= 4.5 for pause in pauses), pauses

    def test_the_gap_can_be_turned_off_without_turning_the_source_off(self) -> None:
        pauses: list[float] = []
        collector = make_collector(healthy_transport(), pauses=pauses)
        collector.config = ZdbkConfig(year="2026", season="12", request_gap_seconds=(0.0, 0.0))

        assert len(collector.pull()) > 0
        assert pauses == [], "区间下沿是 0 就不该睡——这一格关的是礼貌，不是采集"


class TestTheSchoolsOwnTerms:
    """用户 10-22 的话：「**那就说明你的采集代码遗漏了信息。zdbk 的采集器应该包含学期的采集**」。

    第五轮探查（10-22 15:3x，真账号一轮）量的结论：这一家**没有校历端点可采**——教务首页那 18 KB
    是 JS 挂菜单的壳（按 `<a href="/jwglxt/….html">` 抠到菜单 0 条、动作 URL 1 个，页面上没有
    "校历/学期起止"字样），两家 Dart 里 grep 校历也 0 命中（Celechron 的学期起止来自它自己的服务器）。
    但学校**在自己的行里说了学期**：主修 238 行每行都带 `xn`/`xq`/`xqmc`。所以这一格只能是归并，
    而**日期仍然留空**（裁 189：空是一个读数，不是缺口）。
    """

    def test_terms_land_as_their_own_kind_from_what_the_school_states(self) -> None:
        items = make_collector(healthy_transport()).pull()
        terms = [i for i in items if i.kind == "term"]

        assert len(terms) == 2, "主修三行分布在两个学期（2025/1 两行、2025/2 一行）⇒ 归并成两条"
        assert {i.external_id for i in terms} == {"term:2025:1", "term:2025:2"}
        by_year = {i.external_id: i for i in terms}
        assert by_year["term:2025:1"].title.startswith("2025 秋冬学期")
        assert by_year["term:2025:1"].evidence["records"] == 2      # 这一学期学校给了两行
        assert by_year["term:2025:1"].evidence["credits"] == 6.0    # 学分是加出来的，不是猜的
        assert all(i.start_at == "" for i in terms), "学校没给起止日期 ⇒ 不许推一个出来当数据"

    def test_bell_times_land_because_class_rows_have_no_clock(self) -> None:
        """课表行只有星期×第几节；钟点在 `sjkList`（实测 2 行）。不采它，池里答不出"几点上"。"""
        items = make_collector(healthy_transport()).pull()
        times = [i for i in items if i.kind == "class_time"]

        assert len(times) == 2
        assert any("08:00" in i.title for i in times)
        assert all(i.start_at == "" for i in times), "作息是模板，不是某一天"

    def test_one_pull_still_lands_several_kinds(self) -> None:
        """裁定 194 那颗判据：一条信源在池里**不止一种 `kind`**（不许为每种数据再开一条源）。"""
        kinds = {i.kind for i in make_collector(healthy_transport()).pull()}

        assert {"class", "exam", "grade", "major", "term", "class_time"} <= kinds


class TestCollapseIsNotSilent:
    """10-22 那 46 行是**静默**少的：池里 15 条、学校给 40 行，池、日志、设置面一个字没说。"""

    def test_a_shared_identity_is_reported_in_the_round(self) -> None:
        rows = [{"xkkh": "K", "kssj": "2026-01-06 09:00:00", "kcmc": "同一门课的两个考场"}]
        rows.append(dict(rows[0], zwxh="12"))          # 身份那几格一模一样 ⇒ 会并成一条

        given, distinct = collapse_of("exam", rows)
        report = make_collector(healthy_transport(**{
            "kscx_cxXsgrksIndex": (200, json.dumps(
                {"items": rows, "totalCount": len(rows), "totalPage": 1}, ensure_ascii=False))})).pull_report()[1]

        assert (given, distinct) == (2, 1)
        assert report["collapsed"] == {"exam": "2 行 → 1 条身份"}
        assert report["counts"]["exam"] == 2, "counts 是学校给的行数，不是我算出的条数"

    def test_a_healthy_round_reports_no_collapse(self) -> None:
        report = make_collector(healthy_transport()).pull_report()[1]

        assert report["collapsed"] == {}, f"这一轮的读数该是干净的：{report}"
        assert report["counts"]["major"] == 3 and report["counts"]["term"] == 2
