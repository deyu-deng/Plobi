"""§5 console data API — HTTP + row assembly in ONE module (ARCH-UI-MASTER §3.5).

Mounted at ``/api`` by ``plobi_cli.web_server`` so the routes are exactly the
contract paths: ``/api/agents``, ``/api/agents/{id}/subagents``,
``/api/agents/{id}/overview``.

Every response — success or failure — is the §5 envelope
``{ ok: bool, data | error }``, because the desktop unwraps it in one place
(``ApiEnvelope`` in console/types.ts).

Nothing here invents agent machinery: the registry (B2), the subagent
tracker (B4) and the model router (ADR-0011) already own that. This module is
only the projection onto §5 shapes. Per §3.5's "no parallel service layer"
rule the assembly helpers live here instead of a separate ``service.py``.

==============  ============================================================
field           source
==============  ============================================================
``id``/``name`` ``plobi.agents.registry`` entry name (+ description)
``status``      derived from that agent's L3 children (``delegation.tracker``)
``model``       ``registry.router()`` — default routes + per-agent overrides
``todayCalls``  ``totals_for_sessions`` over attributed sessions
``sessionId``   attributed sessions (see ``sessions_for_agent``)
==============  ============================================================

Usage numbers come from ``session_model_usage`` in ``$PLOBI_HOME/state.db``,
the same table the base CLI's own ``/usage`` surfaces read. No estimation
layer is added and no figure is invented when the table has nothing to say.

Session attribution (WP-BE-3, ARCH-RULINGS 2026-09-02 裁定 1): sessions belong
to a **profile** (one ``state.db`` per profile home), and agent → profile is 1:1
(``AgentEntry.profile_name``), so an agent's ``sessionId`` = the most recently
active session in its profile's own ``state.db`` (read-only query). Chat itself
does NOT flow through here — the desktop uses the base session store + gateway
RPC (裁定 2; no ``POST /api/chat`` in MVP).

One honest limitation, documented rather than faked:

* **L1 secretary.** The registry also holds the L1 entry
  (``role: l1_secretary``). §5's ``GET /api/agents`` feeds the *left rail*,
  which §3.1 defines as the L2 list — L1 is the shell itself, not a row — so
  L1 is excluded here. Flagged in PROGRESS.md so it can be overruled.
* **POST /api/agents.** Human 配备 (裁定 18): register one ``l2_project``.
  L1 still auto-spawns only one agenda L2 (S2). A second agenda is 409 /
  the existing row — never a second spawn. Not M3 execution.
"""

from __future__ import annotations

import datetime as _dt
import logging
import ntpath
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Union

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from plobi.agents.registry import (
    AGENT_CATEGORIES,
    DEFAULT_CATEGORY,
    MAX_LIVE_EVENTS_AGENTS,
    MIND_PROJECT_CATEGORIES,
    AgentEntry,
    AgentRegistry,
    L1_SECRETARY_ROLE,
    RegistryError,
    default_project_path,
    find_agenda_agent,
    is_agenda_entry,
    load_registry,
    profile_name_blocker,
    registration_blocker,
)
from plobi.delegation.tracker import (
    STATUS_AWAITING_APPROVAL,
    STATUS_ERROR,
    STATUS_IDLE,
    STATUS_WORKING,
    get_tracker,
)
from plobi.mind.project_declarations import (
    DeclarationSyncError,
    sync_project_declarations,
)
from plobi.mind.project_tree import (
    ProjectTreeConflict,
    ProjectTreeUnwritable,
    ensure_project_tree,
)
from plobi.quota.pool import get_quota_pool
from plobi.routing import ModelRoute

logger = logging.getLogger(__name__)

router = APIRouter()


# --------------------------------------------------------------------------- #
# §5 envelope
# --------------------------------------------------------------------------- #


def _envelope(payload: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status, content=payload)


def _error(status: int, message: str) -> JSONResponse:
    return _envelope({"ok": False, "error": message}, status=status)


# --------------------------------------------------------------------------- #
# registry singleton
# --------------------------------------------------------------------------- #

_registry: Optional[AgentRegistry] = None


def get_registry() -> AgentRegistry:
    """Registry singleton — re-read from disk on every call if unset."""
    global _registry
    if _registry is None:
        _registry = load_registry()
    return _registry


def set_registry(registry: Optional[AgentRegistry]) -> None:
    """Injection seam for tests."""
    global _registry
    _registry = registry


def reload_registry() -> AgentRegistry:
    """Force a re-read (the file can change under a long-running server)."""
    global _registry
    _registry = load_registry()
    return _registry


# --------------------------------------------------------------------------- #
# per-agent derivation
# --------------------------------------------------------------------------- #

# Most severe wins: a broken child outranks one waiting on a human, which
# outranks one merely busy (§4.2).
_STATUS_PRIORITY = (STATUS_ERROR, STATUS_AWAITING_APPROVAL, STATUS_WORKING)


