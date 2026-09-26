"""WP-DT-DIGEST：一整天清单（构建/格式）、开机补发、早报/晚报 cron 的接线。

清单是给手机抄进日历的：钟点必须是真的——没有 ``end_at`` 就标「未写结束」，
不许编 1 小时、不许编 9:00；计划没批就只列已确认事件（不把待批当成已安排）。
"""

from __future__ import annotations

import importlib.util
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from plobi.agenda import store
from plobi.agenda.service import AgendaService
from plobi.butler.report import (
    build_day_digest,
    format_day_digest,
    morning_body,
    morning_sent_for,
)
from plobi.collectors.chatlog.config import CollectorConfig
from plobi.collectors.chatlog.confirm import HeuristicConfirmer
from plobi.collectors.chatlog.pipeline import ChatlogPipeline
from plobi.collectors.chatlog.state import SeenStore
from plobi.collectors.chatlog.watchdog import Watchdog
from plobi.notify.base import NullNotifier, RecordingNotifier, SendOutcome
from plobi.quota.pool import QuotaPool

DAY = "2026-09-12"  # 周六
MORNING = datetime(2026, 9, 12, 8, 10)
SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "plobi"


@pytest.fixture()
def svc(tmp_path):
    return AgendaService(tmp_path / "agenda.db")


@pytest.fixture(autouse=True)
def _reset_default_service():
    """默认 AgendaService 会缓存到模块全局——每个用例后清掉，避免跨 tmp 库串味。"""
    yield
    import plobi.agenda.service as agenda_service

    agenda_service._DEFAULT = None


def _rows(text: str) -> list[str]:
    return [line for line in text.splitlines()]


def _plan_with_items(svc: AgendaService, day: str, items: list[dict], *, confirmed: bool):
    """直接落一条带 items 的计划（pending → 可选 confirm）。"""
    conn = store.connect(svc.db_path)
    try:
        plan = store.upsert_daily_plan(
            conn,
            for_date=day,
            status="pending",
            summary="测试计划",
            conflict_count=0,
            event_count=len(items),
            confirm_seq=store.next_confirm_seq(conn),
            confirm_seq_at=store.now_iso(),
            evidence={"kind": "evening_plan"},
        )
        store.replace_plan_items(conn, plan.id, items)
    finally:
        conn.close()
    if confirmed:
        svc.confirm_plan(plan.id)
    return plan


def _routine_item(day: str, title: str, start: str, end: str) -> dict:
    return {
        "event_id": f"routine:{title}",
        "title": title,
        "start_at": f"{day}T{start}:00",
        "end_at": f"{day}T{end}:00",
        "kind": "task",
        "evidence": {"kind": "routine", "template_id": title},
    }


def _project_block(day: str, name: str, start: str, end: str) -> dict:
    return {
        "event_id": f"project:{name}",
        "title": f"推进 · {name}",
        "start_at": f"{day}T{start}:00",
        "end_at": f"{day}T{end}:00",
        "kind": "task",
        "evidence": {"kind": "project_block", "project_id": name, "pace": "weekly_hours:7"},
    }


# ── 格式 ─────────────────────────────────────────────────────────────────────


def test_digest_prints_start_and_end(svc):
    svc.create_manual(title="高数课", start_at=f"{DAY}T08:00:00", end_at=f"{DAY}T09:40:00", kind="class")
    text = format_day_digest(build_day_digest(svc, for_date=DAY, now=datetime(2026, 9, 12, 7, 30)))
    assert text.splitlines() == [
        "[Plobi] 今天 09-12（周六）",
        "08:00–09:40  高数课 · 课",
        "—",
        "待确认 0 条（桌面看板处理）",
    ]


def test_missing_end_never_invents_an_end_time(svc):
    svc.create_manual(title="组会", start_at=f"{DAY}T10:00:00", kind="meeting")
    text = format_day_digest(build_day_digest(svc, for_date=DAY, now=MORNING))
    assert "10:00        组会 · 会议 · 未写结束" in text
    assert "11:00" not in text  # 不编 1 小时
    assert "09:00" not in text  # 不编默认钟点


