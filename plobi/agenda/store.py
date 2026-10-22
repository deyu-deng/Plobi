"""SQLite persistence for agenda events.

This module is deliberately dumb: schema, CRUD, queries. No business rules,
no LLM calls, no HTTP. Business semantics (pending/confirm, prev_value diffs,
evidence assembly) live in :mod:`plobi.agenda.service`.

Runtime state lives here rather than in Mind because the board needs range
queries, diffs and evidence lookups — see docs/adr/0007-agenda-state-sqlite.md.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass, field, replace as _dc_replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional

from plobi.collectors import registry

# --- vocabulary (mirrors Docs/specs/agenda-board.md) ------------------------

KINDS = ("meeting", "ddl", "class", "task")
STATUSES = ("pending", "confirmed", "cancelled")
# 信源词表**不在这里硬编**（判据 3 / 裁定 98 二）：它由采集面注册表派生，
# 加一个平台 = `plobi/collectors/registry.py` 登记一行 + 实现 Collector，本文件一行不动。
# `manual` 是人手写的档，归本模块，不进注册表。
# 故意**不留**一个 import 时算死的 ``SOURCES`` 常量：那等于第二真源——新登记的信源进不去，
# 而它看起来还在（判据 7 那个病的另一种形态）。校验一律走下面这个函数。
def allowed_sources() -> tuple[str, ...]:
    """读**当时**的注册表。"""
    return registry.allowed_sources()


PLAN_STATUSES = ("empty", "pending", "confirmed", "dismissed")

# Human decision ledger (A5 change-rate metric, ADR-0009 byproduct).
ACTIONS = ("confirm", "dismiss", "manual_edit", "manual_delete", "complete", "uncomplete")

CONFIRM_SEQ_TTL_HOURS = 24

_INIT_LOCK = threading.Lock()
_INITIALIZED: set[str] = set()


@dataclass
class Event:
    id: str
    title: str
    start_at: str
    kind: str = "task"
    status: str = "confirmed"
    source: str = "manual"
    end_at: Optional[str] = None
    # WP-DAY-SURFACE: 手动日程可带地点与备注（课表导入时从 evidence 摊平）。
    location: Optional[str] = None
    notes: Optional[str] = None
    # WP-PLAN-COMPLETION-ACK：人承认「这条做完了」的那一刻。真源在**事件**上，不在
    # `plan_items` 上——排程每晚把那一天的行 DELETE 后重新插入（`replace_plan_items`），
    # 状态放在计划行里会被它本该拦住的那次重排抹掉。见 `mark_event_completed`。
    completed_at: Optional[str] = None
    evidence: Optional[dict] = None
    prev_value: Optional[dict] = None
    confirm_seq: Optional[int] = None
    confirm_seq_at: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DailyPlan:
    """One night's plan for a calendar day (M2). Not an event."""

    id: str
    for_date: str
    status: str = "pending"
    generated_at: str = ""
    summary: str = ""
    conflict_count: int = 0
    event_count: int = 0
    confirm_seq: Optional[int] = None
    confirm_seq_at: Optional[str] = None
    evidence: Optional[dict] = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PlanItem:
    """One row in a daily plan; must point at an event and carry evidence."""

    id: str
    plan_id: str
    event_id: str
    title: str
    start_at: str
    kind: str = "task"
    end_at: Optional[str] = None
    sort_order: int = 0
    conflict_with: tuple[str, ...] = ()
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["conflict_with"] = list(self.conflict_with)
        return data


MAX_DURATION_MIN = 24 * 60


class AgendaValidationError(ValueError):
    """Raised for caller-supplied values the store refuses to persist."""


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def normalize_dt(value: Any, *, field_name: str, required: bool = True) -> Optional[str]:
    """Accept ISO8601 (with or without offset) and return a canonical string."""
    if value is None or value == "":
        if required:
            raise AgendaValidationError(f"{field_name} is required")
        return None
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat()
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise AgendaValidationError(f"{field_name} is not ISO8601: {value!r}") from exc
    return parsed.replace(microsecond=0).isoformat()