def is_l2(entry: AgentEntry) -> bool:
    """True for rows the left rail should render (i.e. not the L1 secretary)."""
    from plobi.agents.registry import L1_SECRETARY_ROLE

    return entry.role != L1_SECRETARY_ROLE


def _resolve(entry: AgentEntry) -> Optional[ModelRoute]:
    """Route for an entry's role, or None when the role has no route."""
    try:
        return get_registry().router().resolve(entry.role)
    except Exception:  # RoutingError for operator-invented roles
        return None


def model_for(entry: AgentEntry) -> Optional[str]:
    """Qualified model id (``provider/model``) — the shape types.ts documents.

    Returns None when the role has no configured route (not an empty string,
    so the JSON serializer omits the key and the frontend treats it as absent).
    """
    route = _resolve(entry)
    if entry.has_model_override:
        base = route or ModelRoute(role=entry.role)
        return ModelRoute(
            role=entry.role,
            provider=entry.provider or base.provider,
            model=entry.model or base.model,
        ).qualified or None
    return route.qualified if route else None


def status_for(agent_id: str) -> str:
    """Derive an L2's state from its live L3 children (B4 tracker).

    A resident agent with no children has nothing in flight, so ``idle`` is a
    fact rather than a default. This replaces the old mock "always idle" with
    something the UI can actually trust once delegation runs.
    """
    children = get_tracker().subagents(agent_id)
    states = {child.get("status") for child in children}
    for candidate in _STATUS_PRIORITY:
        if candidate in states:
            return candidate
    return STATUS_IDLE