def test_empty_day_is_still_sent(svc):
    data = build_day_digest(svc, for_date=DAY, now=MORNING)
    text = format_day_digest(data)
    assert "（今天没有已确认的安排）" in text
    assert text.splitlines()[-1] == "待确认 0 条（桌面看板处理）"


def test_tomorrow_heading_and_empty_wording(svc):
    text = format_day_digest(
        build_day_digest(svc, for_date="2026-09-13", now=MORNING)
    )
    assert text.splitlines()[0] == "[Plobi] 明天 09-13（周日）"
    assert "（明天没有已确认的安排）" in text


def test_every_kind_gets_its_chinese_label(svc):
    svc.create_manual(title="高数课", start_at=f"{DAY}T08:00:00", end_at=f"{DAY}T09:40:00", kind="class")
    svc.create_manual(title="组会", start_at=f"{DAY}T10:00:00", end_at=f"{DAY}T11:00:00", kind="meeting")
    svc.create_manual(title="交作业", start_at=f"{DAY}T12:00:00", kind="ddl")
    svc.create_manual(title="跑步", start_at=f"{DAY}T18:00:00", end_at=f"{DAY}T19:00:00", kind="task")
    text = format_day_digest(build_day_digest(svc, for_date=DAY, now=MORNING))
    assert "高数课 · 课" in text
    assert "组会 · 会议" in text
    assert "交作业 · 截止" in text
    assert "跑步 · 任务" in text
    # 排序：整条按钟点排
    times = [line[:5] for line in _rows(text)[1:5]]
    assert times == ["08:00", "10:00", "12:00", "18:00"]


def test_pending_events_are_not_scheduled(svc):
    svc.ingest_candidate(title="待批的组会", start_at=f"{DAY}T16:00:00", source="wechat")
    text = format_day_digest(build_day_digest(svc, for_date=DAY, now=MORNING))
    assert "待批的组会" not in text
    assert "（今天没有已确认的安排）" in text
    assert "待确认 1 条（桌面看板处理）" in text


# ── 计划项 ───────────────────────────────────────────────────────────────────


def test_confirmed_plan_items_join_the_digest(svc):
    svc.create_manual(title="高数课", start_at=f"{DAY}T08:00:00", end_at=f"{DAY}T09:40:00", kind="class")
    _plan_with_items(
        svc,
        DAY,
        [_routine_item(DAY, "午饭", "12:00", "12:40"), _project_block(DAY, "Plobi", "14:00", "16:00")],
        confirmed=True,
    )
    text = format_day_digest(build_day_digest(svc, for_date=DAY, now=MORNING))
    assert "12:00–12:40  午饭 · 作息" in text
    assert "14:00–16:00  推进 · Plobi · 项目块" in text
    assert "08:00–09:40  高数课 · 课" in text
    assert "计划待批" not in text


def test_pending_plan_lists_events_only_and_says_so(svc):
    svc.create_manual(title="高数课", start_at=f"{DAY}T08:00:00", end_at=f"{DAY}T09:40:00", kind="class")
    _plan_with_items(svc, DAY, [_routine_item(DAY, "午饭", "12:00", "12:40")], confirmed=False)
    text = format_day_digest(build_day_digest(svc, for_date=DAY, now=MORNING))
    assert text.splitlines()[0] == "[Plobi] 今天 09-12（周六）（计划待批，以下为已确认事件）"
    assert "午饭" not in text
    assert "08:00–09:40  高数课 · 课" in text


def test_event_referenced_plan_item_appears_once(svc):
    event = svc.create_manual(title="组会", start_at=f"{DAY}T16:00:00", end_at=f"{DAY}T17:00:00", kind="meeting")
    _plan_with_items(
        svc,
        DAY,
        [
            {
                "event_id": event.id,
                "title": event.title,
                "start_at": event.start_at,
                "end_at": event.end_at,
                "kind": event.kind,
                "evidence": {"kind": "event", "event_id": event.id},
            },
            _routine_item(DAY, "午饭", "12:00", "12:40"),
        ],
        confirmed=True,
    )
    text = format_day_digest(build_day_digest(svc, for_date=DAY, now=MORNING))
    assert text.count("组会") == 1


