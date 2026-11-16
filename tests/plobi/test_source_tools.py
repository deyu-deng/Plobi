"""信源管理的六把原子（WP-SOURCE-TOOLS）：形状、口径、与"值永不出门"。

跑法：`bash scripts/run_tests.sh tests/plobi/test_source_tools.py -q`（唯一入口，不传 `--basetemp`）。
零真网络、零真库：共享家目录与 `.env` 都钉在 `tmp_path` 里。

这一档要钉住的是四件事：
① **形状**（裁定 185）：六把，一件原子事一件工具，参数里**没有** action/intent/mode/purpose；
② **口径只有一份**：状态词表走 `sources_api.status_of()`，不在这儿自造一份；
③ **写的那几把真的落盘**（开关落 `sources.yaml`、频率能被 `resolve_cadence()` 读到），
   写坏的**不落**；
④ **值永不出门**：密钥落 `.env`，回包里只有「设过没有」。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from plobi.collectors import cadence, registry, sources_config
from plobi.collectors.registry import SetupField, SetupGuide, SourceSpec
from tools import source_tools
from tools.registry import registry as tool_registry

CAPS = {
    "history_backfill": False, "incremental_since": False, "supports_media": False,
    "requires_manual_setup": False, "authoritative_replace": False,
}

SOURCE_TOOLS = ("source_list", "source_read", "source_enable", "source_set_cadence",
                "source_configure", "source_collect")

WRITERS = ("source_enable", "source_set_cadence", "source_configure", "source_collect")


@pytest.fixture()
def shared_root(tmp_path, monkeypatch):
    """共享家目录钉进 tmp_path：`sources.yaml`、频率台账、`.env` 都从它派生。

    **非钉不可**：这一族会写盘（翻开关／写频率／落凭据），跑测试碰到真家目录
    就是拿用户的真配置当测试台——凭据那一格尤其（它会写进 `.env`）。
    """
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path))
    monkeypatch.setattr("plobi_constants.get_shared_plobi_root", lambda: tmp_path)
    (tmp_path / "plobi").mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture()
def fake_sources():
    """临时登记几条假信源，用完撤掉——注册表是进程级单例，不许留脏。"""
    registered: list[str] = []

    def _add(*specs: SourceSpec) -> None:
        for spec in specs:
            registry.register(spec, replace=True)
            registered.append(spec.source_id)

    yield _add
    for source_id in registered:
        registry.unregister(source_id)


class FakeCollector:
    def __init__(self, result=None):
        self._result = result or {"created": 3, "unchanged": 1, "state": "ready"}

    def collect(self, service=None):
        return self._result


def _call(name: str, **args) -> dict:
    """调一把工具，回**解过码**的那一包（工具回的是 JSON 字符串）。"""
    handler = tool_registry.get_entry(name).handler
    return json.loads(handler(args))


def _plain_spec(source_id: str = "plain") -> SourceSpec:
    return SourceSpec(source_id, CAPS, collecting=True, category="custom",
                      builder=lambda: FakeCollector())


def _secret_spec(source_id: str = "withsecret") -> SourceSpec:
    return SourceSpec(
        source_id, CAPS, collecting=True, category="custom",
        builder=lambda: FakeCollector(),
        setup_fields=(
            SetupField(key="address", label="Address", required=True),
            SetupField(key="password", label="Password", kind="secret"),
        ),
        setup_guide=SetupGuide(title="Install the client", action="Download",
                               detail="装好客户端并登录"),
    )


class TestShape:
    """六把，一个门类，一件原子事一件工具（裁定 185）。"""

    def test_six_tools_are_registered_under_one_toolset(self) -> None:
        for name in SOURCE_TOOLS:
            entry = tool_registry.get_entry(name)
            assert entry is not None, f"{name} 没注册"
            assert entry.toolset == "sources", f"{name} 不在 sources 门类里"

    def test_no_tool_takes_a_branch_parameter(self) -> None:
        """`action`／`intent`／`mode`／`purpose` 这类选分支的参数＝违例（裁 185 判据③）。"""
        for name in SOURCE_TOOLS:
            schema = tool_registry.get_entry(name).schema
            props = set((schema.get("parameters") or {}).get("properties") or {})
            assert not (props & {"action", "intent", "mode", "purpose"}), \
                f"{name} 的参数里有选分支的那一类：{props}"

    def test_the_whole_family_is_in_the_default_bundle(self) -> None:
        """按 role 过滤就把这件事重新变成特权入口——所以整族进默认束（与池那两枚同一 rung）。"""
        import toolsets

        for name in SOURCE_TOOLS:
            assert name in toolsets._PLOBI_CORE_TOOLS, f"{name} 不在默认束里"

    def test_the_four_writers_are_in_the_mutating_list(self) -> None:
        """循环守卫要认得出它们在改东西。"""
        from agent.tool_guardrails import MUTATING_TOOL_NAMES

        for name in WRITERS:
            assert name in MUTATING_TOOL_NAMES, f"{name} 不在改动名单里"

    def test_the_two_readers_are_not_in_the_mutating_list(self) -> None:
        from agent.tool_guardrails import MUTATING_TOOL_NAMES

        assert "source_list" not in MUTATING_TOOL_NAMES
        assert "source_read" not in MUTATING_TOOL_NAMES


class TestListAndRead:
    def test_listing_gives_every_source_its_reading(self, shared_root, fake_sources) -> None:
        fake_sources(_plain_spec("aaa"), _plain_spec("bbb"))

        out = _call("source_list")

        assert out["ok"] is True
        names = {row["source"] for row in out["sources"]}
        assert {"aaa", "bbb"} <= names
        row = next(r for r in out["sources"] if r["source"] == "aaa")
        for key in ("enabled", "status", "status_code", "cadence", "pool_count", "last_run_at"):
            assert key in row, f"读数里少了 {key}"
        assert row["status"] in ("connected", "disconnected", "error")

    def test_reading_an_unknown_source_says_so_instead_of_inventing_one(self, shared_root) -> None:
        out = _call("source_read", source_id="nope")

        assert out["ok"] is False
        assert "不是一条信源" in out["error"]

    def test_reading_one_gives_the_declared_fields_and_whether_they_are_set(
            self, shared_root, fake_sources) -> None:
        fake_sources(_secret_spec())

        out = _call("source_read", source_id="withsecret")

        assert out["ok"] is True
        keys = {f["key"]: f for f in out["setup_fields"]}
        assert "address" in keys and keys["address"]["required"] is True
        assert keys["address"]["configured"] is False
        assert keys["password"]["kind"] == "secret"
        assert out["secrets"] == {"password": False}
        assert out["setup_guide"]["action"] == "Download"

    def test_a_secret_value_never_comes_back(self, shared_root, fake_sources) -> None:
        """值只落 `.env`；读数里只有「设过没有」。"""
        fake_sources(_secret_spec())
        sources_config.set_secret("withsecret", "password", "s3cret-value")

        out = _call("source_read", source_id="withsecret")

        assert out["secrets"] == {"password": True}
        assert "s3cret-value" not in json.dumps(out, ensure_ascii=False)


class TestEnable:
    def test_the_switch_lands_and_the_leg_is_told(self, shared_root, fake_sources, monkeypatch) -> None:
        fake_sources(_plain_spec())
        seen: list[tuple] = []
        monkeypatch.setattr("plobi.collectors.sidecar.apply",
                            lambda spec, *, enabled: seen.append((spec.source_id, enabled)) or None)

        out = _call("source_enable", source_id="plain", enabled=True)

        assert out["ok"] is True and out["enabled"] is True
        assert sources_config.effective_enabled("plain")[0] is True
        assert seen == [("plain", True)]

    def test_turning_it_off_also_reaches_the_leg(self, shared_root, fake_sources, monkeypatch) -> None:
        """用户 10-23：「打开还是关闭都要真的切断这条信源的采集程序」——只改一个布尔是假动作。"""
        fake_sources(_plain_spec())
        sources_config.set_enabled("plain", True)
        seen: list[tuple] = []
        monkeypatch.setattr("plobi.collectors.sidecar.apply",
                            lambda spec, *, enabled: seen.append((spec.source_id, enabled)) or None)

        out = _call("source_enable", source_id="plain", enabled=False)

        assert out["ok"] is True and out["enabled"] is False
        assert sources_config.effective_enabled("plain")[0] is False
        assert seen == [("plain", False)]


class TestCadence:
    def test_the_cadence_is_written_and_can_be_read_back(self, shared_root, fake_sources) -> None:
        fake_sources(_plain_spec())

        out = _call("source_set_cadence", source_id="plain", cadence="0 3 * * *")

        assert out["ok"] is True
        assert cadence.resolve_cadence("plain")[0] == "0 3 * * *"

    def test_a_broken_cron_is_refused_and_nothing_is_written(self, shared_root, fake_sources) -> None:
        """写坏的那一句**不落盘**：频率层读的时候才判"不打"，那时已经存进去了。"""
        fake_sources(_plain_spec())

        out = _call("source_set_cadence", source_id="plain", cadence="0 3 * *")

        assert out["ok"] is False
        assert "不认识" in out["error"]
        assert cadence.resolve_cadence("plain")[0] == ""


class TestConfigure:
    def test_params_land_and_the_secret_goes_to_env(self, shared_root, fake_sources) -> None:
        fake_sources(_secret_spec())

        out = _call("source_configure", source_id="withsecret",
                    params={"address": "me@example.test", "password": "s3cret-value"})

        assert out["ok"] is True
        assert out["setup"]["address"] == "me@example.test"
        assert out["secrets"] == {"password": True}
        assert "s3cret-value" not in json.dumps(out, ensure_ascii=False)
        # 密钥**不在** `sources.yaml` 里——`.env` 才是唯一面
        raw = (shared_root / "plobi" / "sources.yaml").read_text(encoding="utf-8")
        assert "s3cret-value" not in raw

    def test_a_missing_required_field_is_refused_and_says_which_one(self, shared_root, fake_sources) -> None:
        fake_sources(_secret_spec())

        out = _call("source_configure", source_id="withsecret", params={"password": "x"})

        assert out["ok"] is False
        assert "address" in out["error"]


class TestCollect:
    def test_it_runs_only_that_source_and_records_the_round(self, shared_root, fake_sources, monkeypatch) -> None:
        """触发**这一条**跑一轮；跑完把执行方自己报的那句话原样回，并记账。"""
        fake_sources(_plain_spec("wanted"), _plain_spec("other"))
        monkeypatch.setattr("plobi.agenda.get_service", lambda: None)

        out = _call("source_collect", source_id="wanted")

        assert out["ok"] is True
        assert out["state"] == "ready"
        assert out["created"] == 3
        # 手点一次也算碰过它了：频率问的是"多久碰一次"
        assert cadence.last_run("wanted")["state"] == "ready"
        assert cadence.last_run("other")["at"] == ""

    def test_a_failing_collector_keeps_its_own_words(self, shared_root, fake_sources, monkeypatch) -> None:
        """失败也原样回，不许在这儿编一句"没数据"。"""
        class Boom:
            def collect(self, service=None):
                raise RuntimeError("站方说不认识这个口令")

        fake_sources(SourceSpec("boom", CAPS, collecting=True, category="custom",
                                 builder=lambda: Boom()))
        monkeypatch.setattr("plobi.agenda.get_service", lambda: None)

        out = _call("source_collect", source_id="boom")

        assert out["ok"] is False
        assert "站方说不认识这个口令" in out["detail"]