def agenda_db_path() -> Path:
    """Resolve the agenda DB path.

    Honours ``PLOBI_AGENDA_DB`` first (tests, unusual deployments), then the
    **shared** agent root — never the per-profile copy. 裁定 42 /
    WP-PROFILE-SHARED-LAYER（R-096）：日程库是共享件，profile 活跃时也解析到
    root 那一份；此前课程分身各读到自己的空库（现核 5 份分裂），问「我明天有
    什么」得到的是空。Never hardcodes a drive letter.
    """
    override = os.environ.get("PLOBI_AGENDA_DB", "").strip()
    if override:
        return Path(override)
    try:
        from plobi_constants import get_shared_plobi_root

        root = get_shared_plobi_root() / "plobi"
    except Exception:
        root = Path.home() / ".plobi" / "plobi"
    return root / "agenda.db"


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a connection with WAL + row factory, initializing schema once."""
    path = Path(db_path) if db_path else agenda_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    key = str(path.resolve())
    if key not in _INITIALIZED:
        with _INIT_LOCK:
            if key not in _INITIALIZED:
                init_db(conn)
                _INITIALIZED.add(key)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create schema. Idempotent — safe to call on every connect."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS events (
            id             TEXT PRIMARY KEY,
            title          TEXT NOT NULL,
            start_at       TEXT NOT NULL,
            end_at         TEXT,
            kind           TEXT NOT NULL DEFAULT 'task',
            status         TEXT NOT NULL DEFAULT 'confirmed',
            source         TEXT NOT NULL DEFAULT 'manual',
            location       TEXT,
            notes          TEXT,
            evidence       TEXT,
            prev_value     TEXT,
            confirm_seq    INTEGER,
            confirm_seq_at TEXT,
            created_at     TEXT NOT NULL,
            updated_at     TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_start_at ON events(start_at);
        CREATE INDEX IF NOT EXISTS idx_events_status ON events(status);
        CREATE TABLE IF NOT EXISTS action_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ts           TEXT NOT NULL,
            event_id     TEXT NOT NULL,
            action       TEXT NOT NULL,
            event_source TEXT NOT NULL,
            detail       TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_action_log_ts ON action_log(ts);
        CREATE TABLE IF NOT EXISTS daily_plans (
            id             TEXT PRIMARY KEY,
            for_date       TEXT NOT NULL UNIQUE,
            status         TEXT NOT NULL,
            generated_at   TEXT NOT NULL,
            summary        TEXT NOT NULL DEFAULT '',
            conflict_count INTEGER NOT NULL DEFAULT 0,
            event_count    INTEGER NOT NULL DEFAULT 0,
            confirm_seq    INTEGER,
            confirm_seq_at TEXT,
            evidence       TEXT,
            created_at     TEXT NOT NULL,
            updated_at     TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS plan_items (
            id            TEXT PRIMARY KEY,
            plan_id       TEXT NOT NULL,
            event_id      TEXT NOT NULL,
            title         TEXT NOT NULL,
            start_at      TEXT NOT NULL,
            end_at        TEXT,
            kind          TEXT NOT NULL DEFAULT 'task',
            sort_order    INTEGER NOT NULL DEFAULT 0,
            conflict_with TEXT,
            evidence      TEXT NOT NULL,
            FOREIGN KEY (plan_id) REFERENCES daily_plans(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_plan_items_plan_sort
            ON plan_items(plan_id, sort_order);
        CREATE TABLE IF NOT EXISTS checkin_cards (
            id             TEXT PRIMARY KEY,
            for_date       TEXT NOT NULL,
            kind           TEXT NOT NULL DEFAULT 'checkin',
            status         TEXT NOT NULL DEFAULT 'pending',
            questions      TEXT,
            payload        TEXT,
            confirm_seq    INTEGER,
            confirm_seq_at TEXT,
            created_at     TEXT NOT NULL,
            updated_at     TEXT NOT NULL,
            UNIQUE(for_date, kind)
        );
        """
    )
    _ensure_event_completed_column(conn)
    _ensure_event_location_notes_columns(conn)
    conn.commit()


logger = logging.getLogger(__name__)


# --- row mapping ------------------------------------------------------------


def _loads(raw: Any) -> Optional[dict]:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        # Corrupted JSON in prev_value is dangerous: dismiss() treats
        # None prev_value as "delete the event entirely" rather than
        # rolling back. Surface the corruption so callers can handle it.
        logger.warning("agenda: corrupted JSON in database field: %r", raw[:200])
        return None
    return value if isinstance(value, dict) else None


def _dumps(value: Optional[dict]) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _loads_list(raw: Any) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        logger.warning("agenda: corrupted JSON list in database field: %r", str(raw)[:200])
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _dumps_list(value: Iterable[str] | None) -> Optional[str]:
    if not value:
        return None
    return json.dumps(list(value), ensure_ascii=False)