def _latest_session_for_profile(profile: str) -> str:
    """该 profile 的 state.db 里最近活跃的 session id（ARCH-RULINGS 裁定 1）。

    Plobi 的会话隶属 **profile**（每个 profile home 一份 ``state.db``，
    ``sessions`` 表没有 profile 列——隔离是天然的）。这里按
    ``plobi_cli.profiles.get_profile_dir`` 解析 profile home 后只读查询
    （``mode=ro``，与底座跨 profile 聚合同一语义：不建库、不拿写锁、
    db 不存在诚实返回空串）。"最近活跃" = ``COALESCE(ended_at, started_at)``
    最大——进行中的会话 ``ended_at`` 为空，天然排前。
    """
    try:
        from plobi_cli.profiles import get_profile_dir

        db_path = get_profile_dir(profile) / "state.db"
    except (ImportError, KeyError) as exc:
        # ImportError: plobi_cli not installed; KeyError: unknown profile name
        logger.debug("plobi console: cannot resolve profile dir for %s: %s", profile, exc)
        return ""
    if not db_path.exists():
        return ""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
        try:
            row = conn.execute(
                "SELECT id FROM sessions "
                "ORDER BY COALESCE(ended_at, started_at) DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("plobi console: session query failed for %s: %s", profile, exc)
        return ""
    return str(row[0]) if row else ""


def sessions_for_agent(agent_id: str) -> list[str]:
    """Session ids attributed to a resident agent（裁定 1：profile 下最近活跃）。

    agent → profile 是 1:1（``AgentEntry.profile_name``），profile → session
    是 1:N，所以归属 = 「该 profile 最近的一个会话」，只读展示用途
    （overview.sessionId / todayCalls / todayTokens 的数据源）。聊天主通路
    不在这里——前端走底座 session store + gateway RPC（裁定 2）。
    """
    try:
        entry = get_registry().get(agent_id)
    except Exception:
        return []
    if entry is None:
        return []
    latest = _latest_session_for_profile(entry.profile_name)
    return [latest] if latest else []


def totals_for_agent(agent_id: str, *, db_path=None) -> "UsageTotals":
    return totals_for_sessions(sessions_for_agent(agent_id), db_path=db_path)


# --------------------------------------------------------------------------- #
# §5 shapes
# --------------------------------------------------------------------------- #


# L1 AGENTS rail labels — id → short human name. Do not surface projects.yaml
# pipeline slogans (``description``) as the button text (QA P0-2).
_AGENT_SHORT_NAMES = {
    "agenda": "日程秘书",
    "agenda-secretary": "日程秘书",
    "secretary-agenda": "日程秘书",
}


def agent_display_name(entry: AgentEntry) -> str:
    """Short label for the L1 AGENTS rail — never the pipeline description.

    WP-AGENT-DISPLAY-NAME / 裁定 37.1：先用 ``entry.display_name``（人设的中文 /
    保留大小写的人名），空时回退 ``entry.name``（小写 slug id）。已知 agenda
    id 仍享裁定 13 的固定短名「日程秘书」——优先级高于 display_name。

    ``projects.yaml`` 旧条目没有 display_name 字段——回退到 ``entry.name`` 不
    漂。description 继续存长标语，不承担显示名职责。
    """
    key = (entry.name or "").strip().lower()
    mapped = _AGENT_SHORT_NAMES.get(key)
    if mapped:
        return mapped
    display = (entry.display_name or "").strip()
    if display:
        return display
    return entry.name


def agent_row(entry: AgentEntry) -> dict:
    """``Agent`` in console/types.ts — id/name/status/model/todayCalls + profile.

    ``profile`` is the Plobi profile the desktop must switch onto before
    resuming or creating that agent's session (ARCH-RULINGS 裁定 1/3;
    registry ``profile_name`` = ``profile or name``).

    R-012: ``category`` is always present (butler default) so the left rail
    can group rows. ``project_path`` lives on the overview (S2 right rail),
    not the row — it is a per-workbench mount, not a sidebar concern.

    R-013 扩写（2026-10-06）: ``mindSubtree`` 行上必带（没挂就是空串）。左栏那对
    ``Nymo``/``nymo`` 之所以看着像两个项目，是因为真数据里只有一条挂了 Mind——把这一格
    透出去，前端才辨得出「同一个项目登记了两条」，不用替用户猜。
    """
    totals = totals_for_agent(entry.name)
    row: dict = {
        "id": entry.name,
        "name": agent_display_name(entry),
        "status": status_for(entry.name),
        "model": model_for(entry),
        "todayCalls": totals.calls,
        "profile": entry.profile_name,
        "category": entry.category,
        "mindSubtree": entry.mind_subtree,
    }
    # Omit None values so the frontend receives absent keys (not JSON null),
    # matching the `model?: string` optional contract in types.ts.
    return {k: v for k, v in row.items() if v is not None}


def list_agents(
    entries: Optional[Iterable[AgentEntry]] = None,
    *,
    include_archived: bool = False,
) -> list[dict]:
    """``GET /api/agents`` — one row per registered L2, sorted by name.

    裁定 19: archived rows are hidden by default; ``include_archived=True``
    surfaces them (a separate, explicitly-requested view).
    """
    if entries is not None:
        source = list(entries)
    else:
        source = (
            get_registry().live_entries()
            if not include_archived
            else get_registry().entries()
        )
    rows = [agent_row(entry) for entry in source if is_l2(entry)]
    rows.sort(key=lambda row: row["id"])
    return rows


def subagents_for(agent_id: str) -> list[dict]:
    """``GET /api/agents/:id/subagents`` — B4's shape, passed through as-is."""
    return get_tracker().subagents(agent_id)


def agent_overview(agent_id: str, *, db_path=None) -> Optional[dict]:
    """``GET /api/agents/:id/overview`` — the S2 status card.

    ``None`` means "no such agent"; the route turns that into a 404 envelope.

    R-013: ``projectPath`` is the folder the right-rail file browser mounts.
    Empty string = no bound folder (butler, or an unset project row).
    """
    entry = get_registry().get(agent_id)
    if entry is None or not is_l2(entry):
        return None
    totals = totals_for_agent(agent_id, db_path=db_path)
    session_ids = sessions_for_agent(agent_id)
    return {
        "agent": agent_row(entry),
        "sessionId": session_ids[0] if session_ids else "",
        "todayCostUsd": round(totals.cost_usd, 6),
        "todayTokens": totals.tokens,
        "projectPath": entry.project_path,
    }


# --------------------------------------------------------------------------- #
# today's token / cost accounting (from Plobi' own usage tables)
# --------------------------------------------------------------------------- #

USAGE_TABLE = "session_model_usage"

# Summed into the single ``todayTokens`` figure the UI expects.
_TOKEN_COLUMNS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)


@dataclass(frozen=True)
class UsageTotals:
    """One agent's usage for a single calendar day."""

    calls: int = 0
    cost_usd: float = 0.0
    tokens: int = 0

    def as_dict(self) -> dict:
        return {
            "calls": self.calls,
            "costUsd": round(self.cost_usd, 6),
            "tokens": self.tokens,
        }


def default_db_path() -> Path:
    """``$PLOBI_HOME/state.db`` — the canonical Plobi state database."""
    from plobi_state import DEFAULT_DB_PATH

    return Path(DEFAULT_DB_PATH)


def day_bounds(now: Optional[float] = None) -> tuple[float, float]:
    """Local calendar-day window ``[midnight, now)`` as unix timestamps."""
    moment = time.time() if now is None else now
    start = _dt.datetime.fromtimestamp(moment).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return start.timestamp(), moment


