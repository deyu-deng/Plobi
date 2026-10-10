"""判据 7/8 的钉：代码默认值等于 ADR 现行口径；四种"采不到"在面上分得开。

跑法：``bash scripts/run_tests.sh tests/plobi/test_chatlog_current_posture.py -q -j 6``
（唯一入口、不传 ``--basetemp``，裁定 87）。全是关系断言，不冻名单快照。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from plobi.collectors.chatlog import sidecar
from plobi.collectors.chatlog.config import CollectorConfig


def _env_with_go(tmp_path: Path, with_binary: bool = False) -> dict[str, str]:
    """造一个"Go 在"的环境：Windows 上 ``shutil.which`` 要真的可执行后缀。"""
    (tmp_path / "go.cmd").write_text("@echo off\n", encoding="utf-8")
    env = {"PATH": str(tmp_path), "GO_BINARY": "go.cmd"}
    if with_binary:
        binary = tmp_path / "chatlog.exe"
        binary.write_text("", encoding="utf-8")
        env["PLOBI_CHATLOG_BIN"] = str(binary)
    return env


@pytest.fixture
def absent(tmp_path: Path) -> Path:
    return tmp_path / "no-such-chatlog.json"


class TestNoConfigEqualsCurrentPosture:
    """判据 7 的那条硬要求：没有 ``chatlog.json`` 时，行为就等于 ADR 现行口径。"""

    def test_defaults_are_blacklist_and_enabled(self, absent: Path) -> None:
        assert absent.exists() is False
        cfg = CollectorConfig.load(absent)
        assert cfg.mode == "blacklist"
        assert cfg.enabled is True
        assert cfg.blacklist == []

    def test_empty_blacklist_reaches_an_unseen_talker(self, absent: Path) -> None:
        cfg = CollectorConfig.load(absent)
        assert cfg.allows("任何第一次见到的群") is True
        assert cfg.allows("某个个人号") is True

    def test_missing_file_is_not_reported_as_silently_disabled(self, absent: Path) -> None:
        """静默 fail-closed 就是这次要修的病根：不能既不开也不说。"""
        cfg = CollectorConfig.load(absent)
        assert cfg.enabled and cfg.mode == "blacklist"

    def test_a_first_seen_talker_is_ingested_with_no_config_file(self, absent: Path, tmp_path: Path) -> None:
        """判据 7 说的是**行为**，不止默认值：ADR-0010 修订 2026-09-30（裁定 50）把
        "未评审即拒采"推翻了，所以没有配置文件时，第一次见到的会话也要真进库。
        闸在 `pipeline.handle_message` 里，不在 `config.allows` 里——只测后者会漏。"""
        from datetime import datetime

        from plobi.agenda.service import AgendaService
        from plobi.collectors.chatlog.client import ChatMessage
        from plobi.collectors.chatlog.confirm import HeuristicConfirmer
        from plobi.collectors.chatlog.pipeline import ChatlogPipeline, IngestReport
        from plobi.collectors.chatlog.state import SeenStore, TalkerStore

        class OneMessageClient:
            def fetch(self, talker: str, day=None):
                return []

            def list_talkers(self, keyword: str = "", limit: int = 10000):
                return []

        pipeline = ChatlogPipeline(
            config=CollectorConfig.load(absent),
            client=OneMessageClient(),
            service=AgendaService(tmp_path / "agenda.db"),
            seen=SeenStore(tmp_path / "seen.db"),
            talkers=TalkerStore(tmp_path / "talkers.db"),
            confirmer=HeuristicConfirmer(now_factory=lambda: datetime(2026, 10, 10, 9, 0)),
        )

        report = IngestReport()
        pipeline.handle_message(
            ChatMessage(
                msg_id="m1",
                talker="第一次见到的组群",
                sender="导师",
                sent_at="2026-10-10 09:00:00",
                content="周五下午三点答辩",
            ),
            report,
        )

        assert report.created, "新会话默认采：一条都没进库就是闸还停在被推翻的那一版"
        assert "第一次见到的组群" in pipeline.talkers.pending()


class TestNoSilentFallback:
    def test_typoed_mode_runs_current_posture_and_is_remembered(self, tmp_path: Path) -> None:
        path = tmp_path / "chatlog.json"
        path.write_text(json.dumps({"mode": "backlist"}), encoding="utf-8")
        cfg = CollectorConfig.load(path)
        assert cfg.mode == "blacklist"
        assert cfg.mode_as_written == "backlist"

    def test_explicit_whitelist_is_still_honoured(self, tmp_path: Path) -> None:
        path = tmp_path / "chatlog.json"
        path.write_text(json.dumps({"mode": "whitelist", "talkers": ["mom"]}), encoding="utf-8")
        cfg = CollectorConfig.load(path)
        assert cfg.mode == "whitelist"
        assert cfg.allows("mom") is True
        assert cfg.allows("其他人") is False

    def test_explicit_empty_whitelist_collects_nothing(self, tmp_path: Path) -> None:
        path = tmp_path / "chatlog.json"
        path.write_text(json.dumps({"mode": "whitelist", "talkers": []}), encoding="utf-8")
        cfg = CollectorConfig.load(path)
        assert cfg.allows("任何东西") is False

    def test_roundtrip_keeps_the_corrected_value_visible(self, tmp_path: Path) -> None:
        cfg = CollectorConfig(mode="blacklist", mode_as_written="whilsten")
        path = cfg.save(tmp_path / "rt.json")
        again = CollectorConfig.load(path)
        assert again.mode_as_written == "whilsten"


class TestFourDeadEndsAreFourSentences:
    """判据 8：Go / 产物 / 密钥 / 在听 四种状态各一句，且顺序按依赖链。"""

    def test_each_state_has_its_own_sentence(self) -> None:
        notes = {sidecar.note_for(s) for s in (
            sidecar.STATE_GO_MISSING, sidecar.STATE_BUILD_MISSING,
            sidecar.STATE_KEY_MISSING, sidecar.STATE_NOT_LISTENING, sidecar.STATE_READY,
        )}
        assert len(notes) == 5

    def test_missing_toolchain_is_reported_before_the_port(self, tmp_path: Path) -> None:
        state, why = sidecar.diagnose(
            env={"PATH": "", "GO_BINARY": "definitely-not-installed-go"},
            healthy=False,
            repo_root=tmp_path,
        )
        assert state == sidecar.STATE_GO_MISSING
        assert "Go" in why

    def test_build_comes_before_keys(self, tmp_path: Path) -> None:
        state, _ = sidecar.diagnose(env=_env_with_go(tmp_path), healthy=False, repo_root=tmp_path)
        assert state == sidecar.STATE_BUILD_MISSING

    def test_keys_are_checked_for_presence_only_never_value(self, tmp_path: Path) -> None:
        secret = "SHOULD-NEVER-APPEAR-IN-ANY-OUTPUT"
        env = {**_env_with_go(tmp_path, with_binary=True),
               "CHATLOG_DATA_KEY": secret, "CHATLOG_IMG_KEY": "   "}
        state, why = sidecar.diagnose(env=env, healthy=False, repo_root=tmp_path)
        assert state == sidecar.STATE_KEY_MISSING
        assert secret not in why

    def test_everything_present_but_not_listening_is_its_own_state(self, tmp_path: Path) -> None:
        env = {**_env_with_go(tmp_path, with_binary=True),
               "CHATLOG_DATA_KEY": "set", "CHATLOG_IMG_KEY": "set"}
        state, why = sidecar.diagnose(env=env, healthy=False, repo_root=tmp_path)
        assert state == sidecar.STATE_NOT_LISTENING
        assert "5030" in why

    def test_healthy_probe_says_ready_and_is_not_a_dead_end(self, tmp_path: Path) -> None:
        assert sidecar.diagnose(env={}, healthy=True) == (sidecar.STATE_READY, sidecar.note_for(sidecar.STATE_READY))

    def test_unrecognised_state_is_not_guessed(self) -> None:
        assert sidecar.note_for("weird").startswith("没记到的采集器状态")

    def test_resolved_binary_is_the_one_answer_the_desktop_reads(self, tmp_path: Path) -> None:
        """`WP-SIDECAR-STATE-SINGLE-SOURCE` 判据 1 的服务端那一半：产物路径由这里给一次，
        桌面不许再列一份候选（`main.ts` 原来硬编了 `tools/chatlog/bin` 与 `D:\\Tools\\wechat`）。"""
        env = _env_with_go(tmp_path, with_binary=True)
        assert sidecar.resolved_binary(env=env) == str(tmp_path / "chatlog.exe")

    def test_resolved_binary_is_none_when_nothing_exists(self, tmp_path: Path) -> None:
        assert sidecar.resolved_binary(env={"PATH": str(tmp_path)}, repo_root=tmp_path) is None

    def test_health_carries_the_binary_alongside_the_state(self, monkeypatch, tmp_path: Path) -> None:
        """桌面一次 `health` 就要拿到"缺哪一步"+"可用的产物"，不必二次问。"""
        from types import SimpleNamespace

        from plobi.collectors.chatlog import pipeline as pipeline_module

        monkeypatch.setattr(pipeline_module, "chatlog_is_healthy", lambda client: False)
        env = _env_with_go(tmp_path, with_binary=True)
        monkeypatch.setattr(sidecar, "resolved_binary", lambda **kw: str(tmp_path / "chatlog.exe"))
        pipe = pipeline_module.ChatlogPipeline(
            config=CollectorConfig(), client=SimpleNamespace(), service=SimpleNamespace(),
            seen=SimpleNamespace(), talkers=SimpleNamespace(), confirmer=SimpleNamespace(),
        )
        report = pipe.health()
        assert report["binary"] == str(tmp_path / "chatlog.exe")
        assert report["ok"] is False


class TestHealthCarriesTheReason:
    def test_unhealthy_health_reports_a_state_key(self, monkeypatch, tmp_path: Path) -> None:
        from types import SimpleNamespace
        from plobi.collectors.chatlog import pipeline as pipeline_module

        monkeypatch.setattr(pipeline_module, "chatlog_is_healthy", lambda client: False)
        pipe = pipeline_module.ChatlogPipeline(
            config=CollectorConfig(), client=SimpleNamespace(), service=SimpleNamespace(),
            seen=SimpleNamespace(), talkers=SimpleNamespace(), confirmer=SimpleNamespace(),
        )
        report = pipe.health()
        assert report["ok"] is False
        assert report["state"] in {
            sidecar.STATE_GO_MISSING, sidecar.STATE_BUILD_MISSING,
            sidecar.STATE_KEY_MISSING, sidecar.STATE_NOT_LISTENING,
        }
        assert report["detail"].startswith("chatlog 采不到：")


DEAD_STATES = {
    sidecar.STATE_GO_MISSING,
    sidecar.STATE_BUILD_MISSING,
    sidecar.STATE_KEY_MISSING,
    sidecar.STATE_NOT_LISTENING,
}


class TestUnreachableIsNotZeroSessions:
    """判据 2 的负例。`collect_api.py` 曾把枚举异常吞掉、返回 200 + 空表，于是
    "你一条会话都没有"和"采集器根本到不了"在面上长得一模一样——这条钉住它们必须分得开。"""

    @pytest.fixture
    def board(self, tmp_path):
        import contextlib
        from datetime import datetime
        from types import SimpleNamespace

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from plobi.agenda.service import AgendaService
        from plobi.collectors.chatlog import collect_api
        from plobi.collectors.chatlog import webhook as webhook_module
        from plobi.collectors.chatlog.confirm import HeuristicConfirmer
        from plobi.collectors.chatlog.pipeline import ChatlogPipeline
        from plobi.collectors.chatlog.state import SeenStore, TalkerStore

        @contextlib.contextmanager
        def build(*, explode: bool, sessions: list[str] | None = None):
            class StubClient:
                def list_talker_sessions(self, keyword: str = "", limit: int = 10000):
                    if explode:
                        raise RuntimeError("connection refused on :5030")
                    return [
                        SimpleNamespace(id=name, name=name) for name in (sessions or [])
                    ]

                def list_talkers(self, keyword: str = "", limit: int = 10000):
                    return list(sessions or [])

                def fetch(self, talker, day=None):
                    return []

                def healthy(self):
                    return not explode

            pipeline = ChatlogPipeline(
                config=CollectorConfig(),
                client=StubClient(),
                service=AgendaService(tmp_path / "agenda.db"),
                seen=SeenStore(tmp_path / "seen.db"),
                talkers=TalkerStore(tmp_path / "talkers.db"),
                confirmer=HeuristicConfirmer(
                    now_factory=lambda: datetime(2026, 10, 10, 9, 0)
                ),
            )
            webhook_module.set_pipeline(pipeline)
            app = FastAPI()
            app.include_router(collect_api.router, prefix="/api/collect")
            try:
                with TestClient(app) as client:
                    yield client
            finally:
                webhook_module.set_pipeline(None)

        return build

    def test_unreachable_sidecar_is_not_an_empty_list_with_http_200(self, board) -> None:
        with board(explode=True) as client:
            response = client.get("/api/collect/talkers")

        assert response.status_code == 503, "到不了要说「到不了」，不是 200 装正常"
        body = response.json()
        assert body["ok"] is False
        assert body["state"] in DEAD_STATES
        assert body["error"], "那句「为什么到不了」必须跟着一起回来"
        assert body["talkers"] == []

    def test_reachable_but_genuinely_zero_sessions_is_still_distinct(self, board) -> None:
        with board(explode=False, sessions=[]) as client:
            response = client.get("/api/collect/talkers")

        assert response.status_code == 200
        body = response.json()
        assert body["talkers"] == []
        # 同样是空表，这里靠 collector 那一位说清"采集器活着、真的没有会话"。
        collector = body["collector"]
        assert collector["ok"] is True
        assert collector["state"] == sidecar.STATE_READY
        assert collector["enumerated"] == 0

    def test_the_two_cases_are_not_the_same_shape(self, board) -> None:
        with board(explode=True) as client:
            dead = client.get("/api/collect/talkers").json()
        with board(explode=False, sessions=["班级群"]) as client:
            alive = client.get("/api/collect/talkers").json()

        assert dead["state"] != alive["collector"]["state"]
        assert dead["ok"] is False and alive["collector"]["ok"] is True