def test_plan_item_for_a_pending_event_never_shows(svc):
    pending = svc.ingest_candidate(title="待批组会", start_at=f"{DAY}T16:00:00", source="wechat").event
    _plan_with_items(
        svc,
        DAY,
        [
            {
                "event_id": pending.id,
                "title": pending.title,
                "start_at": pending.start_at,
                "kind": pending.kind,
                "evidence": {"kind": "event", "event_id": pending.id},
            }
        ],
        confirmed=True,
    )
    text = format_day_digest(build_day_digest(svc, for_date=DAY, now=MORNING))
    assert "待批组会" not in text


# ── 早报组装 ─────────────────────────────────────────────────────────────────


def test_morning_body_puts_the_digest_first(svc):
    svc.create_manual(title="高数课", start_at="2026-09-12T08:00:00", end_at="2026-09-12T09:40:00", kind="class")
    body = morning_body(datetime(2026, 9, 12, 7, 30), service=svc, pool=QuotaPool())
    lines = body.splitlines()
    assert lines[0] == "[Plobi] 今天 09-12（周六）"
    assert "08:00–09:40  高数课 · 课" in lines
    assert "" in lines  # 清单与统计段之间空一行
    assert any(line.startswith("改动率") for line in lines)


def test_morning_body_only_hands_the_stats_to_the_polisher(svc):
    svc.create_manual(title="高数课", start_at="2026-09-12T08:00:00", end_at="2026-09-12T09:40:00", kind="class")
    seen: list[str] = []

    def transform(text: str):
        seen.append(text)
        return "统计段被改写了"

    body = morning_body(
        datetime(2026, 9, 12, 7, 30),
        service=svc,
        pool=QuotaPool(),
        transform_stats=transform,
    )
    assert seen and "08:00" not in seen[0], "清单钟点绝不进模型"
    assert "08:00–09:40  高数课 · 课" in body
    assert body.endswith("统计段被改写了")


# ── 开机补发 ─────────────────────────────────────────────────────────────────


class _FakeClient:
    def fetch(self, talker, day=None):
        return []

    def healthy(self):
        return True


class _FlakyNotifier:
    def __init__(self, ok: bool):
        self.ok = ok
        self.sent: list[str] = []

    @property
    def configured(self) -> bool:
        return True

    def send(self, text: str) -> SendOutcome:
        self.sent.append(text)
        return SendOutcome(ok=self.ok, detail="" if self.ok else "webhook down")


def _watchdog(tmp_path, svc, notifier, *, morning_catchup: bool = True, digest_state: Path | None = None):
    pipeline = ChatlogPipeline(
        config=CollectorConfig(talkers=[], enabled=False),
        client=_FakeClient(),
        service=svc,
        seen=SeenStore(tmp_path / "seen.db"),
        confirmer=HeuristicConfirmer(),
    )
    return Watchdog(
        pipeline=pipeline,
        notifier=notifier,
        state_path=tmp_path / "watchdog_state.json",
        heal_command=lambda: None,
        morning_catchup=morning_catchup,
        digest_state_path=digest_state or (tmp_path / "digest_state.json"),
    )


def test_late_boot_catchup_sends_once_and_marks(tmp_path, svc):
    notifier = RecordingNotifier()
    state = tmp_path / "digest_state.json"
    svc.create_manual(title="高数课", start_at="2026-09-12T08:00:00", end_at="2026-09-12T09:40:00", kind="class")
    watchdog = _watchdog(tmp_path, svc, notifier, digest_state=state)

    result = watchdog.tick(now=MORNING)

    assert result["morning_catchup"] == {"due": True, "sent": True, "reason": ""}
    assert len(notifier.sent) == 1
    assert notifier.sent[0].startswith("[Plobi] 今天 09-12（周六）")
    assert "08:00–09:40  高数课 · 课" in notifier.sent[0]
    assert morning_sent_for(state) == DAY

    # 同一天第二次 tick：不再发
    again = watchdog.tick(now=datetime(2026, 9, 12, 8, 20))
    assert again["morning_catchup"]["sent"] is False
    assert "already sent" in again["morning_catchup"]["reason"]
    assert len(notifier.sent) == 1