def totals_for_sessions(
    session_ids: Iterable[str],
    *,
    now: Optional[float] = None,
    db_path: Optional[Path] = None,
) -> UsageTotals:
    """Aggregate today's usage across ``session_ids``.

    Empty ids short-circuit to zero without touching SQLite: an agent with no
    attributed sessions has genuinely spent nothing we can see, and an empty
    ``IN ()`` is not valid SQL anyway.
    """
    ids = [str(sid) for sid in session_ids if str(sid).strip()]
    if not ids:
        return UsageTotals()

    target = Path(db_path) if db_path else default_db_path()
    if not target.exists():
        return UsageTotals()

    start, end = day_bounds(now)
    placeholders = ",".join("?" * len(ids))
    token_sum = " + ".join(f"COALESCE({column}, 0)" for column in _TOKEN_COLUMNS)
    sql = (
        f"SELECT COALESCE(SUM(COALESCE(api_call_count, 0)), 0), "
        f"       COALESCE(SUM(COALESCE(estimated_cost_usd, 0.0)), 0.0), "
        f"       COALESCE(SUM({token_sum}), 0) "
        f"FROM {USAGE_TABLE} "
        f"WHERE session_id IN ({placeholders}) AND last_seen >= ? AND last_seen < ?"
    )
    params = (*ids, start, end)

    try:
        conn = sqlite3.connect(str(target), timeout=2.0)
        try:
            row = conn.execute(sql, params).fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        # Usage is auxiliary: a locked or schema-drifted DB must not 500 the
        # console rail, and must never be papered over with a fake number.
        logger.warning("plobi console: usage query failed: %s", exc)
        return UsageTotals()

    if row is None:
        return UsageTotals()
    return UsageTotals(
        calls=int(row[0] or 0),
        cost_usd=float(row[1] or 0.0),
        tokens=int(row[2] or 0),
    )


# --------------------------------------------------------------------------- #
# quota summary (WP-BE-4)
# --------------------------------------------------------------------------- #