def _row_to_event(row: sqlite3.Row) -> Event:
    # WP-DAY-SURFACE: 旧库行可能没有 location/notes 列 → 默认空串（=无）。
    columns = row.keys()
    return Event(
        id=row["id"],
        title=row["title"],
        start_at=row["start_at"],
        end_at=row["end_at"],
        kind=row["kind"],
        status=row["status"],
        source=row["source"],
        location=(row["location"] if "location" in columns else "") or None or None,
        notes=(row["notes"] if "notes" in columns else "") or None,
        completed_at=((row["completed_at"] if "completed_at" in columns else "") or None),
        evidence=_loads(row["evidence"]),
        prev_value=_loads(row["prev_value"]),
        confirm_seq=row["confirm_seq"],
        confirm_seq_at=row["confirm_seq_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _validate_enum(value: str, allowed: Iterable[str], field_name: str) -> str:
    if value not in allowed:
        raise AgendaValidationError(
            f"{field_name} must be one of {sorted(allowed)}, got {value!r}"
        )
    return value


_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _validate_hhmm(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not _HHMM_RE.match(text):
        raise AgendaValidationError(
            f"{field_name} must be HH:MM (00:00-23:59), got {value!r}"
        )
    return text


# --- WP-DAY-SURFACE: events 表加 location/notes 列，旧库幂等补列 -----------


def _ensure_event_completed_column(conn: sqlite3.Connection) -> bool:
    """旧库 ALTER：events 加 `completed_at`（nullable TEXT，空 = 没承认过）。

    additive、不动任何已有行 ⇒ 现有那 91 条 `plan_items` 与全部 events 照旧读得出来。
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(events)")}
    if "completed_at" in cols:
        return False
    conn.execute("ALTER TABLE events ADD COLUMN completed_at TEXT")
    return True


def _ensure_event_location_notes_columns(conn: sqlite3.Connection) -> bool:
    """旧库 ALTER：events 加 location/notes 两列（nullable text）。

    Returns True exactly once when a column was added — that is the only
    moment we run the factory-meal enable adopt (so users who already
    disabled a row keep it disabled across re-init).
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(events)")}
    added = False
    for name in ("location", "notes"):
        if name not in cols:
            conn.execute(f"ALTER TABLE events ADD COLUMN {name} TEXT")
            added = True
    return added


# --- WP-DAY-SURFACE: 出厂作息在 init 时打开（仍是出厂钟点才碰） ---------


# --- CRUD -------------------------------------------------------------------


def create_event(
    conn: sqlite3.Connection,
    *,
    title: str,
    start_at: Any,
    end_at: Any = None,
    kind: str = "task",
    status: str = "confirmed",
    source: str = "manual",
    evidence: Optional[dict] = None,
    event_id: Optional[str] = None,
    location: Optional[str] = None,
    notes: Optional[str] = None,
    prev_value: Optional[dict] = None,
    confirm_seq: Optional[int] = None,
    confirm_seq_at: Optional[str] = None,
) -> Event:
    title = (title or "").strip()
    if not title:
        raise AgendaValidationError("title is required")
    _validate_enum(kind, KINDS, "kind")
    _validate_enum(status, STATUSES, "status")
    _validate_enum(source, allowed_sources(), "source")

    start = normalize_dt(start_at, field_name="start_at")
    end = normalize_dt(end_at, field_name="end_at", required=False)
    if end and start and end < start:
        raise AgendaValidationError("end_at must not precede start_at")

    stamp = now_iso()
    # 默认随机 id。只有"身份是从内容推出来的"那种调用方才传 ``event_id``
    # （:func:`ensure_plan_item_events`／裁 175：同一晚重排必须落在同一行上，人勾过的那一次才保得住）。
    event = Event(
        id=(event_id or "").strip() or f"evt_{uuid.uuid4().hex[:12]}",
        title=title,
        start_at=start or stamp,
        end_at=end,
        kind=kind,
        status=status,
        source=source,
        evidence=evidence,
        location=location,
        notes=notes,
        prev_value=prev_value,
        confirm_seq=confirm_seq,
        confirm_seq_at=confirm_seq_at,
        created_at=stamp,
        updated_at=stamp,
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO events (id, title, start_at, end_at, kind, status, source,
                            location, notes, evidence, prev_value,
                            confirm_seq, confirm_seq_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.id,
            event.title,
            event.start_at,
            event.end_at,
            event.kind,
            event.status,
            event.source,
            event.location or None,
            event.notes or None,
            _dumps(event.evidence),
            _dumps(event.prev_value),
            event.confirm_seq,
            event.confirm_seq_at,
            event.created_at,
            event.updated_at,
        ),
    )
    conn.commit()
    return event


def get_event(conn: sqlite3.Connection, event_id: str) -> Optional[Event]:
    row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    return _row_to_event(row) if row else None


def list_events(
    conn: sqlite3.Connection,
    *,
    start_from: Any = None,
    start_to: Any = None,
    status: Optional[str] = None,
    source: Optional[str] = None,
    include_cancelled: bool = False,
    include_completed: bool = True,
    include_plan_artifacts: bool = False,
) -> list[Event]:
    """按窗口读事件。

    ``include_plan_artifacts`` 默认 **关**：裁 175 之后 ``events`` 里会多出一类"排程结果"的行
    （每一格计划行一条，为了让那颗勾点得动）。它们本来就不是素材、也不该在白天看板里
    以"今天的一条事件"再出现一次——出现就会双份，还会把当天的作息锚点按占用挤掉
    （10-18 现核：``tests/plobi/test_day_surface.py`` 两条顶出来的就是这个形状）。
    要连它们一起读（例如按 id 校对、或对账脚本）就显式传 True。
    """
    clauses: list[str] = []
    params: list[Any] = []

    lower = normalize_dt(start_from, field_name="from", required=False)
    upper = normalize_dt(start_to, field_name="to", required=False)
    if lower:
        clauses.append("start_at >= ?")
        params.append(lower)
    if upper:
        clauses.append("start_at <= ?")
        params.append(upper)
    if status:
        _validate_enum(status, STATUSES, "status")
        clauses.append("status = ?")
        params.append(status)
    elif not include_cancelled:
        clauses.append("status != 'cancelled'")
    if not include_completed:
        # 默认 True：别的读侧一个字不变（日 surface / 看板读的是历史，不该因为做完就消失）。
        # 只有排程那一侧显式传 False——它的病根是「写完的作业第二天又被排进来」，不是「看不见」。
        clauses.append("(completed_at IS NULL OR completed_at = '')")
    if not include_plan_artifacts:
        # COALESCE 是必需的：evidence 为 NULL 的行 json_extract 出来是 NULL，
        # 直接 NOT(... = 'plan') 会连它们一起判假、把全库挡空。
        clauses.append("COALESCE(json_extract(evidence, '$.created_by'), '') <> ?")
        params.append(PLAN_ARTIFACT_MARK)
    # WP-COLLECTOR-ICS: optional source narrowing so the shared ingest path
    # can index existing rows per source without dragging the whole table.
    if source:
        _validate_enum(source, allowed_sources(), "source")
        clauses.append("source = ?")
        params.append(source)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM events {where} ORDER BY start_at ASC, created_at ASC", params
    ).fetchall()
    return [_row_to_event(r) for r in rows]


_UPDATABLE = {
    "title",
    "start_at",
    "end_at",
    "kind",
    "status",
    "source",
    "completed_at",
    "location",
    "notes",
    "evidence",
    "prev_value",
    "confirm_seq",
    "confirm_seq_at",
}


def update_event(conn: sqlite3.Connection, event_id: str, **fields: Any) -> Optional[Event]:
    unknown = set(fields) - _UPDATABLE
    if unknown:
        raise AgendaValidationError(f"unknown field(s): {sorted(unknown)}")
    if not fields:
        return get_event(conn, event_id)
    if get_event(conn, event_id) is None:
        return None

    assignments: list[str] = []
    params: list[Any] = []
    for name, value in fields.items():
        if name in {"kind", "status", "source"} and value is not None:
            allowed = {"kind": KINDS, "status": STATUSES, "source": allowed_sources()}[name]
            _validate_enum(value, allowed, name)
        if name in {"start_at", "end_at"} and value is not None:
            value = normalize_dt(value, field_name=name, required=(name == "start_at"))
        if name == "title":
            value = (value or "").strip()
            if not value:
                raise AgendaValidationError("title must not be empty")
        # WP-DAY-SURFACE: 空字符串视为未填（None）；update_manual / EventPatch 同口径。
        if name in {"location", "notes"}:
            text = (str(value).strip() if value is not None else "")
            value = text or None
        if name in {"evidence", "prev_value"}:
            value = _dumps(value)
        assignments.append(f"{name} = ?")
        params.append(value)

    assignments.append("updated_at = ?")
    params.append(now_iso())
    params.append(event_id)
    conn.execute(f"UPDATE events SET {', '.join(assignments)} WHERE id = ?", params)
    conn.commit()
    return get_event(conn, event_id)


def mark_event_completed(
    conn: sqlite3.Connection,
    event_id: str,
    *,
    completed: bool = True,
    at: Optional[str] = None,
) -> Optional[Event]:
    """承认这条做完了（或撤销承认）。**唯一真源是事件本身**，不复制进任何 profile。

    `completed=False` 把那一格清回 None——撤销不是删除，历史一行不动。
    找不到那条就返回 None，**不许静默成功**（判据四那一族）。
    """
    if get_event(conn, event_id) is None:
        return None

    return update_event(conn, event_id, completed_at=(at or now_iso()) if completed else None)


def delete_event(conn: sqlite3.Connection, event_id: str) -> bool:
    cur = conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
    conn.commit()
    return cur.rowcount > 0


# --- decision ledger (A5) ---------------------------------------------------


def log_action(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    action: str,
    event_source: str,
    detail: Optional[str] = None,
) -> None:
    """Append one human decision. Append-only; never updated or deleted."""
    _validate_enum(action, ACTIONS, "action")
    conn.execute(
        """
        INSERT INTO action_log (ts, event_id, action, event_source, detail)
        VALUES (?, ?, ?, ?, ?)
        """,
        (now_iso(), event_id, action, event_source, detail),
    )
    conn.commit()


def list_actions(
    conn: sqlite3.Connection,
    *,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> list[dict]:
    """Decisions newest-first as plain dicts; bounds are ISO timestamps."""
    clauses = []
    params: list[Any] = []
    if since:
        clauses.append("ts >= ?")
        params.append(since)
    if until:
        clauses.append("ts <= ?")
        params.append(until)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT id, ts, event_id, action, event_source, detail FROM action_log "
        f"{where} ORDER BY ts DESC, id DESC",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def list_active_avoid_windows(conn: sqlite3.Connection) -> list[dict]:
    """已确认 avoid 窗（WP-PLAN-PREFS 裁定 34.1）的合并去重列表。

    真源：``checkin_cards`` 里所有 ``status='confirmed' AND kind='config_proposal'``
    的卡，从每张 ``payload.changes.avoid_windows`` 抽取；不去重是有意的——用户
    可以对一个项目要求多次同窗，规划器把同窗合并即可。``card_id`` 留给运维追查
    与「撤销」接口用（撤销 = 重新 ``dismiss_card``，下一次 list 就少一条）。
    """
    rows = conn.execute(
        "SELECT id, payload FROM checkin_cards "
        "WHERE status = 'confirmed' AND kind = 'config_proposal'"
    ).fetchall()
    out: list[dict] = []
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        changes = (payload or {}).get("changes") or {}
        for win in changes.get("avoid_windows") or []:
            if not isinstance(win, dict):
                continue
            start = str(win.get("start_time") or "").strip()
            end = str(win.get("end_time") or "").strip()
            if not start or not end:
                continue
            out.append(
                {"start_time": start, "end_time": end, "card_id": row["id"]}
            )
    return out


# --- confirm sequence -------------------------------------------------------


def next_confirm_seq(conn: sqlite3.Connection) -> int:
    """Small per-day integer used by the DingTalk confirm protocol.

    Sequences restart daily so the numbers users type stay short; combined
    with the TTL check in :func:`confirm_seq_is_live` a stale reply cannot
    resolve a newer event. Events, daily_plans and checkin_cards share one
    namespace (DESIGN-M2 §4.1; C6 adds the third table — replies like
    ``确认3`` must resolve the newest issuer, so MAX spans all three).
    """
    today = datetime.now().date().isoformat()
    row = conn.execute(
        """
        SELECT MAX(top) AS top FROM (
            SELECT MAX(confirm_seq) AS top FROM events
             WHERE confirm_seq_at >= ?
            UNION ALL
            SELECT MAX(confirm_seq) AS top FROM daily_plans
             WHERE confirm_seq_at >= ?
            UNION ALL
            SELECT MAX(confirm_seq) AS top FROM checkin_cards
             WHERE confirm_seq_at >= ?
        )
        """,
        (today, today, today),
    ).fetchone()
    top = row["top"] if row and row["top"] is not None else 0
    return int(top) + 1


def find_by_confirm_seq(conn: sqlite3.Connection, seq: int) -> Optional[Event]:
    row = conn.execute(
        """
        SELECT * FROM events
        WHERE confirm_seq = ? AND status = 'pending'
        ORDER BY confirm_seq_at DESC LIMIT 1
        """,
        (int(seq),),
    ).fetchone()
    return _row_to_event(row) if row else None


def confirm_seq_is_live(
    row: Event | DailyPlan, *, now: Optional[datetime] = None
) -> bool:
    if not row.confirm_seq_at:
        return False
    try:
        issued = datetime.fromisoformat(row.confirm_seq_at)
    except ValueError:
        return False
    # Single clock seam (ARCH-RULING 28.9 hygiene): read "now" from this store's
    # own clock — ``now_iso()`` — the same source every other timestamp here is
    # written with. In production it is the wall clock (to the second), so this
    # is behaviour-preserving; but by going through the seam the TTL judgement
    # stays consistent with the store's clock instead of silently bypassing it
    # with a bare ``datetime.now()``.
    try:
        reference = now or datetime.fromisoformat(now_iso())
    except ValueError:
        # Defensive only: a malformed clock must never crash the store.
        reference = datetime.now()
    return reference - issued <= timedelta(hours=CONFIRM_SEQ_TTL_HOURS)


def find_plan_by_confirm_seq(conn: sqlite3.Connection, seq: int) -> Optional[DailyPlan]:
    row = conn.execute(
        """
        SELECT * FROM daily_plans
        WHERE confirm_seq = ? AND status = 'pending'
        ORDER BY confirm_seq_at DESC LIMIT 1
        """,
        (int(seq),),
    ).fetchone()
    return _row_to_plan(row) if row else None


def _row_to_plan(row: sqlite3.Row) -> DailyPlan:
    return DailyPlan(
        id=row["id"],
        for_date=row["for_date"],
        status=row["status"],
        generated_at=row["generated_at"],
        summary=row["summary"] or "",
        conflict_count=int(row["conflict_count"] or 0),
        event_count=int(row["event_count"] or 0),
        confirm_seq=row["confirm_seq"],
        confirm_seq_at=row["confirm_seq_at"],
        evidence=_loads(row["evidence"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_item(row: sqlite3.Row) -> PlanItem:
    evidence = _loads(row["evidence"]) or {}
    return PlanItem(
        id=row["id"],
        plan_id=row["plan_id"],
        event_id=row["event_id"],
        title=row["title"],
        start_at=row["start_at"],
        kind=row["kind"] or "task",
        end_at=row["end_at"],
        sort_order=int(row["sort_order"] or 0),
        conflict_with=tuple(_loads_list(row["conflict_with"])),
        evidence=evidence,
    )


def get_daily_plan(conn: sqlite3.Connection, for_date: str) -> Optional[DailyPlan]:
    row = conn.execute(
        "SELECT * FROM daily_plans WHERE for_date = ?",
        (for_date,),
    ).fetchone()
    return _row_to_plan(row) if row else None


def get_daily_plan_by_id(conn: sqlite3.Connection, plan_id: str) -> Optional[DailyPlan]:
    row = conn.execute(
        "SELECT * FROM daily_plans WHERE id = ?",
        (plan_id,),
    ).fetchone()
    return _row_to_plan(row) if row else None


def upsert_daily_plan(
    conn: sqlite3.Connection,
    *,
    for_date: str,
    status: str,
    summary: str,
    conflict_count: int = 0,
    event_count: int = 0,
    confirm_seq: Optional[int] = None,
    confirm_seq_at: Optional[str] = None,
    evidence: Optional[dict] = None,
    generated_at: Optional[str] = None,
    plan_id: Optional[str] = None,
) -> DailyPlan:
    """Insert or replace the plan header for ``for_date``. Does not touch items."""
    _validate_enum(status, PLAN_STATUSES, "status")
    day = str(for_date).strip()
    if not day:
        raise AgendaValidationError("for_date is required")
    stamp = now_iso()
    generated = generated_at or stamp
    existing = get_daily_plan(conn, day)
    pid = (plan_id or (existing.id if existing else "") or f"plan_{day}").strip()
    created_at = existing.created_at if existing else stamp
    conn.execute(
        """
        INSERT INTO daily_plans (
            id, for_date, status, generated_at, summary, conflict_count,
            event_count, confirm_seq, confirm_seq_at, evidence,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(for_date) DO UPDATE SET
            id=excluded.id,
            status=excluded.status,
            generated_at=excluded.generated_at,
            summary=excluded.summary,
            conflict_count=excluded.conflict_count,
            event_count=excluded.event_count,
            confirm_seq=excluded.confirm_seq,
            confirm_seq_at=excluded.confirm_seq_at,
            evidence=excluded.evidence,
            updated_at=excluded.updated_at
        """,
        (
            pid,
            day,
            status,
            generated,
            summary,
            int(conflict_count),
            int(event_count),
            confirm_seq,
            confirm_seq_at,
            _dumps(evidence),
            created_at,
            stamp,
        ),
    )
    conn.commit()
    stored = get_daily_plan(conn, day)
    assert stored is not None
    return stored


def set_plan_status(
    conn: sqlite3.Connection,
    plan_id: str,
    status: str,
    *,
    clear_seq: bool = False,
) -> Optional[DailyPlan]:
    _validate_enum(status, PLAN_STATUSES, "status")
    if get_daily_plan_by_id(conn, plan_id) is None:
        return None
    if clear_seq:
        conn.execute(
            """
            UPDATE daily_plans
               SET status = ?, confirm_seq = NULL, confirm_seq_at = NULL,
                   updated_at = ?
             WHERE id = ?
            """,
            (status, now_iso(), plan_id),
        )
    else:
        conn.execute(
            "UPDATE daily_plans SET status = ?, updated_at = ? WHERE id = ?",
            (status, now_iso(), plan_id),
        )
    conn.commit()
    return get_daily_plan_by_id(conn, plan_id)


def list_plan_items(conn: sqlite3.Connection, plan_id: str) -> list[PlanItem]:
    rows = conn.execute(
        """
        SELECT * FROM plan_items
         WHERE plan_id = ?
         ORDER BY sort_order ASC, start_at ASC
        """,
        (plan_id,),
    ).fetchall()
    return [_row_to_item(r) for r in rows]

# ---------------------------------------------------------------------------
# 裁 175：计划行只许引用**真存在的 events 行**
# ---------------------------------------------------------------------------

# 排程自己造的那块身份（``l1:block-3``／``project:aura``）不是事件身份：
# 界面上那颗勾点下去要写的 ``events.completed_at`` 根本没有那一行可写 ⇒ 前端只能画一个点不动的勾
# （已闭的 ``WP-PLAN-ACK-NO-FAKE-CHECK`` 就是把它改成"查无此行就说记不了"）。
# 真源还是同一句：**先落成 events 那一行，再进计划**。这一格是那条规矩的落笔处——所有写
# ``plan_items`` 的路径（秘书 planner、老的分配器、对话改计划）都从这里过。
PLAN_EVENT_SOURCE = "manual"
# 造出来的那一行身上留的两个记号：``created_by="plan"``（是谁落的）与
# ``planned_from``（从哪个计划行来的）。排程那一侧要靠第一个记号把"结果"从"素材"里分出去
# （见 :func:`is_plan_artifact`）。
PLAN_ARTIFACT_KEY = "created_by"
PLAN_ARTIFACT_MARK = "plan"


def is_plan_artifact(event: "Event") -> bool:
    """这一行是**排程的结果**，不是排程的素材。

    裁 175 让每一格计划行都必须指到一条真 ``events`` 行，于是过去那些自造身份
    （``l1:block-3``）现在都成了真行。真行会被读回素材侧，
    那就得先说清它算不算素材——**不算**：不然"昨晚给某块补了身份"会变成"今晚它被当成
    一条新作业再排一次"（作业池那条 ``_ddl_candidates`` 读 ``events``，按这一句挡）。
    """
    return (event.evidence or {}).get(PLAN_ARTIFACT_KEY) == PLAN_ARTIFACT_MARK


def _plan_event_seed(plan: DailyPlan, item: "PlanItem") -> str:
    """这一天这一块内容的**稳定身份种子**。

    按 (那天, kind, 标题) 取种子，不按 ``l1:block-N`` 那个位置号：排程明晚把同一件事挪个钟点，
    它还是"那天那条作业"，勾过就还该是勾过的；反过来标题变了就是另一件事，不许继承旧勾。
    """
    return f"plan-event:{plan.for_date}:{item.kind}:{item.title.strip().casefold()}"


_EVENT_ID_SHAPE_RE = re.compile(r"^evt_[0-9a-f]{12}$")


def _plan_event_identity(plan: DailyPlan, item: "PlanItem") -> str:
    """这一行该用哪个 ``events.id``。

    两种情况，顺序固定：
    ① 那个自造 id 里**本来就带着采集器的身份**（``ddl:evt_ac46920dc0c1`` 这种——
       排程只是给它加了个前缀）⇒ 就用那把 id 落行。事件被上游 resync 删掉时，
       计划行还认得它原来是谁，下次采集回来按 uid 也对得上（判据原文要的就是
       "``ddl:evt_ac46920dc0c1`` 在 events 里要有行"，不是"有一行别的什么"）。
    ② 其余（``l1:block-N``／``project:x``）⇒ 由「那天 + kind + 标题」
       推出一个确定性 id：同一晚重排落同一行，不同天各一行。
    """
    raw = (item.event_id or "").strip()
    suffix = raw.split(":", 1)[-1] if ":" in raw else raw
    if _EVENT_ID_SHAPE_RE.match(suffix):
        return suffix
    return "evt_" + hashlib.sha1(_plan_event_seed(plan, item).encode("utf-8")).hexdigest()[:12]


def _plan_event_id(seed: str) -> str:
    """② 那一支的算法，单独留一格好测。"""
    return "evt_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


def _find_material_event(conn: sqlite3.Connection, event_id: str) -> Optional[Event]:
    """``ddl:evt_x`` 这类"材料身份加个前缀"的行，去找**那条真事件**。

    认两种对法：id 本身（``ddl:evt_x`` 去掉前缀就是它），以及采集器存在 ``evidence.uid``
    里的那把。找到就复用它的 id——同一门课的 ddl 不该因为排程写了个前缀就变成两条事。
    """
    raw = (event_id or "").strip()
    if not raw:
        return None
    suffix = raw.split(":", 1)[-1] if ":" in raw else raw
    row = conn.execute(
        """
        SELECT * FROM events
         WHERE id IN (?, ?)
            OR json_extract(evidence, '$.uid') = ?
         ORDER BY created_at ASC
         LIMIT 1
        """,
        (raw, suffix, suffix),
    ).fetchone()
    return _row_to_event(row) if row is not None else None


def ensure_plan_item_events(
    conn: sqlite3.Connection, plan: DailyPlan, items: Iterable["PlanItem"]
) -> list["PlanItem"]:
    """把每一行的 ``event_id`` 换成一个**真在 ``events`` 里**的 id（裁 175）。

    三条分支，顺序固定：
    ① 已经指到真行 ⇒ 原样过（今天 94 行里那 20 行 ``evt_…`` 就是这一支）；
    ② 材料身份能对上真事件（``ddl:evt_x``／模板 uid）⇒ 换成那条真事件的 id；
    ③ 都不是 ⇒ **就地落一行 events**（kind/title/钟点来自这一行，``source=manual``，
       evidence 里写清是谁造的），再引用它。
    第③支是幂等的：id 由 ``_plan_event_id`` 从 (那天, kind, 标题) 推出来，同一晚重排
    拿到的是同一行，只跟新钟点走——``completed_at`` 一律不动，那是人承认过的账（裁 148）。
    """
    out: list[PlanItem] = []
    for item in items:
        eid = (item.event_id or "").strip()
        if eid and get_event(conn, eid) is not None:
            out.append(item)
            continue
        material = _find_material_event(conn, eid)
        if material is not None:
            out.append(_dc_replace(item, event_id=material.id))
            continue
        wanted = _plan_event_identity(plan, item)
        existing = get_event(conn, wanted)
        if existing is not None:
            update_event(
                conn,
                wanted,
                title=item.title,
                start_at=item.start_at,
                end_at=item.end_at,
                kind=item.kind,
                evidence={**(existing.evidence or {}), **_plan_event_evidence(item, plan)},
            )
            out.append(_dc_replace(item, event_id=wanted))
            continue
        create_event(
            conn,
            event_id=wanted,
            title=item.title,
            start_at=item.start_at,
            end_at=item.end_at,
            kind=item.kind,
            status="confirmed" if plan.status == "confirmed" else "pending",
            source=PLAN_EVENT_SOURCE,
            evidence=_plan_event_evidence(item, plan),
        )
        # INSERT OR IGNORE：撞上同一把确定性 id（同一晚重排、或别的进程刚落过）就取回既有那一行，
        # 不新增、也不让一次重排把整批写库炸掉。
        landed = get_event(conn, wanted) or existing
        out.append(_dc_replace(item, event_id=landed.id))
    return out


# 计划行的 evidence 里那些"材料形状"的键（``kind`` / ``source`` / 材料侧附加格）
# **不抄进事件**。只带采集器身份 ``uid`` 那一把——它是"这条作业是谁"的凭证，明天采集回来还要按它对齐。
# 其余一律留在计划行上，免得这一行看起来像"素材侧真有这条事"。
_PLAN_EVENT_MATERIAL_KEYS = ("uid",)


def _plan_event_evidence(item: "PlanItem", plan: DailyPlan) -> dict:
    """造出来的那一行要说得出自己是谁造的、从哪个计划行来的（不许留匿名行）。"""
    source_evidence = item.evidence or {}
    evidence = {
        PLAN_ARTIFACT_KEY: PLAN_ARTIFACT_MARK,
        "planned_from": item.event_id,
        "plan_id": plan.id,
        "plan_date": plan.for_date,
    }
    for key in _PLAN_EVENT_MATERIAL_KEYS:
        if source_evidence.get(key):
            evidence[key] = source_evidence[key]
    return evidence


def _plan_item_from_raw(raw: "PlanItem | dict", *, plan_id: str, index: int) -> PlanItem:
    """把一格计划行归一成 :class:`PlanItem`（校验口径一个字不动）。"""
    if isinstance(raw, PlanItem):
        item = raw
    else:
        evidence = raw.get("evidence") or {}
        if not isinstance(evidence, dict) or not evidence:
            raise AgendaValidationError("plan_items evidence is required")
        event_id = str(raw.get("event_id") or "").strip()
        if not event_id:
            raise AgendaValidationError("plan_items event_id is required")
        item = PlanItem(
            id=str(raw.get("id") or f"pit_{uuid.uuid4().hex[:12]}"),
            plan_id=plan_id,
            event_id=event_id,
            title=str(raw.get("title") or "").strip(),
            start_at=normalize_dt(raw.get("start_at"), field_name="start_at") or "",
            kind=str(raw.get("kind") or "task"),
            end_at=normalize_dt(raw.get("end_at"), field_name="end_at", required=False),
            sort_order=int(raw.get("sort_order") if raw.get("sort_order") is not None else index),
            conflict_with=tuple(str(x) for x in (raw.get("conflict_with") or ())),
            evidence=evidence,
        )
    if not item.title.strip():
        raise AgendaValidationError("plan_items title is required")
    if not item.evidence:
        raise AgendaValidationError("plan_items evidence is required")
    if not item.event_id.strip():
        raise AgendaValidationError("plan_items event_id is required")
    _validate_enum(item.kind, KINDS, "kind")
    return item


def replace_plan_items(
    conn: sqlite3.Connection,
    plan_id: str,
    items: Iterable[PlanItem | dict],
) -> list[PlanItem]:
    """Replace all items for a plan. Empty evidence is refused."""
    plan = get_daily_plan_by_id(conn, plan_id)
    if plan is None:
        raise AgendaValidationError(f"unknown plan_id {plan_id!r}")
    # 三步顺序是写死的：归一 → 落真事件身份（裁 175）→ 删旧插新。
    # 归一要在落身份**之前**（门要看 kind/标题/那天 算身份），落身份要在 DELETE **之前**
    # （中间留缝就等于有一瞬间库里的计划行指不到任何事，界面那一片勾在那一瞬间点不动）。
    normalized = [
        _plan_item_from_raw(raw, plan_id=plan_id, index=index)
        for index, raw in enumerate(items)
    ]
    normalized = ensure_plan_item_events(conn, plan, normalized)
    conn.execute("DELETE FROM plan_items WHERE plan_id = ?", (plan_id,))
    written: list[PlanItem] = []
    for item in normalized:
        conn.execute(
            """
            INSERT INTO plan_items (
                id, plan_id, event_id, title, start_at, end_at, kind,
                sort_order, conflict_with, evidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.id,
                plan_id,
                item.event_id,
                item.title,
                item.start_at,
                item.end_at,
                item.kind,
                int(item.sort_order),
                _dumps_list(item.conflict_with),
                json.dumps(item.evidence, ensure_ascii=False),
            ),
        )
        written.append(item)
    conn.commit()
    return list_plan_items(conn, plan_id)


# --- checkin cards (C6) -------------------------------------------------------

CARD_STATUSES = ("pending", "confirmed", "dismissed")
CHECKIN_KIND = "checkin"  # 回访卡；config_proposal 是 C6b 的配置提案卡


@dataclass
class CheckinCard:
    """One daily secretary check-in (or a C6b config proposal). ADR-0009 machine."""

    id: str
    for_date: str  # UNIQUE — 每天最多一条回访卡
    kind: str = CHECKIN_KIND
    status: str = "pending"
    questions: list = field(default_factory=list)  # [{key, text, evidence}]
    payload: dict = field(default_factory=dict)
    confirm_seq: Optional[int] = None
    confirm_seq_at: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _row_to_card(row: sqlite3.Row) -> CheckinCard:
    return CheckinCard(
        id=row["id"],
        for_date=row["for_date"],
        kind=row["kind"] or CHECKIN_KIND,
        status=row["status"] or "pending",
        questions=_loads_questions(row["questions"]),
        payload=_loads(row["payload"]) or {},
        confirm_seq=row["confirm_seq"],
        confirm_seq_at=row["confirm_seq_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _loads_questions(raw: Any) -> list:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def upsert_checkin_card(
    conn: sqlite3.Connection,
    *,
    for_date: str,
    kind: str = CHECKIN_KIND,
    questions: Optional[list] = None,
    payload: Optional[dict] = None,
    confirm_seq: Optional[int] = None,
    confirm_seq_at: Optional[str] = None,
    status: str = "pending",
) -> CheckinCard:
    """Insert or replace the card for ``for_date``. One card per day."""
    _validate_enum(status, CARD_STATUSES, "status")
    if kind not in (CHECKIN_KIND, "config_proposal"):
        raise AgendaValidationError(f"unknown card kind {kind!r}")
    day = str(for_date).strip()
    if not day:
        raise AgendaValidationError("for_date is required")
    stamp = now_iso()
    existing = get_checkin_card(conn, day, kind=kind)
    card_id = existing.id if existing else f"card_{day}_{kind}"
    conn.execute(
        """
        INSERT INTO checkin_cards (
            id, for_date, kind, status, questions, payload,
            confirm_seq, confirm_seq_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(for_date, kind) DO UPDATE SET
            status=excluded.status,
            questions=excluded.questions,
            payload=excluded.payload,
            confirm_seq=excluded.confirm_seq,
            confirm_seq_at=excluded.confirm_seq_at,
            updated_at=excluded.updated_at
        """,
        (
            card_id,
            day,
            kind,
            status,
            json.dumps(questions or [], ensure_ascii=False),
            _dumps(payload),
            confirm_seq,
            confirm_seq_at,
            existing.created_at if existing else stamp,
            stamp,
        ),
    )
    conn.commit()
    stored = get_checkin_card(conn, day, kind=kind)
    assert stored is not None
    return stored


def get_checkin_card(
    conn: sqlite3.Connection, for_date: str, *, kind: str = CHECKIN_KIND
) -> Optional[CheckinCard]:
    row = conn.execute(
        "SELECT * FROM checkin_cards WHERE for_date = ? AND kind = ?",
        (str(for_date), kind),
    ).fetchone()
    return _row_to_card(row) if row else None


def get_checkin_card_by_id(conn: sqlite3.Connection, card_id: str) -> Optional[CheckinCard]:
    row = conn.execute(
        "SELECT * FROM checkin_cards WHERE id = ?", (str(card_id),)
    ).fetchone()
    return _row_to_card(row) if row else None


def find_card_by_confirm_seq(
    conn: sqlite3.Connection, seq: int, *, kind: Optional[str] = None
) -> Optional[CheckinCard]:
    sql = "SELECT * FROM checkin_cards WHERE confirm_seq = ? AND status = 'pending'"
    params: list[Any] = [int(seq)]
    if kind is not None:
        sql += " AND kind = ?"
        params.append(kind)
    row = conn.execute(sql + " ORDER BY confirm_seq_at DESC LIMIT 1", params).fetchone()
    return _row_to_card(row) if row else None


def set_card_status(
    conn: sqlite3.Connection,
    card_id: str,
    status: str,
    *,
    clear_seq: bool = False,
) -> Optional[CheckinCard]:
    _validate_enum(status, CARD_STATUSES, "status")
    if get_checkin_card_by_id(conn, card_id) is None:
        return None
    if clear_seq:
        conn.execute(
            """
            UPDATE checkin_cards
               SET status = ?, confirm_seq = NULL, confirm_seq_at = NULL,
                   updated_at = ?
             WHERE id = ?
            """,
            (status, now_iso(), card_id),
        )
    else:
        conn.execute(
            "UPDATE checkin_cards SET status = ?, updated_at = ? WHERE id = ?",
            (status, now_iso(), card_id),
        )
    conn.commit()
    return get_checkin_card_by_id(conn, card_id)


def list_checkin_cards(
    conn: sqlite3.Connection,
    *,
    since: Optional[str] = None,
    kind: Optional[str] = None,
) -> list[CheckinCard]:
    """Cards newest-first. ``since`` bounds for_date (ISO day, inclusive)."""
    clauses, params = [], []
    if since:
        clauses.append("for_date >= ?")
        params.append(str(since))
    if kind is not None:
        clauses.append("kind = ?")
        params.append(kind)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM checkin_cards {where} ORDER BY for_date DESC", params
    ).fetchall()
    return [_row_to_card(r) for r in rows]
