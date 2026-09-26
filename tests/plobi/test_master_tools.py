"""B1: L1 Master 窄接口 — plobi_master_dispatch/status/preview/approve.

四个薄工具包装 plobi kanban（kanban_db.dispatch_once / promote_task /
specify_triage_task / board_stats），service-gated 注册：worker 上下文
（PLOBI_KANBAN_TASK）不可见，profile 需启用 ``plobi_north_star`` toolset。
L1 只看到这四个窄工具，不暴露原始 ``kanban_*`` 生命周期工具。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from plobi_cli import kanban_db as kb


def _load_master_tools():
    """插件目录带连字符（plobi-north-star），不能按包名导入，按文件路径加载。

    仅加载 master_tools.py 本身（模块级只依赖标准库与 tools.registry），
    不加载插件 ``__init__.py``，避免 PluginContext 注册副作用。
    """
    path = (
        Path(__file__).resolve().parents[2]
        / "plugins" / "plobi-north-star" / "master_tools.py"
    )
    spec = importlib.util.spec_from_file_location("plobi_master_tools", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MT = _load_master_tools()


@pytest.fixture()
def board(monkeypatch, tmp_path):
    """Per-test kanban board under the redirected PLOBI_HOME.

    ``dispatch_once`` 校验 assignee 是否为真实 profile（测试环境无
    ``~/.plobi/profiles/*``），monkeypatch 视为全部存在，便于观察
    ``spawned`` 候选。

    ``run_tests.sh`` 用 ``env -i`` 起子进程（无 USERPROFILE），``Path.home()``
    会炸；沿用 tests/tools/test_kanban_tools.py 的做法 stub 掉。
    """
    from pathlib import Path as _Path

    monkeypatch.setattr(_Path, "home", lambda: tmp_path)
    kb.init_db()
    conn = kb.connect()
    monkeypatch.setattr("plobi_cli.profiles.profile_exists", lambda name: True)
    yield conn
    conn.close()


@pytest.fixture()
def master_mode(monkeypatch):
    """模拟 L1 profile：toolsets 启用 plobi_north_star。"""
    monkeypatch.setattr(
        "plobi_cli.config.load_config",
        lambda: {"toolsets": ["plobi_north_star"]},
    )


def _call(handler, **args):
    return json.loads(handler(args))


# ---------------------------------------------------------------------------
# service gate
# ---------------------------------------------------------------------------


def test_gate_off_without_toolset(monkeypatch):
    """默认 profile 未打开 plobi_north_star 时看不到工具。"""
    monkeypatch.setattr(
        "plobi_cli.config.load_config",
        lambda: {"toolsets": ["plobi-cli"]},
    )
    assert MT.check_plobi_master_mode() is False


def test_gate_on_with_toolset(master_mode):
    assert MT.check_plobi_master_mode() is True


def test_gate_on_default_profile_without_master(monkeypatch):
    """裁定 6：无 master 时，当前 default 打开 toolset 即可看见 secretary 工具。"""
    monkeypatch.setattr(
        "plobi_cli.config.load_config",
        lambda: {
            "toolsets": ["plobi-cli", "plobi_north_star"],
            "platform_toolsets": {
                "cli": ["plobi-cli", "plobi_north_star"],
                "gateway": ["plobi-cli", "plobi_north_star"],
            },
        },
    )
    assert MT.check_plobi_master_mode() is True


def test_gate_on_platform_toolsets_only(monkeypatch):
    monkeypatch.setattr(
        "plobi_cli.config.load_config",
        lambda: {
            "toolsets": ["plobi-cli"],
            "platform_toolsets": {"gateway": ["plobi_north_star"]},
        },
    )
    assert MT.check_plobi_master_mode() is True


def test_ensure_north_star_toolset_writes_default_config(monkeypatch):
    from plobi.agents.registry import L1_MID_TOOLSETS, ensure_north_star_toolset

    stored = {"toolsets": ["plobi-cli"]}
    saved: dict = {}
    monkeypatch.setattr("plobi_cli.config.load_config", lambda: stored)

    def fake_save(cfg, **_kwargs):
        saved.clear()
        saved.update(cfg)

    monkeypatch.setattr("plobi_cli.config.save_config", fake_save)
    monkeypatch.setattr(
        "plobi.agents.registry.ensure_l1_secretary_routing_soul",
        lambda **_kwargs: False,
    )
    ensure_north_star_toolset()
    assert saved["toolsets"] == list(L1_MID_TOOLSETS)
    assert "plobi-cli" not in saved["toolsets"]
    assert "terminal" not in saved["platform_toolsets"]["cli"]
    assert "session_search" not in saved["platform_toolsets"]["gateway"]
    assert "plobi_north_star" in saved["platform_toolsets"]["cli"]
    assert "clarify" in saved["platform_toolsets"]["gateway"]
    assert "plobi-north-star" in saved["plugins"]["enabled"]


def test_l1_mid_narrow_drops_terminal_and_session_search():
    from plobi.agents.registry import apply_l1_mid_toolsets, mid_narrow_toolset_names

    narrowed = mid_narrow_toolset_names(["plobi-cli", "spotify"])
    assert "terminal" not in narrowed
    assert "session_search" not in narrowed
    assert "code_execution" not in narrowed
    assert "plobi_north_star" in narrowed
    assert "clarify" in narrowed
    assert "spotify" in narrowed

    cfg = {
        "toolsets": ["plobi-cli"],
        "platform_toolsets": {
            "cli": ["plobi-cli", "plobi_north_star"],
            "gateway": ["terminal", "session_search", "clarify"],
        },
    }
    assert apply_l1_mid_toolsets(cfg) is True
    for platform in ("cli", "gateway"):
        names = cfg["platform_toolsets"][platform]
        assert "terminal" not in names
        assert "session_search" not in names
        assert "plobi_north_star" in names
        assert "clarify" in names


def test_l1_mid_narrow_preserves_already_mid_list():
    from plobi.agents.registry import L1_MID_TOOLSETS, mid_narrow_toolset_names

    mid = list(L1_MID_TOOLSETS)
    assert mid_narrow_toolset_names(mid) == mid


def test_ensure_l2_agenda_toolsets_restores_terminal(tmp_path):
    import yaml

    from plobi.agents.registry import ensure_l2_agenda_toolsets

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "platform_toolsets": {
                    "cli": ["web", "clarify", "plobi_north_star"],
                    "gateway": ["web", "clarify", "plobi_north_star"],
                },
                "toolsets": ["web", "clarify", "plobi_north_star"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    assert ensure_l2_agenda_toolsets(tmp_path) is True
    loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert "terminal" in loaded["platform_toolsets"]["cli"]
    assert "terminal" in loaded["platform_toolsets"]["gateway"]
    assert "terminal" in loaded["toolsets"]
    assert ensure_l2_agenda_toolsets(tmp_path) is False


def test_ensure_l2_agenda_keeps_plobi_cli(tmp_path):
    import yaml

    from plobi.agents.registry import ensure_l2_agenda_toolsets

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {"platform_toolsets": {"cli": ["plobi-cli"], "gateway": ["plobi-cli"]}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    assert ensure_l2_agenda_toolsets(tmp_path) is False
    loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert loaded["platform_toolsets"]["cli"] == ["plobi-cli"]


def test_l1_secretary_soul_upsert(tmp_path):
    from plobi.agents.registry import (
        L1_SOUL_BEGIN,
        ensure_l1_secretary_routing_soul,
    )

    assert ensure_l1_secretary_routing_soul(home=tmp_path) is True
    text = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    assert L1_SOUL_BEGIN in text
    assert "PLOBI_L1" in text
    assert "plobi_secretary_ask" in text
    assert "session_search" in text
    ensure_l1_secretary_routing_soul(home=tmp_path)
    text2 = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    assert text2.count(L1_SOUL_BEGIN) == 1


def test_l1_soul_fence_avoids_html_comment_injection():
    from tools.threat_patterns import scan_for_threats
    from plobi.agents.registry import (
        L1_SOUL_BEGIN,
        L1_SOUL_BLOCK,
        L1_SOUL_END,
        L1_SOUL_LEGACY_BEGIN,
    )

    assert "<!--" not in L1_SOUL_BEGIN
    assert "secret" not in L1_SOUL_BEGIN.lower()
    assert "secret" not in L1_SOUL_END.lower()
    assert "PLOBI_L1" in L1_SOUL_BEGIN
    assert "html_comment_injection" not in scan_for_threats(L1_SOUL_BEGIN)
    assert "html_comment_injection" not in scan_for_threats(L1_SOUL_END)
    assert "html_comment_injection" not in scan_for_threats(L1_SOUL_BLOCK)
    assert "html_comment_injection" in scan_for_threats(L1_SOUL_LEGACY_BEGIN)


def test_l1_secretary_soul_replaces_legacy_html_fence(tmp_path):
    from plobi.agents.registry import (
        L1_SOUL_BEGIN,
        L1_SOUL_LEGACY_BEGIN,
        L1_SOUL_LEGACY_END,
        ensure_l1_secretary_routing_soul,
    )

    (tmp_path / "SOUL.md").write_text(
        "identity\n\n"
        f"{L1_SOUL_LEGACY_BEGIN}\n旧块\n{L1_SOUL_LEGACY_END}\n",
        encoding="utf-8",
    )
    assert ensure_l1_secretary_routing_soul(home=tmp_path) is True
    text = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    assert L1_SOUL_BEGIN in text
    assert L1_SOUL_LEGACY_BEGIN not in text
    assert "旧块" not in text
    assert text.count(L1_SOUL_BEGIN) == 1


def test_secretary_ask_schema_steers_soft_routing():
    desc = MT.SECRETARY_ASK_SCHEMA["description"]
    assert MT.SECRETARY_ASK_SCHEMA["name"] == "plobi_secretary_ask"
    assert "session_search" in desc
    assert "terminal" in desc
    assert "agent id" in desc.lower()


def test_gate_hides_from_worker(master_mode, monkeypatch):
    """dispatcher-spawned worker 即使启用 toolset 也不可见。"""
    monkeypatch.setenv("PLOBI_KANBAN_TASK", "t_abc")
    assert MT.check_plobi_master_mode() is False


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_reports_board_counts(board):
    kb.create_task(board, title="待批任务", triage=True)
    kb.create_task(board, title="可派工", assignee="l2_agenda")

    out = _call(MT.handle_master_status)
    assert out["ok"] is True
    assert out["by_status"].get("triage") == 1
    assert out["by_status"].get("ready") == 1
    assert [t["title"] for t in out["awaiting_human"]] == ["待批任务"]
    assert [t["title"] for t in out["ready"]] == ["可派工"]


# ---------------------------------------------------------------------------
# preview（dry-run，不落盘）
# ---------------------------------------------------------------------------


def test_preview_is_dry_run(board):
    tid = kb.create_task(board, title="派工预览", assignee="l2_agenda")

    out = _call(MT.handle_master_preview)
    assert out["ok"] is True
    assert out["dry_run"] is True
    assert (tid, "l2_agenda", "") in [tuple(s) for s in out["spawned"]]
    # 任务仍是 ready，未被 claim
    assert kb.get_task(board, tid).status == "ready"


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------


def test_dispatch_dry_run_matches_preview(board):
    tid = kb.create_task(board, title="实派", assignee="l2_agenda")

    out = _call(MT.handle_master_dispatch, dry_run=True)
    assert out["ok"] is True
    assert out["dry_run"] is True
    assert (tid, "l2_agenda", "") in [tuple(s) for s in out["spawned"]]
    assert kb.get_task(board, tid).status == "ready"


# ---------------------------------------------------------------------------
# approve
# ---------------------------------------------------------------------------


def test_approve_promotes_triage_to_ready(board):
    tid = kb.create_task(board, title="批准链", triage=True)

    out = _call(MT.handle_master_approve, task_id=tid, reason="L1 批准")
    assert out["ok"] is True
    assert out["status_before"] == "triage"
    assert out["status_after"] == "ready"
    assert kb.get_task(board, tid).status == "ready"


def test_approve_promotes_blocked_to_ready(board):
    tid = kb.create_task(board, title="解阻塞", initial_status="blocked")

    out = _call(MT.handle_master_approve, task_id=tid)
    assert out["ok"] is True
    assert out["status_after"] == "ready"


def test_approve_refuses_running(board):
    tid = kb.create_task(board, title="运行中")
    # create_task 无父任务一律落 ready；running 是 dispatcher claim 后的
    # 状态，直接写库构造。
    with kb.write_txn(board):
        board.execute("UPDATE tasks SET status = 'running' WHERE id = ?", (tid,))

    out = _call(MT.handle_master_approve, task_id=tid)
    assert out["ok"] is False
    assert "approve only applies" in out.get("error", "")
    assert kb.get_task(board, tid).status == "running"


def test_approve_requires_task_id(board):
    out = _call(MT.handle_master_approve)
    assert out["ok"] is False
    assert "task_id" in out.get("error", "")


def test_approve_unknown_task(board):
    out = _call(MT.handle_master_approve, task_id="t_does_not_exist")
    assert out["ok"] is False
    assert "not found" in out.get("error", "")


# ---------------------------------------------------------------------------
# 假模型 e2e 闭环：派工 → 状态 → 批准
# ---------------------------------------------------------------------------


def test_e2e_fake_model_loop(board, master_mode):
    """L1 秘书按工具调用顺序走完整闭环。

    ops 采集 → 任务进 triage → status 看到 awaiting_human → preview 确认
    无可派工 → approve 批准到 ready（带 assignee）→ dispatch 派工（dry_run
    观察 spawned）→ status 确认 ready 队列。
    """
    # 1. 上游产生待批准任务（triage）
    tid = kb.create_task(board, title="整理周报", triage=True)

    # 2. status：L1 看到 awaiting_human
    s1 = _call(MT.handle_master_status)
    assert tid in [t["id"] for t in s1["awaiting_human"]]

    # 3. preview：还没有 ready 可派工
    p1 = _call(MT.handle_master_preview)
    assert p1["spawned"] == []

    # 4. approve：L1 批准，任务 triage → ready 并带 assignee
    a = _call(
        MT.handle_master_approve,
        task_id=tid,
        assignee="l2_agenda",
        reason="e2e 闭环",
    )
    assert a["ok"] is True
    assert a["status_before"] == "triage"
    assert a["status_after"] == "ready"

    # 5. dispatch：派工候选出现
    d = _call(MT.handle_master_dispatch, dry_run=True)
    assert (tid, "l2_agenda", "") in [tuple(s) for s in d["spawned"]]

    # 6. status：ready 队列确认闭环
    s2 = _call(MT.handle_master_status)
    assert tid in [t["id"] for t in s2["ready"]]
    assert s2["by_status"].get("triage", 0) == 0


# ---------------------------------------------------------------------------
# WP-BE-5: plobi_secretary_ask — agenda refresh (not kanban)
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta

from plobi.agenda.service import AgendaService
from plobi.agents.registry import (
    AgentRegistry,
    find_agenda_agent,
    run_secretary_ask,
)
from plobi.collectors.chatlog.client import ChatlogUnavailable
from plobi.collectors.chatlog.config import CollectorConfig
from plobi.collectors.chatlog.confirm import HeuristicConfirmer
from plobi.collectors.chatlog.pipeline import (
    ChatlogDead,
    ChatlogPipeline,
    refresh_agenda,
)
from plobi.collectors.chatlog.state import SeenStore, TalkerStore


class _FakeChatlog:
    """Stand-in for ChatlogClient. ``fail=True`` → unhealthy."""

    def __init__(self, *, fail: bool = False, by_talker: dict | None = None):
        self.fail = fail
        self.by_talker = by_talker or {}
        self.health_checks = 0

    def healthy(self) -> bool:
        self.health_checks += 1
        return not self.fail

    def fetch(self, talker: str, day=None):
        if self.fail:
            raise ChatlogUnavailable("boom")
        return self.by_talker.get(talker, [])

    def list_talkers(self, keyword: str = "", limit: int = 10000) -> list[str]:
        if self.fail:
            raise ChatlogUnavailable("boom")
        return list(self.by_talker)


def _tomorrow_iso(hour: int = 9) -> str:
    day = datetime.now().date() + timedelta(days=1)
    return datetime.combine(day, datetime.min.time()).replace(hour=hour).isoformat()


@pytest.fixture()
def secretary_home(tmp_path, monkeypatch):
    """Isolate PLOBI_HOME so spawn / registry never touch the real home."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    plobi = tmp_path / ".plobi"
    plobi.mkdir()
    monkeypatch.setenv("PLOBI_HOME", str(plobi))
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(plobi / "plobi" / "projects.yaml"))
    monkeypatch.delenv("PLOBI_MODELS_CONFIG", raising=False)
    return tmp_path


@pytest.fixture()
def stub_spawn(secretary_home, monkeypatch):
    """Shrink create_profile to mkdir so tests don't clone a real profile."""
    import plobi_cli.profiles as profiles_mod

    def fake_create_profile(name, **kwargs):
        profile_dir = Path(secretary_home) / ".plobi" / "profiles" / name
        profile_dir.mkdir(parents=True, exist_ok=True)
        (profile_dir / "plobi").mkdir(exist_ok=True)
        (profile_dir / "config.yaml").write_text("model: {}\n", encoding="utf-8")
        return profile_dir

    def fake_profile_exists(name):
        if name == "default":
            return True
        return (Path(secretary_home) / ".plobi" / "profiles" / name).is_dir()

    def fake_get_profile_dir(name):
        if name == "default":
            return Path(secretary_home) / ".plobi"
        return Path(secretary_home) / ".plobi" / "profiles" / name

    monkeypatch.setattr(profiles_mod, "create_profile", fake_create_profile)
    monkeypatch.setattr(profiles_mod, "profile_exists", fake_profile_exists)
    monkeypatch.setattr(profiles_mod, "get_profile_dir", fake_get_profile_dir)
    monkeypatch.setattr("plobi_cli.profiles.create_profile", fake_create_profile)
    monkeypatch.setattr("plobi_cli.profiles.profile_exists", fake_profile_exists)
    monkeypatch.setattr("plobi_cli.profiles.get_profile_dir", fake_get_profile_dir)
    return secretary_home


def _agenda_pipeline(tmp_path, *, fail: bool):
    service = AgendaService(tmp_path / "agenda.db")
    client = _FakeChatlog(fail=fail)
    pipe = ChatlogPipeline(
        config=CollectorConfig(talkers=["班级群"], enabled=True),
        client=client,
        service=service,
        seen=SeenStore(tmp_path / "seen.db"),
        talkers=TalkerStore(tmp_path / "talkers.db"),
        confirmer=HeuristicConfirmer(),
    )
    return pipe, service, client


def test_refresh_agenda_healthy_returns_board(tmp_path):
    pipe, service, client = _agenda_pipeline(tmp_path, fail=False)
    service.create_manual(title="组会", start_at=_tomorrow_iso(14), kind="meeting")

    report, summary = refresh_agenda(pipeline=pipe)
    assert client.health_checks >= 1
    assert report.scanned == 0  # whitelist talker has no new messages
    titles = [event["title"] for event in summary["events"]]
    assert "组会" in titles
    assert summary["tomorrow"]


def test_refresh_agenda_unhealthy_raises(tmp_path):
    pipe, _service, client = _agenda_pipeline(tmp_path, fail=True)
    with pytest.raises(ChatlogDead, match="采集不通"):
        refresh_agenda(pipeline=pipe)
    assert client.health_checks >= 1


def test_secretary_ask_unhealthy_is_error_not_fake_board(stub_spawn, tmp_path):
    """Dead door: chatlog down → ok=False, no invented events."""
    pipe, _service, _client = _agenda_pipeline(tmp_path, fail=True)
    out = run_secretary_ask(
        "refresh_agenda",
        "明天的日常安排是什么",
        pipeline=pipe,
    )
    assert out["ok"] is False
    assert "采集不通" in out["error"]
    assert out.get("dead") is True
    assert "agenda" not in out
    assert "plan" not in out


def test_secretary_ask_healthy_refresh(stub_spawn, tmp_path):
    pipe, service, _client = _agenda_pipeline(tmp_path, fail=False)
    service.create_manual(title="高数课", start_at=_tomorrow_iso(8), kind="class")

    out = run_secretary_ask(
        "refresh_agenda",
        "明天的日常安排是什么",
        pipeline=pipe,
    )
    assert out["ok"] is True
    assert out["intent"] == "refresh_agenda"
    assert out["agent"]["role"] == "l2_agenda"
    titles = [event["title"] for event in out["agenda"]["events"]]
    assert "高数课" in titles


def test_secretary_ask_spawns_one_agenda_when_missing(stub_spawn, tmp_path):
    pipe, _service, _client = _agenda_pipeline(tmp_path, fail=False)
    reg = AgentRegistry.load()
    assert find_agenda_agent(reg) is None

    out = run_secretary_ask(
        "refresh_agenda",
        "明天的日常安排是什么",
        registry=reg,
        pipeline=pipe,
    )
    assert out["ok"] is True
    assert out["agent"]["spawned"] is True
    assert out["agent"]["id"] in {"agenda", "secretary-agenda"}
    # S2: still exactly one agenda L2
    reloaded = AgentRegistry.load()
    assert len([e for e in reloaded.entries() if e.role == "l2_agenda"]) == 1


def test_secretary_ask_does_not_spawn_second_agenda(stub_spawn, tmp_path):
    pipe, _service, _client = _agenda_pipeline(tmp_path, fail=False)
    reg = AgentRegistry.load()
    first = run_secretary_ask(
        "refresh_agenda", "明天安排", registry=reg, pipeline=pipe
    )
    second = run_secretary_ask(
        "refresh_agenda", "明天安排", registry=reg, pipeline=pipe
    )
    assert first["ok"] and second["ok"]
    assert second["agent"]["spawned"] is False
    assert first["agent"]["id"] == second["agent"]["id"]


def test_secretary_ask_rejects_bad_intent(stub_spawn):
    out = run_secretary_ask("kanban_dispatch", "把任务派出去")
    assert out["ok"] is False
    assert "intent" in out["error"]


def test_handle_secretary_ask_unhealthy_json(stub_spawn, tmp_path, monkeypatch):
    """Tool JSON: unhealthy chatlog is an error envelope, not a fake board."""
    pipe, _service, _client = _agenda_pipeline(tmp_path, fail=True)
    real = run_secretary_ask

    def _with_pipe(intent, user_text, **kwargs):
        kwargs["pipeline"] = pipe
        return real(intent, user_text, **kwargs)

    monkeypatch.setattr("plobi.agents.registry.run_secretary_ask", _with_pipe)
    out = _call(
        MT.handle_secretary_ask,
        intent="refresh_agenda",
        user_text="明天的日常安排是什么",
    )
    assert out["ok"] is False
    assert "采集不通" in out.get("error", "")
    assert "agenda" not in out


def test_handle_secretary_ask_rejects_bad_intent_json():
    out = _call(MT.handle_secretary_ask, intent="kanban", user_text="明天安排")
    assert out["ok"] is False
    assert "intent" in out.get("error", "")


# ---------------------------------------------------------------------------
# WP-BE-6: N3 — user original + L1 briefing in the L2 session store
# ---------------------------------------------------------------------------


def test_n3_writes_user_text_and_briefing(stub_spawn, tmp_path):
    pipe, _service, _client = _agenda_pipeline(tmp_path, fail=False)
    user_text = "明天的日常安排是什么"
    out = run_secretary_ask("refresh_agenda", user_text, pipeline=pipe)
    assert out["ok"] is True
    sid = out["sessionId"]
    assert sid

    from plobi_cli.profiles import get_profile_dir
    from plobi_state import SessionDB

    db = SessionDB(db_path=get_profile_dir(out["agent"]["profile"]) / "state.db")
    msgs = db.get_messages(sid)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == user_text
    brief = msgs[1]["content"]
    assert "【L1 派工简报】" in brief
    assert "refresh_agenda" in brief
    assert "刷新" in brief
    # Must not dump an L1 transcript
    assert "transcript" not in brief.lower()
    assert user_text not in brief


def test_n3_not_written_when_chatlog_dead(stub_spawn, tmp_path):
    pipe, _service, _client = _agenda_pipeline(tmp_path, fail=True)
    out = run_secretary_ask(
        "refresh_agenda", "明天的日常安排是什么", pipeline=pipe
    )
    assert out["ok"] is False
    assert "sessionId" not in out


def test_n3_session_is_the_profile_latest(stub_spawn, tmp_path):
    """WP-BE-3 overview.sessionId = latest session in that profile's state.db."""
    pipe, _service, _client = _agenda_pipeline(tmp_path, fail=False)
    out = run_secretary_ask("refresh_agenda", "明天安排", pipeline=pipe)
    assert out["ok"] is True

    from plobi_cli.profiles import get_profile_dir
    from plobi.console.router import _latest_session_for_profile

    latest = _latest_session_for_profile(out["agent"]["profile"])
    assert latest == out["sessionId"]
    assert (get_profile_dir(out["agent"]["profile"]) / "state.db").is_file()


# ---------------------------------------------------------------------------
# WP-BE-7: write_briefing → aigw workbuddy / F2 fallback
# ---------------------------------------------------------------------------


def test_write_briefing_route_workbuddy(stub_spawn, tmp_path):
    pipe, service, _client = _agenda_pipeline(tmp_path, fail=False)
    service.create_manual(title="高数课", start_at=_tomorrow_iso(8), kind="class")
    out = run_secretary_ask(
        "write_briefing",
        "根据明天的日程写一段早报",
        pipeline=pipe,
        aigw_complete=lambda prompt: "明早有高数课，出门带书。",
    )
    assert out["ok"] is True
    assert out["route"] == "workbuddy"
    assert "高数" in out["briefing"] or "早" in out["briefing"]
    assert out["model"].startswith("workbuddy/") or out["model"]


def test_write_briefing_route_fallback(stub_spawn, tmp_path):
    pipe, _service, _client = _agenda_pipeline(tmp_path, fail=False)

    def boom(_prompt):
        raise RuntimeError("aigw down")

    out = run_secretary_ask(
        "write_briefing",
        "根据明天的日程写一段早报",
        pipeline=pipe,
        aigw_complete=boom,
        fallback_complete=lambda prompt: "（便宜 API）明早组会。",
    )
    assert out["ok"] is True
    assert out["route"] == "fallback"
    assert "组会" in out["briefing"] or "便宜" in out["briefing"]


def test_ensure_aigw_provider_writes_openai_base_url(tmp_path):
    from plobi.agents.registry import ensure_aigw_provider

    profile = tmp_path / "l2-agenda"
    profile.mkdir()
    (profile / "config.yaml").write_text("model: {}\n", encoding="utf-8")
    assert ensure_aigw_provider(profile) is True
    import yaml

    cfg = yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["providers"]["aigw"]["base_url"].rstrip("/").endswith("/v1")
    assert str(cfg["providers"]["aigw"]["model"]).startswith("workbuddy/")
    assert any(
        str(row.get("base_url", "")).rstrip("/").endswith("/v1")
        for row in cfg["custom_providers"]
    )
    # idempotent
    assert ensure_aigw_provider(profile) is False


def test_pick_workbuddy_prefers_listed_id():
    from plobi.agents.registry import pick_workbuddy_model

    assert pick_workbuddy_model(["gpt-4", "workbuddy/kimi-k3"]) == "workbuddy/kimi-k3"
    assert pick_workbuddy_model([]).startswith("workbuddy/")


# ---------------------------------------------------------------------------
# WP-C-WIRE: last-night plan on plobi_secretary_ask (no new intent)
# ---------------------------------------------------------------------------


def _tomorrow_date() -> str:
    return (datetime.now().date() + timedelta(days=1)).isoformat()


def _seed_nightly_plan(service, *, status: str, summary: str, event=None, conflict_count: int = 0):
    from plobi.agenda import store

    tomorrow = _tomorrow_date()
    conn = store.connect(service.db_path)
    try:
        plan = store.upsert_daily_plan(
            conn,
            for_date=tomorrow,
            status=status,
            summary=summary,
            event_count=1 if event is not None else 0,
            conflict_count=conflict_count,
        )
        if event is not None:
            store.replace_plan_items(
                conn,
                plan.id,
                [
                    {
                        "event_id": event.id,
                        "title": event.title,
                        "start_at": event.start_at,
                        "kind": event.kind,
                        "evidence": {"kind": "event", "event_id": event.id},
                    }
                ],
            )
        return plan
    finally:
        conn.close()


def test_secretary_ask_exposes_plan_status(stub_spawn, tmp_path):
    pipe, service, _client = _agenda_pipeline(tmp_path, fail=False)
    event = service.create_manual(title="组会", start_at=_tomorrow_iso(10), kind="meeting")
    _seed_nightly_plan(
        service,
        status="pending",
        summary="明天上午组会，冲突 0",
        event=event,
    )

    out = run_secretary_ask("refresh_agenda", "明天安排", pipeline=pipe)
    assert out["ok"] is True
    assert out["plan"]["status"] == "pending"
    assert out["plan"]["summary"] == "明天上午组会，冲突 0"
    assert out["plan"]["for_date"] == _tomorrow_date()
    assert out["plan"]["event_count"] == 1
    assert out["plan"]["conflict_count"] == 0
    assert out["plan"]["stale"] is False
    assert out["plan"]["empty"] is False


def test_secretary_ask_plan_stale_when_event_ids_differ(stub_spawn, tmp_path):
    pipe, service, _client = _agenda_pipeline(tmp_path, fail=False)
    service.create_manual(title="新课", start_at=_tomorrow_iso(8), kind="class")
    _seed_nightly_plan(
        service,
        status="pending",
        summary="昨夜按旧课表写的",
    )

    out = run_secretary_ask("refresh_agenda", "明天安排", pipeline=pipe)
    assert out["ok"] is True
    assert out["plan"]["status"] == "pending"
    assert out["plan"]["stale"] is True
    assert "以看板为准" in out["plan"]["summary"]


def test_secretary_ask_missing_plan_is_honest(stub_spawn, tmp_path):
    pipe, service, _client = _agenda_pipeline(tmp_path, fail=False)
    service.create_manual(title="组会", start_at=_tomorrow_iso(10), kind="meeting")

    out = run_secretary_ask("refresh_agenda", "明天安排", pipeline=pipe)
    assert out["ok"] is True
    assert out["plan"]["status"] == "missing"
    assert out["plan"]["summary"] == "昨夜未生成计划"
    assert "组会" not in out["plan"]["summary"]
    assert out["plan"]["empty"] is False


def test_secretary_ask_dead_does_not_fill_with_plan(stub_spawn, tmp_path):
    pipe, service, _client = _agenda_pipeline(tmp_path, fail=True)
    _seed_nightly_plan(
        service,
        status="confirmed",
        summary="昨夜写过明天组会",
    )
    out = run_secretary_ask("refresh_agenda", "明天安排", pipeline=pipe)
    assert out["ok"] is False
    assert out.get("dead") is True
    assert not out.get("plan")


def test_write_briefing_uses_plan_summary(stub_spawn, tmp_path):
    pipe, service, _client = _agenda_pipeline(tmp_path, fail=False)
    event = service.create_manual(title="高数课", start_at=_tomorrow_iso(8), kind="class")
    plan_summary = "明天早八高数，记得带书"
    _seed_nightly_plan(
        service,
        status="confirmed",
        summary=plan_summary,
        event=event,
        conflict_count=1,
    )

    captured: dict = {}

    def capture(prompt):
        captured["prompt"] = prompt
        return "按昨夜计划：早八高数。"

    out = run_secretary_ask(
        "write_briefing",
        "根据明天的日程写一段早报",
        pipeline=pipe,
        aigw_complete=capture,
    )
    assert out["ok"] is True
    assert out["route"] == "workbuddy"
    assert plan_summary in captured["prompt"]
    assert "冲突数：1" in captured["prompt"]
    assert f"{event.start_at} {event.title}" not in captured["prompt"]
    assert "- " not in captured["prompt"].split("昨夜计划：", 1)[-1]


def test_write_briefing_dismissed_uses_agenda_summary(stub_spawn, tmp_path):
    pipe, service, _client = _agenda_pipeline(tmp_path, fail=False)
    event = service.create_manual(title="高数课", start_at=_tomorrow_iso(8), kind="class")
    _seed_nightly_plan(
        service,
        status="dismissed",
        summary="昨夜计划已被忽略",
        event=event,
    )

    captured: dict = {}

    def capture(prompt):
        captured["prompt"] = prompt
        return "看板：明早高数课。"

    out = run_secretary_ask(
        "write_briefing",
        "根据明天的日程写一段早报",
        pipeline=pipe,
        aigw_complete=capture,
    )
    assert out["ok"] is True
    assert out["route"] == "workbuddy"
    assert out["plan"]["status"] == "dismissed"
    assert "高数课" in captured["prompt"]
    assert "昨夜计划已被忽略" not in captured["prompt"]