def today_usd(*, db_path: Optional[Path] = None) -> Optional[float]:
    """今天（本地日历日）全部 session 的花费合计 — ``todayUsd`` 数据源。

    不按 session 归属过滤：额度面是全进程的。无 ``state.db`` 或查询失败 →
    ``None``（契约的 ``number | null`` 里 null = 未知，诚实区分"没花钱"）。
    """
    target = Path(db_path) if db_path else default_db_path()
    if not target.exists():
        return None
    start, end = day_bounds()
    sql = (
        "SELECT COALESCE(SUM(COALESCE(estimated_cost_usd, 0.0)), 0.0) "
        f"FROM {USAGE_TABLE} WHERE last_seen >= ? AND last_seen < ?"
    )
    try:
        conn = sqlite3.connect(str(target), timeout=2.0)
        try:
            row = conn.execute(sql, (start, end)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("plobi console: today-usd query failed: %s", exc)
        return None
    return round(float(row[0] or 0.0), 6) if row else 0.0


def quota_summary_data() -> dict:
    """``GET /api/quota/summary`` 的 data 段（WP-BE-4 契约）。

    数据源 = 进程级 ``QuotaPool`` 的 probe 结果（``SourceStatus.as_dict()``
    原样透出，含 ``checked_at``——超集字段，前端按需取）。探针是状态推导
    而非网络请求，此调用离线且幂等。

    ``gateway`` 是最近一次目录读取的结果（同样不触发请求）：``None`` = 还没问过
    网关，``state=ok`` + 空 ``source_names`` = 问过且它一个额度源都没供。这两格
    必须能分开，否则「没接」和「坏了」在界面上长成同一个样子（R-048 那条口径）。
    """
    pool = get_quota_pool()
    statuses = pool.probe_all()
    return {
        "sources": [status.as_dict() for status in statuses],
        "gateway": pool.catalog_status(),
        "todayUsd": today_usd(),
    }


@router.get("/quota/summary", name="quota_summary")
async def quota_summary():
    """WP-BE-4 — 额度池快照：各源健康度 + 今日花费。"""
    data = await run_in_threadpool(quota_summary_data)
    return {"ok": True, "data": data}


# --------------------------------------------------------------------------- #
# routes
# --------------------------------------------------------------------------- #


def _known(agent_id: str) -> bool:
    return get_registry().get(agent_id) is not None


@router.get("/agents", name="list_agents")
async def list_agents_route(request: Request):
    """§5 GET /api/agents — L2 rows for the console left rail.

    裁定 19: ``?include_archived=1`` surfaces dissolved rows (hidden by default).
    """
    include_archived = str(request.query_params.get("include_archived") or "").strip() in (
        "1", "true", "True",
    )
    rows = await run_in_threadpool(list_agents, None, include_archived=include_archived)
    return {"ok": True, "data": rows}


# Human L2 配备 (裁定 18). L1 still may auto-spawn only one agenda L2 (S2).
_AGENT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_AGENDA_ROLE = "l2_agenda"


def create_agent(body: dict) -> Union[dict, JSONResponse]:
    """Register one human-created L2: upsert → save → spawn → agent_row.

    ``role`` defaults to ``l2_project``. ``l1_secretary`` is rejected. A second
    agenda L2 is not spawned: same id returns the existing row, a new id is 409.

    R-012: ``category`` is required (projects/butler/events/research). Legacy
    callers that omit it get a 400 — the desktop always sends one.
    R-013: ``project_path`` is optional; when absent the registry seeds it from
    the category default. The bound folder is created on disk if missing.
    R-013 扩写（2026-10-06）: a ``projects`` / ``research`` row is registered in
    Mind first — ``Vault/projects/<项目名>`` with its 三件套 — and the registry
    only gets written once that tree exists, so no row ever claims a subtree that
    is not on disk. Mind unreachable / unwritable means the create fails.
    裁定 19: ``category=events`` requires ``source_event_id`` pointing to a
    confirmed agenda event, and live events agents are capped at
    :data:`MAX_LIVE_EVENTS_AGENTS` (5).
    裁定 42 §42.2 ⑥: ``secretOverrides`` (env name → value) is the 密钥 override —
    it is injected into the 分身's process environment when it is started and is
    never written into the profile home; the values are deliberately not echoed
    back by ``agent_row``.
    R-071 / R-013 扩写: 重名在这道门就拒（409），措辞来自
    :func:`plobi.agents.registry.registration_blocker` —— 命中已有键、只差大小写的
    键、同一个项目名、同一棵 Mind 树，以及建不出家的保留 profile 名（400）。
    """
    raw_id = body.get("id") if isinstance(body, dict) else None
    if raw_id is None or not str(raw_id).strip():
        return _error(400, "id is required")
    agent_id = str(raw_id).strip()
    if not _AGENT_ID_RE.fullmatch(agent_id):
        return _error(400, "id must match [a-zA-Z0-9_-]+")

    # R-012: category required + validated.
    raw_category = body.get("category")
    if raw_category is None or not str(raw_category).strip():
        return _error(400, "category is required (projects/butler/events/research)")
    category = str(raw_category).strip().lower()
    if category not in AGENT_CATEGORIES:
        return _error(
            400,
            f"category must be one of {list(AGENT_CATEGORIES)}",
        )

    role_raw = body.get("role")
    role = str(role_raw).strip() if role_raw else "l2_project"
    if not role:
        role = "l2_project"
    if role.lower() == L1_SECRETARY_ROLE:
        return _error(400, "role l1_secretary is not allowed")

    # WP-AGENT-DISPLAY-NAME / 裁定 37.1: ``name`` 进 display_name（人设的中
    # 文 / 保留大小写）；description 继续存长标语 / 一句话意图——这两条不再混
    # 用。``name`` 为空时 display_name 也空，``agent_display_name`` 会回退
    # 到 entry.name（slug id）。不做 title-case、不截断——「iPhone 项目」就
    # 存这个。
    display = str(body.get("name") or "").strip()
    blurb = str(body.get("description") or "").strip()
    mind_subtree = str(body.get("mindSubtree") or "").strip()

    # R-013: optional override of the category default folder binding.
    project_path_override = str(body.get("projectPath") or "").strip()

    # 裁定 42 §42.2 第 6 项的三个覆盖里，密钥这一样以前没有字段——它靠
    # ``create_profile(clone_config=True)`` 整份拷根 ``.env`` 实现（实测 13 个分身
    # 一把自己的密钥都没有）。这一格填的键值只在起分身时注进子进程环境
    # （``plobi.agents.registry.apply_secret_overrides``），不会落进 profile 家。
    secret_overrides: dict = {}
    secrets_raw = body.get("secretOverrides")
    if isinstance(secrets_raw, dict):
        secret_overrides = {
            str(key).strip(): str(value).strip()
            for key, value in secrets_raw.items()
            if str(key).strip() and str(value).strip()
        }

    registry = get_registry()
    existing = registry.get(agent_id)
    if existing is not None and is_agenda_entry(existing):
        return {"ok": True, "data": agent_row(existing)}
    # 桌面不会把密钥读回来（``agent_row`` 不吐这一格），所以一次编辑不该把它抹掉：
    # body 没带这一键时沿用已有那条的覆盖。
    if existing is not None and "secretOverrides" not in body:
        secret_overrides = dict(existing.secret_overrides)
    # 同理，弹窗从不填 ``mindSubtree``（那一格是 Mind 的位置，不是给人打的字）。不带
    # 这一键的编辑若把它抹成空串，那条分身就从「挂了 Mind 的项目」退化成用户 10-06
    # 手建的那种没有 ``mind_subtree`` 的记录——左栏于是又看不出它是同一个项目。
    if existing is not None and "mindSubtree" not in body:
        mind_subtree = existing.mind_subtree

    # R-072「新建了 Agent 就自动登记好」：建册这一步**就是**登记，章在这儿盖，
    # 不靠任何一句 description 的长相反推（那一套以前会让手下从名单里消失）。
    # 编辑一条已有记录不改章——桌面从不回传这一格，改个标语不该把一条 Mind 投影
    # 晋升成手下，也不该把用户建过的手下降级。
    registered = True if existing is None else existing.registered

    # 裁定 19: events lifecycle guards.
    source_event_id = ""
    if category == "events":
        source_event_id = str(body.get("sourceEventId") or "").strip()
        if not source_event_id:
            return _error(
                400,
                "events agents require sourceEventId (a confirmed agenda event)",
            )
        # Validate the referenced event exists and is confirmed.
        event_ok = _agenda_event_is_confirmed(source_event_id)
        if not event_ok:
            return _error(
                400,
                f"sourceEventId {source_event_id!r} is not a confirmed agenda event",
            )
        # Cap live events agents (an update of an existing events row does not
        # count toward the cap — only a brand-new one does).
        if existing is None or existing.category != "events":
            if registry.count_live_events() >= MAX_LIVE_EVENTS_AGENTS:
                return _error(
                    400,
                    f"at most {MAX_LIVE_EVENTS_AGENTS} live events agents allowed",
                )

    proposed = AgentEntry(
        name=agent_id,
        role=role,
        description=blurb,
        display_name=display,
        mind_subtree=mind_subtree,
        category=category,
        project_path=project_path_override,
        source_event_id=source_event_id,
        secret_overrides=secret_overrides,
        registered=registered,
    )
    creating_agenda = role == _AGENDA_ROLE or is_agenda_entry(proposed)
    if creating_agenda:
        found = find_agenda_agent(registry)
        if found is not None:
            if found.name == agent_id:
                return {"ok": True, "data": agent_row(found)}
            return _error(409, "agenda L2 already exists")

    # 重名禁止落在**创建那一刻**（R-013 扩写，用户 2026-10-06：「如果有出现重名的就直接
    # 禁止创建」）。判据只有一份，在 :func:`registration_blocker`——命中已有键、只差
    # 大小写的键、同一个项目名、同一棵 Mind 树都在这里拒，桌面弹窗不另算一套，只把
    # 这句显示出来。registry.upsert 里那条大小写闸门留着给 CLI / 播种机兜底。
    duplicate = registration_blocker(registry, proposed)
    if duplicate:
        return _error(409, duplicate)

    # R-071：项目分身叫 ``Plobi`` 这类记录登记了也永远问不到——它的 profile 家
    # 撞上保留名，永远建不出来。在这道门就说清「改哪一格」，别把底座那句
    # "Profile name 'plobi' is reserved" 原样丢给人，也别先落一条查不着的记录。
    blocker = profile_name_blocker(proposed)
    if blocker:
        return _error(400, blocker)

    # 「每创建一个 project 类型的 L2 agent，mind 里面要自动创建对应的文件夹和相关文件」：
    # 先立项、后落册——树建不成就直接拒绝，压根不往名册里写，自然留不下「有名册没目录」
    # 的孤儿行（反过来落册在前就得替用户回滚 yaml，那才是给自己找麻烦）。
    tree = None
    declarations = None
    if existing is None and category in MIND_PROJECT_CATEGORIES:
        from dataclasses import replace as _replace

        try:
            tree = ensure_project_tree(
                raw_name=display or agent_id,
                summary=blurb,
                category=category,
            )
        except (ProjectTreeConflict, ProjectTreeUnwritable) as exc:
            return _error(409 if isinstance(exc, ProjectTreeConflict) else 400, str(exc))

        owner = registry.find_mind_subtree_owner(tree.relative, exclude=agent_id)
        if owner:
            # 磁盘上那棵树已经属于别条分身：不许认领，也不许覆盖（reused 时没写过东西，
            # 写过的那几份由 rollback 收回）。
            tree.rollback()
            return _error(
                409,
                f"Mind 里 ``{tree.relative}`` 已经挂在分身「{owner}」名下——同一个项目不登记"
                f"第二条：要推进它就是去编辑「{owner}」，真另起一个项目请换一个项目名。",
            )
        # 建完必须同步脑仓自己那两处声明（``AGENTS.md`` §1 那一行 + ``Vault/projects/INDEX.md``）：
        # 脑仓的门禁 ``Loom/scripts/verifier.py`` 把「§1 声明 ≠ 磁盘」判成 BLOCKER，pre-commit
        # 就在跑它——只建目录不同步声明，用户在脑仓的下一次提交会被这个**我们**造的洞拦下。
        # 顺序固定在落册之前：同步不成 = 这次创建不成，树收回、名册一个字都不写。
        try:
            declarations = sync_project_declarations(dir_name=tree.dir_path.name)
        except DeclarationSyncError as exc:
            tree.rollback()
            return _error(400, str(exc))
        proposed = _replace(proposed, mind_subtree=tree.relative)

    try:
        entry = registry.upsert(proposed)
        registry.save()
    except (RegistryError, OSError) as exc:
        # 名册没落成：内存里那条也撤掉（否则 singleton 带着一条 yaml 里没有的行跑到下次
        # reload），刚改过的两处声明与刚建的树一并收回——它们现在谁也不是了，别在 Mind 里
        # 留下「声明里有、名册里没有」的孤儿目录。
        if existing is None:
            registry.remove(agent_id)
        if declarations is not None:
            declarations.rollback()
        if tree is not None:
            tree.rollback()
        return _error(400, str(exc))

    # R-013: ensure the bound folder exists (butler has none → skip) — but only
    # when this platform can actually mean the binding. A Windows-shaped binding
    # is skipped (see :func:`_project_path_is_windows_shaped`); the record stays
    # valid, the folder is just not this machine's business.
    if entry.project_path and _project_path_is_windows_shaped(entry.project_path):
        logger.warning(
            "plobi console: not creating project_path %s for %s — a Windows "
            "binding cannot be a directory on this platform, so no folder was "
            "made",
            entry.project_path, entry.name,
        )
    elif entry.project_path:
        try:
            Path(entry.project_path).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning(
                "plobi console: could not create project_path %s for %s: %s",
                entry.project_path, entry.name, exc,
            )

    try:
        registry.spawn(entry.name)
    except Exception as exc:
        logger.warning("plobi console: spawn failed for %s: %s", entry.name, exc)
        return _error(400, str(exc) or "spawn failed")

    row = agent_row(entry)
    reload_registry()
    return {"ok": True, "data": row}


def _project_path_is_windows_shaped(raw: str) -> bool:
    r"""True when a folder binding is a Windows path this machine cannot mean.

    Legacy rows still carry ``D:\Cloud\Projects\<name>`` — the retired Win box's
    layout, which ``registry.DEFAULT_CLOUD_PROJECTS_ROOT``
    (plobi/agents/registry.py:968, applied at :1062) keeps seeding, and which
    ``~/.plobi/plobi/projects.yaml`` still holds three of. On POSIX a backslash
    is an ordinary filename character, so ``Path(r"D:\Cloud\Projects").mkdir()``
    creates ONE directory literally named ``D:\Cloud\Projects`` inside whatever
    cwd the server happens to run from (measured on macOS, 2026-09-29). R-013
    must not fabricate that.

    None of registry's self-heal helpers answers this question, so the check is
    local instead of imported: ``_project_path_is_stale`` (:223) is equally true
    for the legitimate not-yet-created ``~/Projects/Aura`` that R-013 exists to
    make, and ``_is_bindable_name`` (:175) rejects every multi-segment path on
    BOTH platforms — both ask "does this resolve here" / "is this a bare entry
    name", never "could this ever mean a directory here".
    ``resolve_stale_project_path`` (:208) re-bases onto an existing root; it is
    a heal, not a refusal, and returns "" for a brand-new project too.

    ``ntpath.splitdrive`` is Windows' own drive grammar (no hand-rolled regex),
    and nothing is skipped on ``nt`` — there the historic bindings are correct
    and R-013 keeps creating them.
    """
    if os.name == "nt":
        return False
    text = (raw or "").strip()
    if not text:
        return False
    drive, _rest = ntpath.splitdrive(text)
    return bool(drive) or "\\" in text


def _agenda_event_is_confirmed(event_id: str) -> bool:
    """裁定 19: the sourceEventId must point to a confirmed agenda event.

    Reads through AgendaService.get (read-only). Any failure (missing event,
    non-confirmed status, agenda DB unreachable) returns False — fail-closed,
    so a broken agenda state never silently lets an events agent through.
    """
    try:
        from plobi.agenda import get_service

        event = get_service().get(event_id)
    except Exception as exc:
        logger.warning("plobi console: sourceEventId lookup failed: %s", exc)
        return False
    if event is None:
        return False
    return getattr(event, "status", "") == "confirmed"


def dissolve_agent(agent_id: str) -> Union[dict, JSONResponse]:
    """裁定 19: POST /api/agents/:id/dissolve — archive, do not delete.

    Moves the profile directory to ``profiles/_archived/<id>-<date>/`` and
    marks the registry entry ``archived=True``. The row stays on disk (visible
    only via ``include_archived=1``); a separate, explicitly-confirmed delete
    is a future operation (frontend owns the second confirm).

    Returns a notification body the frontend can push to DingTalk: the agent
    id, the archive path, and a human line.
    """
    registry = get_registry()
    entry = registry.get(agent_id)
    if entry is None:
        return _error(404, f"agent {agent_id!r} is not registered")
    if not is_l2(entry):
        return _error(400, "cannot dissolve the L1 secretary")
    if entry.archived:
        # Idempotent: already dissolved. Return the existing archive info.
        archive_path = _archive_profile_dir(entry, move=False)
        return {
            "ok": True,
            "data": {
                "id": agent_id,
                "archived": True,
                "archivedAt": entry.archived_at,
                "archivePath": str(archive_path) if archive_path else "",
                "alreadyArchived": True,
            },
        }

    # Mark the registry row first (so a crash mid-move leaves a clear state:
    # the row says archived even if the dir move is half-done — the dir is
    # still recoverable from the original location).
    updated = registry.dissolve(agent_id)
    registry.save()

    archive_path = _archive_profile_dir(updated, move=True)
    reload_registry()

    notification = (
        f"代理 {agent_id} 已解散并归档（category={entry.category}）。"
        f"归档目录：{archive_path or '(profile 未落地)'}。"
        "注册表条目保留，可在归档列表查看；彻底删除需二次确认。"
    )
    return {
        "ok": True,
        "data": {
            "id": agent_id,
            "archived": True,
            "archivedAt": updated.archived_at,
            "archivePath": str(archive_path) if archive_path else "",
            "notification": notification,
        },
    }


def _archive_profile_dir(entry: AgentEntry, *, move: bool) -> Optional[Path]:
    """Move (or locate) the profile dir under ``profiles/_archived/<id>-<date>``.

    Returns the archive path, or None when the profile was never materialized
    (no dir to move). ``move=False`` only locates; ``move=True`` performs the
    rename. A missing source dir is not an error — the row is still marked
    archived, just with no folder to carry over.
    """
    try:
        from plobi_cli.profiles import get_profile_dir
    except ImportError:
        return None
    try:
        src = get_profile_dir(entry.profile_name)
    except (KeyError, Exception):
        return None
    if not src.exists():
        return None
    from datetime import date

    suffix = entry.archived_at or date.today().isoformat()
    # Sanitize the id for use as a path component (the id regex already
    # restricts to [a-zA-Z0-9_-], but be defensive).
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in entry.name)
    dest = src.parent / "_archived" / f"{safe_id}-{suffix}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        # Idempotent: archive already in place. Only return the path.
        return dest
    if not move:
        return dest  # locate-only: report where it would go
    try:
        src.rename(dest)
    except OSError as exc:
        logger.warning(
            "plobi console: archive move failed for %s: %s", entry.name, exc
        )
        return None
    return dest