def test_catchup_stays_quiet_before_0730(tmp_path, svc):
    notifier = RecordingNotifier()
    state = tmp_path / "digest_state.json"
    watchdog = _watchdog(tmp_path, svc, notifier, digest_state=state)

    result = watchdog.tick(now=datetime(2026, 9, 12, 7, 0))

    assert result["morning_catchup"]["due"] is False
    assert result["morning_catchup"]["sent"] is False
    assert notifier.sent == []
    assert morning_sent_for(state) is None


def test_failed_catchup_leaves_the_marker_unwritten(tmp_path, svc):
    flaky = _FlakyNotifier(ok=False)
    state = tmp_path / "digest_state.json"
    watchdog = _watchdog(tmp_path, svc, flaky, digest_state=state)

    result = watchdog.tick(now=MORNING)

    assert result["morning_catchup"]["due"] is True
    assert result["morning_catchup"]["sent"] is False
    assert morning_sent_for(state) is None, "发送失败不写标记，下个 tick 再试"

    flaky.ok = True
    assert watchdog.tick(now=datetime(2026, 9, 12, 8, 20))["morning_catchup"]["sent"] is True
    assert morning_sent_for(state) == DAY


def test_catchup_needs_a_configured_notifier(tmp_path, svc):
    state = tmp_path / "digest_state.json"
    watchdog = _watchdog(tmp_path, svc, NullNotifier(), digest_state=state)

    result = watchdog.tick(now=MORNING)

    assert result["morning_catchup"]["due"] is True
    assert result["morning_catchup"]["sent"] is False
    assert "no notifier" in result["morning_catchup"]["reason"]
    assert morning_sent_for(state) is None


# ── cron 脚本接线 ────────────────────────────────────────────────────────────


def _load_script(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cron_morning_sends_the_digest_and_writes_the_marker(tmp_path, monkeypatch, svc):
    import plobi.agenda.service as agenda_service
    import plobi.notify as notify_module

    svc.create_manual(title="高数课", start_at=f"{date.today().isoformat()}T08:00:00", end_at=f"{date.today().isoformat()}T09:40:00", kind="class")
    monkeypatch.setattr(agenda_service, "_DEFAULT", svc)
    notifier = RecordingNotifier()
    monkeypatch.setattr(notify_module, "get_notifier", lambda: notifier)

    module = _load_script("dtdigest_cron_morning", "cron_morning.py")
    assert module.main() == 0

    body = notifier.sent[0]
    assert body.startswith("[Plobi] 今天 ")
    assert "高数课" in body
    assert body.index("高数课") < body.index("改动率"), "清单在前，统计段在后"
    assert morning_sent_for() == date.today().isoformat()

    # 同一天再跑：与看门狗的补发共用标记 → 不再发第二条
    assert module.main() == 0
    assert len(notifier.sent) == 1


def test_cron_evening_appends_tomorrows_digest(tmp_path, monkeypatch):
    import plobi.notify as notify_module

    tomorrow = date.today() + timedelta(days=1)
    default_service = AgendaService()
    default_service.create_manual(
        title="组会",
        start_at=f"{tomorrow.isoformat()}T15:00:00",
        end_at=f"{tomorrow.isoformat()}T16:00:00",
        kind="meeting",
    )

    notifier = RecordingNotifier()
    monkeypatch.setattr(notify_module, "get_notifier", lambda: notifier)

    module = _load_script("dtdigest_cron_evening", "cron_evening_plan.py")
    assert module.main() == 0

    body = notifier.sent[0]
    assert body.startswith(f"[Plobi] 明日计划 {tomorrow.isoformat()}"), "原有计划段不动"
    assert f"[Plobi] 明天 {tomorrow.strftime('%m-%d')}" in body
    assert "组会" in body
    assert "15:00–16:00  组会 · 会议" in body
    # 计划还没批 → 标题行写明，且不把待批计划项当安排
    assert "（计划待批，以下为已确认事件）" in body
