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
from plobi.collectors.zdbk.collector import ZdbkCollector, ZdbkConfig, to_occurrences
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
]}, ensure_ascii=False)
EXAM_JSON = json.dumps({"items": [
    {"row_id": "ex-1", "kcmc": "高等数学（上）", "kssj": "2026-01-06 09:00:00",
     "jgpxzd": "1", "userModel": {}},
], "totalCount": 1, "totalPage": 1}, ensure_ascii=False)
GRADE_JSON = json.dumps({"items": [
    {"row_id": "gr-1", "kcmc": "大学物理", "cj": "88", "kssj": "2026-01-08 14:00:00",
     "xf": "3"},
], "totalCount": 1, "totalPage": 1}, ensure_ascii=False)
MAJOR_JSON = json.dumps({"items": [
    {"row_id": "mj-1", "kcmc": "电子工程训练", "xf": "2", "xn": "2025", "xq": "1"},
    {"row_id": "mj-2", "kcmc": "大学物理（甲）Ⅱ", "xf": "5", "xn": "2025", "xq": "2"},
], "totalCount": 2, "totalPage": 1}, ensure_ascii=False)
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


def make_collector(transport, **kwargs) -> ZdbkCollector:
    return ZdbkCollector(transport=transport, credentials=("3160100000", "fake-passwd-42"),
                         config=ZdbkConfig(year="2026", season="12", include_practice=True),
                         **kwargs)


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
        item = next(i for i in make_collector(healthy_transport()).pull() if i.external_id == "2026A100001-1-2")

        assert item.title == "高等数学（上）"
        assert "王老师" in item.evidence["kcb"]        # 教师与地点不丢，只是不当列


class TestIdentityIsTheSitesOwnId:
    def test_identity_key_is_recorded_and_not_the_title(self) -> None:
        items = [i for i in make_collector(healthy_transport()).pull() if i.kind == "exam"]

        assert items[0].external_id == "ex-1"
        assert items[0].evidence["identity_key"] == "row_id"

    def test_renamed_course_keeps_the_same_identity(self) -> None:
        rows = [{"xkkh": "keep-me", "kcb": "旧名字<br>王老师<br>X-214", "xqj": "1", "djj": "1", "skcd": "2"}]
        before = to_occurrences("class", rows)[0]
        after = to_occurrences("class", [dict(rows[0], kcb="新名字<br>王老师<br>X-214")])[0]

        assert before.external_id == after.external_id == "keep-me"


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