@router.post("/agents", name="create_agent")
async def create_agent_route(request: Request):
    """§5 POST /api/agents — human registers one project L2 (裁定 18)."""
    try:
        body = await request.json()
    except Exception:
        return _error(400, "id is required")
    if not isinstance(body, dict):
        return _error(400, "id is required")
    return await run_in_threadpool(create_agent, body)


@router.post("/agents/{agent_id}/dissolve", name="dissolve_agent")
async def dissolve_agent_route(agent_id: str):
    """裁定 19: archive an L2 (profile dir → _archived/, row marked archived)."""
    return await run_in_threadpool(dissolve_agent, agent_id)


@router.get("/agents/{agent_id}/subagents")
async def list_subagents(agent_id: str):
    """§5 GET /api/agents/:id/subagents — that L2's L3 children."""
    if not await run_in_threadpool(_known, agent_id):
        return _error(404, f"agent {agent_id!r} is not registered")
    rows = await run_in_threadpool(subagents_for, agent_id)
    return {"ok": True, "data": rows}


@router.get("/agents/{agent_id}/overview", name="agent_overview")
async def agent_overview_route(agent_id: str):
    """§5 GET /api/agents/:id/overview — the S2 status card."""
    overview = await run_in_threadpool(agent_overview, agent_id)
    if overview is None:
        return _error(404, f"agent {agent_id!r} is not registered")
    return {"ok": True, "data": overview}


