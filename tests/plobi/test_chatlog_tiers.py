"""Per-talker tier grading: config roundtrip, confirm weight path, report column.

Per ``Docs/PROMPT-CHATLOG-WHITELIST-REPORT.md`` + the talker-tiers task:

* ``tiers`` defaults to ``{}``; old configs run untouched (no key written on save).
* A ``task``-tier talker's bare-clock messages enter the heuristic confirm path
  ahead of ``info`` ones (the only behaviour change), and the candidate carries
  a higher ``source_weight``.
* ``whitelist_report.py`` surfaces a per-talker ``tier`` column.
* No auto-grading: only human-declared grades take effect; invalid grades fall
  back to ``info``.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from plobi.agenda.rules import RuleHit
from plobi.agenda.service import AgendaService
from plobi.collectors.chatlog.client import ChatMessage
from plobi.collectors.chatlog.config import CollectorConfig
from plobi.collectors.chatlog.confirm import HeuristicConfirmer
from plobi.collectors.chatlog.pipeline import ChatlogPipeline, IngestReport
from plobi.collectors.chatlog.state import SeenStore, TalkerStore

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts" / "plobi"
if str(SCRIPTS_DIR) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(SCRIPTS_DIR))

import whitelist_report  # noqa: E402  (import after sys.path tweak)
from whitelist_report import TalkerStat  # noqa: E402

NOW = datetime(2026, 8, 25, 10, 0)


def _message(content: str, *, talker: str = "班级群", msg_id: str = "m1") -> ChatMessage:
    return ChatMessage(
        msg_id=msg_id,
        talker=talker,
        sender="导师",
        sent_at="2026-08-25 10:00:00",
        content=content,
    )


def _pipeline(tmp_path: Path, *, config: CollectorConfig) -> ChatlogPipeline:
    """Pipeline wired with fakes; confirmer defaults to config.tier_of."""
    return ChatlogPipeline(
        config=config,
        client=None,  # handle_message is called directly; client unused
        service=AgendaService(tmp_path / "agenda.db"),
        seen=SeenStore(tmp_path / "seen.db"),
        talkers=TalkerStore(tmp_path / "talkers.db"),
    )


# --- config roundtrip / backward-compat -------------------------------------


def test_old_config_without_tiers_key_loads_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "chatlog.json"
    path.write_text(
        '{"mode":"blacklist","talkers":[],"blacklist":[],"enabled":true}',
        encoding="utf-8",
    )
    cfg = CollectorConfig.load(path)
    assert cfg.tiers == {}
    # Saving must NOT introduce a tiers key — old configs stay byte-clean.
    cfg.save(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "tiers" not in raw


def test_tiers_roundtrip_via_save_load(tmp_path: Path) -> None:
    path = tmp_path / "chatlog.json"
    cfg = CollectorConfig(
        mode="blacklist",
        blacklist=["广告群"],
        tiers={"项目群": "task", "班级群": "info"},
        enabled=True,
    )
    cfg.save(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["tiers"] == {"项目群": "task", "班级群": "info"}

    reloaded = CollectorConfig.load(path)
    assert reloaded.tiers == {"项目群": "task", "班级群": "info"}
    assert reloaded.tier_of("项目群") == "task"
    assert reloaded.tier_of("班级群") == "info"
    # Undeclared talker defaults to info (not collected any differently).
    assert reloaded.tier_of("陌生群") == "info"


def test_tiers_load_coerces_invalid_grades_to_info(tmp_path: Path) -> None:
    path = tmp_path / "chatlog.json"
    path.write_text(
        '{"mode":"blacklist","tiers":{"A":"task","B":"tasks","C":"urgent","D":"info"},"enabled":true}',
        encoding="utf-8",
    )
    cfg = CollectorConfig.load(path)
    # Only valid grades survive; typos/inventions fall back to info (fail-closed).
    assert cfg.tiers == {"A": "task", "D": "info"}
    assert cfg.tier_of("B") == "info"
    assert cfg.tier_of("C") == "info"


# --- confirm weight path -----------------------------------------------------


def test_task_tier_bare_clock_enters_confirm_with_weight(tmp_path: Path) -> None:
    """Acceptance #1 (unit-test proof): a task talker's ambiguous message is
    confirmed and carries the higher source weight."""
    pipeline = _pipeline(
        tmp_path,
        config=CollectorConfig(talkers=["项目群"], tiers={"项目群": "task"}, enabled=True),
    )

    report = IngestReport()
    # "三点开会吧" has a clock but no day; info-tier is refused (unresolved),
    # task-tier is relaxed into today.
    pipeline.handle_message(_message("三点开会吧", talker="项目群", msg_id="t1"), report)

    assert report.unresolved == 0
    assert report.created or report.updated
    event = pipeline.service.list_pending()[0]
    assert event.evidence["tier"] == "task"
    assert event.evidence["source_weight"] == 2


def test_info_tier_bare_clock_stays_unresolved(tmp_path: Path) -> None:
    """Regression: info-tier behaviour is unchanged — ambiguous clock refused."""
    pipeline = _pipeline(tmp_path, config=CollectorConfig(talkers=["班级群"], enabled=True))
    report = IngestReport()
    pipeline.handle_message(_message("三点开会吧", talker="班级群", msg_id="i1"), report)
    assert report.unresolved == 1
    assert pipeline.service.list_pending() == []


def test_info_tier_scheduled_message_still_confirmed(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path, config=CollectorConfig(talkers=["班级群"], enabled=True))
    report = IngestReport()
    pipeline.handle_message(_message("明天下午三点开组会", msg_id="i2"), report)
    assert report.unresolved == 0
    event = pipeline.service.list_pending()[0]
    assert event.evidence["tier"] == "info"
    assert event.evidence["source_weight"] == 1


def test_confirmer_hook_respects_explicit_tier_of() -> None:
    """The confirmer honours an explicit tier_of hook (decoupled from config)."""
    confirmer = HeuristicConfirmer(
        now_factory=lambda: NOW, tier_of=lambda t: "task" if t == "X" else "info"
    )
    hit = RuleHit(category="meeting", matched_keywords=["开会"], is_change=False)
    task_cand = confirmer.confirm(_message("三点开会", talker="X"), hit)
    info_cand = confirmer.confirm(_message("三点开会", talker="Y"), hit)
    assert task_cand is not None and task_cand.weight == 2 and task_cand.tier == "task"
    assert info_cand is None  # info refuses the bare clock


# --- report column -----------------------------------------------------------


def test_report_table_contains_tier_column() -> None:
    whitelist_report._whitelist_lookup = set()
    whitelist_report._blacklist_lookup = set()

    topn = [
        TalkerStat(
            talker="项目群", msg_count=9, last_active="2026-08-25T10:00:00",
            first_active="2026-08-24T09:00:00", sample_sender="甲",
            sample_snippet="组会", tier="task",
        ),
        TalkerStat(
            talker="闲聊群", msg_count=2, last_active="2026-08-25T08:00:00",
            first_active="2026-08-25T08:00:00", sample_sender="乙",
            sample_snippet="在吗", tier="info",
        ),
    ]
    text = whitelist_report.render_table(
        true_source=Path("/tmp/plobi/chatlog.json"),
        stale=[],
        mode="blacklist",
        enabled=True,
        whitelist_size=0,
        blacklist_size=0,
        archived_size=0,
        archived_at="",
        active_total=2,
        window_hours=48,
        topn=topn,
        not_whitelisted=[],
        not_excluded=[],
        first_batch=[],
        proposal_path_str="/tmp/proposal.json",
        state={"seen_messages": 0, "known_talkers": 0, "excluded_talkers": 0, "pending_talkers": 0},
    )
    assert "tier" in text
    # The task row must show its declared grade.
    assert "task" in text
