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
from dataclasses import asdict, dataclass, field
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
    # WP-PLAN-COMPLETION-ACK：人承认「这条做完了」的那一刻。真源只在事件上；
    # 夜间排程按（日, kind, 标题）覆盖同一行时不动这一格。见 `mark_event_completed`。
    completed_at: Optional[str] = None
    evidence: Optional[dict] = None
    prev_value: Optional[dict] = None
    confirm_seq: Optional[int] = None
    confirm_seq_at: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


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

    additive、不动任何已有行 ⇒ 全部 events 照旧读得出来。
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
    resolve a newer event. Events and checkin_cards share one namespace
    (C6: replies like ``确认3`` must resolve the newest issuer, so MAX spans
    both; the plan layer was retired 10-22).
    """
    today = datetime.now().date().isoformat()
    row = conn.execute(
        """
        SELECT MAX(top) AS top FROM (
            SELECT MAX(confirm_seq) AS top FROM events
             WHERE confirm_seq_at >= ?
            UNION ALL
            SELECT MAX(confirm_seq) AS top FROM checkin_cards
             WHERE confirm_seq_at >= ?
        )
        """,
        (today, today),
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
    row: Event, *, now: Optional[datetime] = None
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


# ---------------------------------------------------------------------------
# 排程落库的 identity 与两类旧记号
# ---------------------------------------------------------------------------

#: 秘书排出来的行用 ``source=manual`` 落库（信源词表只认 manual + 采集器，
#: 而这一行确实是"人这边"的日程）；真正的来源写在 evidence 的 ``created_by`` 上。
PLAN_EVENT_SOURCE = "manual"
#: 旧计划层为了「勾得动」补的影子行记号（``created_by="plan"``）。计划层已退役，
#: 不再产生新行；读侧继续用它把历史影子行从素材/看板里挡掉。
PLAN_ARTIFACT_KEY = "created_by"
PLAN_ARTIFACT_MARK = "plan"


def is_plan_artifact(event: "Event") -> bool:
    """这是**旧计划层补的影子行**，不是排程的素材，也不该出现在看板上。

    计划层 10-22 整层退役后不再产生这种行；留下的历史行继续按这一句挡掉，
    否则它们会被当成"当天已有的一条"再排一次（作业池 ``_ddl_candidates`` 也读 ``events``）。
    """
    return (event.evidence or {}).get(PLAN_ARTIFACT_KEY) == PLAN_ARTIFACT_MARK


#: 日程秘书排出来的块写进 events 时打的记号（用户 10-22：「计划整层退役，夜间直接写 events」）。
#: 与 ``PLAN_ARTIFACT_MARK`` 不同：那一档是旧计划层为了「勾得动」而补的影子行，默认**不显示**；
#: 这一档就是秘书排的日程本身，看板要显示，也只有它会由本轮重排替换。
PLANNER_ARTIFACT_MARK = "planner"


def upsert_planner_events(
    conn: sqlite3.Connection,
    day: str,
    blocks: Iterable[dict[str, Any]],
    *,
    planner: str = "l1",
    reason: str = "",
    written_by: str = "l1:secretary_mouth",
) -> list[Event]:
    """把排程结果**直接写成 events 行**（计划层退役后的唯一落库口）。

    身份 = (那天, kind, 标题) 的稳定哈希：同一晚重排落在同一行，``completed_at`` 一律不动
    （完成态是人的账，见裁 148）。这一天由秘书造、这次提案里已经没有的行会被删掉；
    只删 ``created_by="planner"`` 且没被勾完成的行，人写的和别的信源一律不碰。
    """
    wanted: set[str] = set()
    for block in blocks:
        title = str(block.get("title") or "").strip()
        if not title:
            continue
        start = normalize_dt(block.get("start_at"), field_name="start_at")
        if not start:
            continue
        end = normalize_dt(block.get("end_at"), field_name="end_at", required=False)
        kind = str(block.get("kind") or "task")
        _validate_enum(kind, KINDS, "kind")
        evidence = dict(block.get("evidence") or {})
        evidence.update({
            PLAN_ARTIFACT_KEY: PLANNER_ARTIFACT_MARK,
            "planner": planner,
            "reason": reason,
            "written_by": written_by,
            "planned_for": day,
        })
        eid = _plan_event_id(
            f"planner-event:{day}:{kind}:{title.casefold()}"
        )
        wanted.add(eid)
        existing = get_event(conn, eid)
        if existing is None:
            create_event(
                conn,
                event_id=eid,
                title=title,
                start_at=start,
                end_at=end,
                kind=kind,
                status="confirmed",
                source=PLAN_EVENT_SOURCE,
                evidence=evidence,
            )
        else:
            update_event(
                conn,
                eid,
                title=title,
                start_at=start,
                end_at=end,
                kind=kind,
                evidence=evidence,
            )

    stale = conn.execute(
        """
        SELECT id FROM events
         WHERE start_at >= ? AND start_at <= ?
           AND COALESCE(json_extract(evidence, '$.created_by'), '') = ?
           AND COALESCE(completed_at, '') = ''
        """,
        (f"{day}T00:00:00", f"{day}T23:59:59", PLANNER_ARTIFACT_MARK),
    ).fetchall()
    for row in stale:
        if row["id"] not in wanted:
            delete_event(conn, row["id"])

    return [event for event in (get_event(conn, eid) for eid in wanted) if event is not None]


def is_planner_event(event: "Event") -> bool:
    """这一行是日程秘书排出来的（不是人写的、也不是采集器抓的）。"""
    return (event.evidence or {}).get(PLAN_ARTIFACT_KEY) == PLANNER_ARTIFACT_MARK


def _plan_event_id(seed: str) -> str:
    """② 那一支的算法，单独留一格好测。"""
    return "evt_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


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