# --------------------------------------------------------------------------- #
# routine templates (C4) — minimal read + write
# --------------------------------------------------------------------------- #


def _list_routines_data() -> list[dict]:
    from plobi.agenda import store as agenda_store

    conn = agenda_store.connect()
    try:
        return [t.to_dict() for t in agenda_store.list_routine_templates(conn)]
    finally:
        conn.close()


def _upsert_routine_data(body: dict) -> dict:
    from plobi.agenda import store as agenda_store

    conn = agenda_store.connect()
    try:
        template = agenda_store.upsert_routine_template(
            conn,
            template_id=str(body.get("id") or ""),
            title=str(body.get("title") or ""),
            start_time=str(body.get("start_time") or ""),
            end_time=str(body.get("end_time") or ""),
            weekdays=body.get("weekdays"),
            enabled=bool(body.get("enabled") or False),
        )
        return template.to_dict()
    finally:
        conn.close()


@router.get("/routines", name="list_routines")
async def list_routines():
    """C4 GET /api/routines — routine templates (incl. disabled seeds)."""
    rows = await run_in_threadpool(_list_routines_data)
    return {"ok": True, "data": rows}


@router.post("/routines", name="upsert_routine")
async def upsert_routine(request: Request):
    """C4 POST /api/routines — create/update one template (upsert by id)."""
    try:
        body = await request.json()
    except Exception:
        return _error(400, "title/start_time/end_time are required")
    if not isinstance(body, dict):
        return _error(400, "title/start_time/end_time are required")
    try:
        template = await run_in_threadpool(_upsert_routine_data, body)
    except Exception as exc:
        if type(exc).__name__ == "AgendaValidationError":
            return _error(400, str(exc))
        raise
    return {"ok": True, "data": template}
