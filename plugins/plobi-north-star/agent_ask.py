"""``plobi_agent_ask`` — the L1 secretary asks one of its own project 分身 a
question by name and gets that 分身's real answer back (裁定 50.4, 方案甲).

Why this exists: before it, 「某项目现在怎么样」 could only be answered by
reading ``plan.md`` / ``progress.md`` off disk
(``plobi.agents.registry.run_project_status``) — i.e. "the last day somebody
wrote something". The living 分身 was never consulted. This module opens that
channel; the kanban board path stays what it always was (asynchronous task
handoff), and nothing here posts to a board.

How it works — deliberately the same shape the kanban dispatcher already
proves in production, not a new mechanism:

* **Target** resolves through :class:`plobi.agents.registry.AgentRegistry`
  (name → registry row → its ``project_path``). The model never hands out an
  arbitrary name, archived rows are refused, and a refusal lists the names
  that *are* askable.
* **The 分身 runs as a subprocess** built from the shared
  :func:`plobi_cli.plobi_bin.resolve_plobi_argv` recipe plus the identity /
  ledger pinning (``PLOBI_AGENT_ID`` = the registry name, ``PLOBI_HOME`` =
  the **shared layer root**, ``PLOBI_STATE_HOME`` = the single ledger,
  ``TERMINAL_CWD`` pinned to the real project path, ``PLOBI_TUI`` dropped,
  ``--cli --accept-hooks chat -q``). **No ``-p <profile>``** —— 裁定 42
  「砍掉 Profile 当分身单位」，一个 profile 不再等于一整套 Plobi。
* **The question is attributed** in the text the child receives, so it knows
  who is asking and why.
* **The record is the child's own session.** The child's config/skills/memories
  come from the **shared layer** (one copy, 裁定 42), while its session ledger
  is the shared top-level one: the spawner passes ``PLOBI_STATE_HOME``（缺口 A）
  and ``PLOBI_AGENT_ID``（裁定 180）so the exchange lands in the single
  ``state.db`` attributed to this 分身 by row; the answer and the session id
  are read back out of that same ledger. No second transcript store.
* **Guards**: a bounded timeout (the caller's ``config.yaml`` knob is the floor;
  裁定 167 乙档 lets the asked 分身's own measured history raise it — hard ceiling
  in code, never a ``PLOBI_*`` env var), ``--yolo`` is never in the argv, and every
  failure (unknown target, missing profile, non-zero exit, timeout, missing
  binary) comes back as a structured tool error so the secretary's turn
  survives. The child is always reaped — a busy or wedged one is killed
  together with its process group, never left running.

Footprint: registered by ``plugins/plobi-north-star`` under the
``plobi_north_star`` toolset and gated by :func:`check_agent_ask_available` —
the schema only reaches a model when the registry actually has at least one
live 分身 (Footprint Ladder rung 3). It is **not** in ``_PLOBI_CORE_TOOLS``, so
only agents whose toolset includes this plugin ever pay for it.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import signal
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any, NamedTuple, Optional, Sequence

from plobi_constants import get_plobi_state_home
from tools.registry import tool_error

logger = logging.getLogger(__name__)

PLUGIN_KEY = "plobi-north-star"
ASK_TOOLSET = "plobi_north_star"

#: 「谁能被当面问」的判据**不在这里**——那是名册的事，一份在
#: ``plobi.agents.registry.agent_askability``（角色 / 归档 / profile 名 / 家在不在
#: 盘上，四条都是结构性事实）。名单 ``plobi_secretary_ask(intent=project_status)``
#: 与这张嘴读同一份，才不会出现「名单说有这个人、问到跟前说问不到」（R-072）。
#: 项目分身之外都不放行：日程 L2 有它自己那张嘴（``plobi_secretary_ask``），
#: default profile 就是提问的那一个——挡的是**角色**，不是名字，日程秘书不因
#: 自己是系统自带的就特殊一分（裁定 69）。

#: Bounded wait for the child. ``timeout_seconds`` is a ``config.yaml`` knob
#: (``plugins.entries.plobi-north-star.agent_ask.timeout_seconds``) — behavioural
#: settings go there, never in ``.env`` or a new ``PLOBI_*`` var. The ceiling is
#: code-only on purpose: a bad value must not park the secretary indefinitely.
DEFAULT_TIMEOUT_SECONDS = 180.0
MIN_TIMEOUT_SECONDS = 5.0
MAX_TIMEOUT_SECONDS = 900.0

#: 乙档（裁定 167）：预算跟着**被问分身自己**的耗时走，不再用一个全局数掩所有分身。
#: 区间刻意落在 ``MIN``/``MAX`` 之内——上限那道守卫（坏配置不许把秘书挂死）不动它。
HISTORY_BUDGET_FLOOR_SECONDS = 60.0
HISTORY_BUDGET_CEILING_SECONDS = 600.0
HISTORY_BUDGET_MULTIPLIER = 1.5

#: 最近几趟。再多也救不了：一个分身的问法一变，旧账就成了噪声。
HISTORY_RUNS_CONSIDERED = 8
#: 超过这个数就不算「当面问一句」的耗时了（现核：不这么夹，样本里最大的一条是 88 万秒）。
HISTORY_RUN_MAX_SECONDS = 1800.0

#: How long a terminated child gets to exit before it is killed outright.
_KILL_GRACE_SECONDS = 5.0

_ANSWER_MAX_CHARS = 6000
_CHILD_TAIL_MAX_CHARS = 800

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

AGENT_ASK_SCHEMA = {
    "name": "plobi_agent_ask",
    "description": (
        "当面问一个项目 / 课程的 L2 Agent 一句话，拿回它本人现在的回答。用户问「某个项目现在怎么样」"
        "「这件事你手上是什么情况」时用它——别只读硬盘上那份笔记，那只是上次有人写字那一天"
        "的话，这个 Agent 会自己回答。能问谁看名册：plobi_secretary_ask（intent=project_status）"
        "回的 roster 就是手下名单，其中 askable 那几行现在问得到；一次问一个 Agent，"
        "要问几个项目就分别问几次。项目名照用户说的那样填就行（大小写、显示名都认），"
        "问题尽量用他的原话，不要替他改写。这次问答会留在被问的那个 Agent 自己的会话记录里，"
        "回答同时带回它的会话号，方便事后回看。"
        "问不到（名字不对、已经归档、家还没在这台机器上落地、它答不上来、超时）会明确回错，"
        "并列出现在能问的项目名——照实说那一句原因，不要拿笔记冒充 Agent 的回答，"
        "也不要换个名字硬试或转去问日程秘书。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "description": "要问的那个项目 / 课程的 L2 Agent 的名字。",
            },
            "question": {
                "type": "string",
                "description": "要问它的话；尽量用用户的原话，不要替他改写。",
            },
        },
        "required": ["agent", "question"],
    },
}


# ---------------------------------------------------------------------------
# Service gate (Footprint Ladder rung 3 — zero schema footprint otherwise)
# ---------------------------------------------------------------------------


def has_askable_agents() -> bool:
    """True when the registry holds at least one live project 分身."""
    try:
        return bool(askable_entries())
    except Exception:  # an unreadable projects.yaml must not advertise the tool
        logger.debug("plobi_agent_ask: registry probe failed", exc_info=True)
        return False


def check_agent_ask_available() -> bool:
    """``check_fn`` — the secretary only sees the tool when it can use it.

    Two cheap conditions: this profile already opted into the plugin (same gate
    as ``plobi_secretary_ask``, so dispatcher-spawned workers stay out), *and*
    there is somebody to ask.
    """
    if not _master_mode_enabled():
        return False
    return has_askable_agents()


def _master_mode_enabled() -> bool:
    """Reuse this plugin's own service gate rather than restating it."""
    try:
        from . import master_tools as _mt

        return bool(_mt.check_plobi_master_mode())
    except Exception:  # pragma: no cover - defensive
        logger.debug("plobi_agent_ask: master-mode gate unavailable", exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _config() -> dict:
    try:
        from plobi_cli.config import load_config_readonly

        return load_config_readonly() or {}
    except Exception:
        return {}


def timeout_seconds(cfg: Optional[dict] = None) -> float:
    """Resolve the child timeout: the ``config.yaml`` value, clamped to the ceiling.

    An absent, unparseable, or out-of-range value falls back to the bounded
    default — never to an unbounded wait.
    """
    if cfg is None:
        cfg = _config()
    raw: Any = None
    if isinstance(cfg, dict):
        try:
            from plobi_cli.config import cfg_get

            raw = cfg_get(
                cfg, "plugins", "entries", PLUGIN_KEY, "agent_ask", "timeout_seconds"
            )
        except Exception:
            raw = None
    if raw is None:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("plobi_agent_ask: ignoring non-numeric timeout_seconds %r", raw)
        return DEFAULT_TIMEOUT_SECONDS
    if value < MIN_TIMEOUT_SECONDS:
        return MIN_TIMEOUT_SECONDS
    if value > MAX_TIMEOUT_SECONDS:
        logger.warning(
            "plobi_agent_ask: timeout_seconds %s clamped to ceiling %s",
            value,
            MAX_TIMEOUT_SECONDS,
        )
        return MAX_TIMEOUT_SECONDS
    return value


# ---------------------------------------------------------------------------
# Budget — 全局那个数是下限，分身自己的耗时才是这一问真正该等多久（裁定 167 乙档）
# ---------------------------------------------------------------------------


class Budget(NamedTuple):
    """一次有界的等待：给多少秒，以及这个数建立在该分身自己的几趟历史上。"""

    seconds: float
    runs: int  # 0 = 这一档没有可读的历史，用的就是调用方那个旋钮


def _p75(values: Sequence[float]) -> float:
    """第 75 百分位，取 inclusive 法：结果不越过实测到的最慢那一趟。"""
    if len(values) == 1:
        return float(values[0])
    return statistics.quantiles(values, n=4, method="inclusive")[2]


def _one_shot_durations(entry, *, runs: int = HISTORY_RUNS_CONSIDERED) -> list[float]:
    """该分身自己「一问到底」的历史耗时（秒），最近 ``runs`` 趟；读不到就是空。

    只数 ``source='cli'`` 且**只有一条 user** 的会话——那正是本工具 spawn 的子进程形状
    （``plobi -p <profile> … -q`` 一趟答完）。多轮会话里「首条 user → 末条 assistant」横跨
    真人几次回车，拿来当耗时是假的，所以还要 ``turns == 1`` 并把超长的那几条丢掉。

    耗时取**最后一条 assistant − 第一条 user**，不是第一条 assistant：被问的分身中途要跑
    工具，只量第一个 LLM 步会系统性低估（现核同一趟：首答 23 秒、到底 185 秒），低到让
    这把刀反过来把等待砍短——那正是它要治的病。
    """
    db_path = _child_state_db()
    if db_path is None:
        return []
    rows = _query_rows(
        db_path,
        # 总账是单库（缺口 A），历史耗时只认**这个分身**的行——agent_id 由
        # 子进程建会话时落（PLOBI_AGENT_ID）。老家的家库快照不在此处兜底：
        # 那份只剩迁移前旧数据，拿它算耗时反而是过期的假证据。
        "SELECT min(CASE WHEN m.role = 'user' THEN m.timestamp END),"
        "       max(CASE WHEN m.role = 'assistant' THEN m.timestamp END),"
        "       sum(CASE WHEN m.role = 'user' THEN 1 ELSE 0 END) "
        "  FROM sessions s JOIN messages m ON m.session_id = s.id "
        " WHERE s.source = 'cli' AND s.agent_id = ? "
        " GROUP BY s.id ORDER BY max(m.timestamp) DESC LIMIT ?",
        (entry.name, runs),
    )
    durations: list[float] = []
    for asked, answered, turns in rows:
        if int(turns or 0) != 1 or asked is None or answered is None:
            continue
        duration = float(answered) - float(asked)
        if 0.0 < duration <= HISTORY_RUN_MAX_SECONDS:
            durations.append(duration)
    return durations


def budget_for(entry, *, configured: Optional[float] = None) -> Budget:
    """这一问给多少秒：慢分身不再被快分身的那个全局数掩掉（R-072 复现的成因）。

    口径 26（裁定 143）守死在这里——配置读数**只有**调用方自己那一个旋钮
    ``plugins.entries.plobi-north-star.agent_ask.timeout_seconds``（:func:`timeout_seconds`），
    不读被问分身的那份 config。历史耗时走的是本工具取答案本来就要开的那只只读探针
    （:func:`_child_state_db` + :func:`_query_rows`），不新增第二条进别人家的路。
    """
    base = timeout_seconds() if configured is None else configured
    durations = _one_shot_durations(entry)
    if not durations:
        return Budget(base, 0)
    headroom = min(
        HISTORY_BUDGET_CEILING_SECONDS,
        max(HISTORY_BUDGET_FLOOR_SECONDS, _p75(durations) * HISTORY_BUDGET_MULTIPLIER),
    )
    # 只许抬高，不许砍短：这一档治的是「答完之前就被杀」。若反过来把某个分身的等待
    # 从 180 秒降到 60 秒，这把刀自己就造出了更多的「派了没回音」。
    return Budget(min(MAX_TIMEOUT_SECONDS, max(base, headroom)), len(durations))


# ---------------------------------------------------------------------------
# Target resolution — through the registry, never a name the model invents
# ---------------------------------------------------------------------------


class AskRefusal(Exception):
    """A refusal to ask: carries the message and the names that ARE askable."""

    def __init__(self, message: str, available: Optional[list[str]] = None):
        super().__init__(message)
        self.message = message
        self.available = list(available or [])


def _load_registry():
    from plobi.agents.registry import AgentRegistry

    return AgentRegistry.load()


def askable_entries(registry=None) -> list:
    """Live project 分身这一条嘴问得到的那些行。

    判据 = :func:`plobi.agents.registry.agent_askability`，和名册那一格同源：
    名单里标了 ``askable=false`` 的行不会出现在这儿（反之亦然）。
    """
    from plobi.agents.registry import is_askable_agent

    reg = registry if registry is not None else _load_registry()
    return [entry for entry in reg.entries() if is_askable_agent(entry)]


def unaskable_project_entries(registry=None) -> list:
    """名册里那些**项目分身但此刻问不到**的行：``[(entry, 原因), …]``。

    就是名单里 ``askable=false`` 的那几行（角色是项目、没归档，却缺家 / profile 名
    建不出来）。拒答时把它们点出来，秘书看到的是「名单里有这个人，但它现在问不到，
    因为……」——不是「查无此人」，也不用换个名字再试一次。
    """
    from plobi.agents.registry import ASKABLE_ROLE, agent_askability

    reg = registry if registry is not None else _load_registry()
    found = []
    for entry in reg.entries():
        if entry.archived or entry.role != ASKABLE_ROLE:
            continue
        askable, reason = agent_askability(entry)
        if not askable:
            found.append((entry, reason))
    return found


def _available_names(entries) -> list[str]:
    return sorted({entry.name for entry in entries})


def _label(entry) -> str:
    return ((entry.display_name or "").strip() or entry.name)


def _matches(entry, needle: str) -> bool:
    """Accept the registry key, the display name, or the profile name."""
    names = {entry.name, entry.display_name, entry.profile or entry.name}
    return needle in {name.strip().casefold() for name in names if name}


def _same_home_identity(entry) -> str:
    """一个分身在这台机器上的家（小写 profile 名）——两条记录撞上同一个家就是同一个人。"""
    return (entry.profile_name or entry.name).strip().casefold()


def _pick_row_for_one_home(entries, asked: str):
    """同一个 profile 家被登记成多条时，定一条代表它。

    顺序说得出理由：用户登记过的那条（``registered`` 为真——新建 / 登记那一步盖的
    章，Mind 播种机的投影是 ``False``）→ 大小写正好对上他输入的那条 → 名字最小的
    一条。没有这一步，``Nymo`` / ``nymo`` 一起命中就会报「对上了好几个分身
    （Nymo、Nymo）」，而它们本来就是同一个家（R-071 第一条）。
    """
    registered = [entry for entry in entries if entry.registered]
    pool = registered or list(entries)
    exact = [entry for entry in pool if entry.name.strip() == asked.strip()]
    if len(exact) == 1:
        return exact[0]
    return sorted(pool, key=lambda entry: entry.name.casefold())[0]


def _collapse_same_home(hits, asked: str) -> list:
    """把只差大小写（同一个家）的多条命中收成一条；真的属于不同家时原样返回。"""
    by_home: dict[str, list] = {}
    for entry in hits:
        by_home.setdefault(_same_home_identity(entry), []).append(entry)
    if len(by_home) == 1:
        return [_pick_row_for_one_home(next(iter(by_home.values())), asked)]
    return hits


def _roster_hint(entries) -> str:
    names = _available_names(entries)
    return "、".join(names) if names else "（现在一个都没有）"


def resolve_target(name: str, *, registry=None, entries=None):
    """Map what the model asked for onto exactly one live registry row.

    Raises :class:`AskRefusal` — never a bare ``KeyError``/``AttributeError`` —
    for an unknown name, an archived row, a row that is not a project 分身, or a
    row whose profile home can never be created on this machine (a 分身 named
    ``Plobi`` collides with the reserved profile name ``plobi``, so it has no
    home to ask — R-071 第二条), always listing the names that *are* askable so
    the secretary can correct itself instead of guessing at an identifier it
    never saw.
    """
    needle = (name or "").strip().casefold()
    reg = registry if registry is not None else _load_registry()
    if entries is None:
        entries = askable_entries(reg)
    available = _available_names(entries)

    if not needle:
        raise AskRefusal(
            f"要问哪一个 L2 Agent？现在能问的有：{_roster_hint(entries)}", available
        )

    hits = _collapse_same_home(
        [entry for entry in entries if _matches(entry, needle)], name or ""
    )
    if len(hits) == 1:
        entry = hits[0]
        from plobi.agents.registry import profile_name_blocker

        blocker = profile_name_blocker(entry)
        if blocker:
            raise AskRefusal(blocker, available)
        return entry
    if len(hits) > 1:
        # 报「（Nymo、Nymo）」那种谁也不具体的名单没用：这里带上的必须是能拿去
        # 改的注册键，不是显示名。
        who = "、".join(sorted(f"{_label(entry)}（{entry.name}）" for entry in hits))
        raise AskRefusal(
            f"「{name}」对上了好几个同名 Agent（{who}），说个更具体的名字。", available
        )

    # Not askable — but is it a row that exists? 名字对上了却不在这张嘴的名单里，
    # 就用名册那份同一判据给**这一条自己的**原因（归档 / 角色 / 家没落地 / profile
    # 名建不出来），而不是回一句含混的「不是项目分身」让秘书再试一次别的名字。
    others = [entry for entry in reg.entries() if _matches(entry, needle)]
    if others:
        from plobi.agents.registry import agent_askability

        entry, reason = others[0], ""
        for candidate in others:
            askable, why_not = agent_askability(candidate)
            if not askable:
                entry, reason = candidate, why_not
                break
        raise AskRefusal(
            f"「{name}」问不到：{reason or '这一条不在能当面问的名单里。'}"
            f"现在能问的有：{_roster_hint(entries)}",
            available,
        )
    raise AskRefusal(
        f"没有「{name}」这个项目 / 课程的 L2 Agent。现在能问的有：{_roster_hint(entries)}", available
    )


# ---------------------------------------------------------------------------
# The question the child actually receives
# ---------------------------------------------------------------------------


def compose_question(question: str, *, entry, asker: str = "总秘书") -> str:
    """Attribution header + the owner's original words, as plain prose.

    The 分身 needs to know who is asking and that this is a live question, not
    another note-reading exercise. No framework vocabulary and no field names —
    it is text a person could have typed.
    """
    return "\n".join(
        [
            f"【{asker}代问】",
            f"提问人：{asker}——替他统筹各项目、把活分下去的那一个。",
            f"要回答的是你（{_label(entry)}）手上的实情，不是硬盘那份笔记里的旧话。",
            "照你现在的真实进度回答；没做过、不确定的就说不确定，不要编。",
            "他的原话：",
            (question or "").strip(),
        ]
    )


# ---------------------------------------------------------------------------
# Child command + environment — the dispatcher's proven recipe
# ---------------------------------------------------------------------------


def _project_cwd(entry) -> str:
    """The 分身's real project directory, or "" when there isn't one here.

    Falls back to the registry's self-heal resolver so a binding that moved to
    another machine's default root still pins the right directory instead of
    silently inheriting whoever asked.
    """
    raw = (entry.project_path or "").strip()
    if raw and os.path.isabs(raw) and os.path.isdir(raw):
        return raw
    try:
        from plobi.agents.registry import resolve_stale_project_path

        healed = (resolve_stale_project_path(entry) or "").strip()
    except Exception:
        healed = ""
    if healed and os.path.isabs(healed) and os.path.isdir(healed):
        return healed
    return ""


def build_child_command(
    entry,
    prompt: str,
    *,
    asked_by: str = "l1",
    resume_session: Optional[str] = None,
) -> tuple[list[str], dict[str, str], Optional[str]]:
    """Return ``(argv, env, cwd)`` for one face-to-face question.

    ``resume_session`` is the conversation the question was just put into. Given,
    the child answers **inside that session** instead of opening a fresh one —
    裁定 183 要的是「答落回同一屏」，2026-10-22 实测少了这两个字：问理论力学那一次
    子进程跑完了，回答落在另一条 ``source=cli`` 的新会话里，屏幕上那一屏只有问话没有
    下文（用户原话「和死掉了一样」）。``--resume`` 是顶层旗标（
    ``plobi_cli/main.py`` 的 ``_TOP_LEVEL_VALUE_FLAGS``），必须排在 ``chat`` 之前。

    **形状按裁定 42 + 180**（2026-10-22 改写；旧版是 `-p <profile>` 那一套，
    用户当场点破「profile 概念怎么又回归了」——那一套正是裁定 42 判废的
    「一个 profile 就是一整套 Plobi」。旧版与它犯的错一并留在这里当反例）：

    * 身份 = ``PLOBI_AGENT_ID``（名册 name）。裁定 42 标题即「砍掉 Profile 当
      分身单位：L2 降为一条项目记录」，§42.2 终态只保留 6 项，其中没有 profile；
    * 配置 / 技能 / 记忆走**共享层一份**，不再每个分身各复制一套。现核：共享层
      466 个文件、72 个技能，**不带 `-p` 起进程实测可用**（子进程自己调
      `skills_list` 报「72 个技能」）；
    * 会话落**单库**（``PLOBI_STATE_HOME``）——裁定 180 的行级归属。

    所以这里**不再** ``-p <profile>``、**不再**把 ``PLOBI_HOME`` 指到
    ``profiles/<name>``。父进程自己可能就是被 `-p` 起来的（它的 ``PLOBI_HOME``
    指向某个家），子进程必须**显式回到共享层 root**，否则又会变成一整套 Plobi。

    仍然从 ``kanban_db._default_spawn`` 借的只有两样：同一个二进制解析器，和
    **不挂任何强制批准旗**——想干危险事的子进程会停下来问，有界超时才是兜。
    """
    from plobi_cli.plobi_bin import resolve_plobi_argv
    from plobi_constants import get_default_plobi_root, get_plobi_state_home

    child_env = dict(os.environ)
    # 共享层 root（裁定 42「重东西只留一份」+ R-096）：父进程可能身在某个 profile
    # 家里，子进程必须回到这一份，否则每个分身又各带一套 Plobi。
    child_env["PLOBI_HOME"] = str(get_default_plobi_root())
    # 缺口 A（WP-AGENT-SESSION-ATTRIBUTION）：会话总账单独指到本进程的总账
    # （``PLOBI_STATE_HOME``）——子分身的问答与 L1/桌面落同一个单库，按行级归属。
    # 嵌套场景（秘书自己也是被派出来的子进程）要把**自己的总账**原样传下去，
    # 而不是把自己 PLOBI_HOME 当总账。
    child_env["PLOBI_STATE_HOME"] = str(get_plobi_state_home())
    # 裁定 180 的归属键：子进程建会话时（cli.py create_session）读它落到
    # sessions.agent_id——总账里这一行才能认出是「Resonote 答的」而不是 L1。
    # 这也是**身份**：profile 不再是分身单位之后，名册 name 就是它。
    child_env["PLOBI_AGENT_ID"] = entry.name
    # 裁定 42 §42.2 第 6 项的「密钥」这一样：只有这条记录声明的覆盖进得到子进程，
    # 共享的那几把我从继承来的环境里拿——分身的家里不留第二份 .env。
    from plobi.agents.registry import apply_secret_overrides

    apply_secret_overrides(child_env, entry)

    cwd = _project_cwd(entry)
    if cwd:
        # Pin the child's working directory the way the dispatcher pins a
        # worker's workspace: the child's file tools and context files (its own
        # project notes) then anchor on the project, not on the secretary.
        # Only a real absolute directory is pinned — file tools reject a
        # relative or bogus TERMINAL_CWD, and an inherited value beats a wrong
        # one.
        child_env["TERMINAL_CWD"] = cwd
    # A child must never boot the interactive TUI: an inherited PLOBI_TUI=1 (or
    # ``display.interface: tui``) would send a quiet run into Ink, which bails
    # out on non-TTY stdout. ``--cli`` is the precedence pin; dropping the env
    # var covers older builds found earlier on PATH.
    child_env.pop("PLOBI_TUI", None)

    argv = [
        *resolve_plobi_argv(),
        "--cli",
        # The child runs on the SHARED layer (no profile home), so it sees the
        # shared shell-hook allowlist; pass it explicitly the same way the
        # dispatcher does for workers.
        "--accept-hooks",
        "chat",
        "-q",
        prompt,
    ]
    if (resume_session or "").strip():
        # 排在子命令之前：`--resume` 是顶层旗标，落在 `chat` 后面会被当成别的东西。
        # 按 `chat` 的位置插，不数前缀长度——`resolve_plobi_argv()`  returned 的段数本来就随安装布局变。
        _chat_at = argv.index("chat")
        argv[_chat_at:_chat_at] = ["--resume", str(resume_session).strip()]
    # 这条问在分身自己的会话里要能和"主人亲手打的"分开（messages.asked_by）。
    # 子进程是唯一会读到它的地方，所以随 env 传下去，不写进 prompt 文本里。
    child_env["PLOBI_ASKED_BY"] = asked_by or "l1"
    return argv, child_env, cwd or None


# ---------------------------------------------------------------------------
# The child's own session IS the record — read the answer back out of it
# ---------------------------------------------------------------------------


def _child_state_db() -> Path:
    """子分身会话的真源：单库总账（缺口 A 之后不再是分身家的 state.db）。

    与 spawn 时传给子进程的 ``PLOBI_STATE_HOME`` 同一来源（
    :func:`plobi_constants.get_plobi_state_home`）——同一个进程里 env 不会
    中途变，读回去的就是子进程刚写的那份。
    """
    return get_plobi_state_home() / "state.db"


def _query_rows(db_path: Path, sql: str, params: tuple) -> list[tuple]:
    """Read-only probe of the child's session store. Never creates a database."""
    if not db_path.is_file():
        return []
    import sqlite3

    try:
        conn = sqlite3.connect(str(db_path), timeout=2.0)
    except Exception:
        logger.debug("plobi_agent_ask: cannot open %s", db_path, exc_info=True)
        return []
    try:
        return list(conn.execute(sql, params).fetchall())
    except Exception:
        logger.debug("plobi_agent_ask: session read failed for %s", db_path, exc_info=True)
        return []
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def _new_session_id(entry, *, not_before: float) -> str:
    """The session the child just ran: the newest one started at/after the spawn.

    Best effort by design — an unreadable ``state.db`` costs the pointer, never
    the answer. 两道防串门的闸：``not_before`` 地板挡住秘书自己（更老的）会话；
    ``agent_id = entry.name`` 挡住总账里**并发的别人**——单库之后（缺口 A），
    桌面 / L1 / 其他分身的会话都在同一份库里，光靠时间地板会把别人刚开的
    会话错认成这一问的记录。
    """
    db_path = _child_state_db()
    rows = _query_rows(
        db_path,
        "SELECT id FROM sessions WHERE agent_id = ? AND started_at >= ? "
        "ORDER BY started_at DESC LIMIT 1",
        (entry.name, not_before - 2.0),
    )
    return str(rows[0][0] or "") if rows else ""


def _session_answer(entry, session_id: str) -> str:
    """The child's final answer text, from its own session record."""
    if not session_id:
        return ""
    db_path = _child_state_db()
    if db_path is None:
        return ""
    rows = _query_rows(
        db_path,
        "SELECT content FROM messages WHERE session_id = ? AND role = 'assistant' "
        "AND content IS NOT NULL AND content != '' AND active = 1 "
        "ORDER BY id DESC LIMIT 1",
        (session_id,),
    )
    return str(rows[0][0] or "") if rows else ""


def _fallback_answer(stdout: str) -> str:
    """Best effort when the child's own session record can't be read."""
    text = _ANSI_RE.sub("", stdout or "").strip()
    return text[-_ANSWER_MAX_CHARS:] if len(text) > _ANSWER_MAX_CHARS else text


def _tail(text: str) -> str:
    cleaned = _ANSI_RE.sub("", text or "").strip()
    return cleaned[-_CHILD_TAIL_MAX_CHARS:] if len(cleaned) > _CHILD_TAIL_MAX_CHARS else cleaned


# ---------------------------------------------------------------------------
# Subprocess run — always reaped, never a stray child
# ---------------------------------------------------------------------------


def _kill_child(proc: subprocess.Popen) -> None:
    """Take out the child *and its group*, then reap it. Never leaves a stray.

    ``start_new_session=True`` put the child in its own session, so the group
    kill reaches whatever the 分身 itself spawned; the grace period lets it shut
    down cleanly before SIGKILL.
    """
    try:
        if hasattr(os, "killpg") and hasattr(signal, "SIGTERM"):
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                proc.terminate()
            with contextlib.suppress(Exception):
                proc.wait(timeout=_KILL_GRACE_SECONDS)
            if proc.poll() is not None:
                return
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                proc.kill()
        else:  # pragma: no cover - non-POSIX fallback
            proc.kill()
    except Exception:  # pragma: no cover - defensive
        logger.debug("plobi_agent_ask: child kill failed", exc_info=True)
        with contextlib.suppress(Exception):
            proc.kill()
    with contextlib.suppress(Exception):
        proc.wait(timeout=_KILL_GRACE_SECONDS)


def _run_child(argv, env, cwd, limit: float) -> dict:
    """Run the 分身 to completion, or until the bounded wait expires."""
    started = time.time()
    proc: Optional[subprocess.Popen] = None
    try:
        proc = subprocess.Popen(  # noqa: S603 — argv is a list we built above
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # A child that prints undecodable bytes must not turn into an
            # exception in the middle of the secretary's turn.
            errors="replace",
            env=env,
            cwd=cwd,
            start_new_session=True,
        )
    except FileNotFoundError:
        return {
            "timed_out": False,
            "missing_binary": True,
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "duration": time.time() - started,
        }
    except OSError as exc:
        return {
            "timed_out": False,
            "missing_binary": False,
            "returncode": None,
            "stdout": "",
            "stderr": str(exc),
            "duration": time.time() - started,
        }

    timed_out = False
    out = err = ""
    try:
        out, err = proc.communicate(timeout=limit)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_child(proc)
        with contextlib.suppress(Exception):
            out, err = proc.communicate(timeout=_KILL_GRACE_SECONDS)
    finally:
        if proc.poll() is None:  # belt and braces: never hand back a live child
            _kill_child(proc)

    return {
        "timed_out": timed_out,
        "missing_binary": False,
        "returncode": proc.poll(),
        "stdout": out or "",
        "stderr": err or "",
        "duration": time.time() - started,
    }


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def handle_agent_ask(args: dict, *, entry=None, resume_session: str = "", asker: str = "", **kwargs) -> str:
    """Ask one project 分身 a question; hand its answer back to the secretary.

    Every branch returns a JSON string — this must never raise into the L1
    conversation loop.

    ``entry`` / ``resume_session`` are for :func:`channel.handle_ask`（裁定 183 那张
    嘴）：收件人已经由**名册里没归档的那一行**判过了，这里不许再按「角色是不是项目
    分身」判第二次——两枚原子对同一个人判定不一致，就是用户看到的「0.06 秒就被拒」
    （现读：`say` 的名单 24 行，这一侧 23 行，缺的那一行正是 `agenda`）。
    """
    args = args if isinstance(args, dict) else {}
    name = str(args.get("agent") or "").strip()
    question = str(args.get("question") or "").strip()

    if entry is None:
        try:
            entries = askable_entries()
        except Exception as exc:  # an unreadable roster ≠ a broken turn
            logger.warning("plobi_agent_ask: registry unreadable: %s", exc)
            return tool_error("读不到项目 / 课程的 L2 Agent 名册，这次问不了。", ok=False)

        try:
            entry = resolve_target(name, entries=entries)
        except AskRefusal as refusal:
            return tool_error(refusal.message, ok=False, available=refusal.available)

    if not question:
        return tool_error(
            f"要问「{_label(entry)}」什么？question 不能为空。", ok=False, agent=entry.name
        )

    budget = budget_for(entry)
    limit = budget.seconds
    try:
        argv, env, cwd = build_child_command(
            entry, compose_question(question, entry=entry, asker=asker or "总秘书"),
            asked_by=f"l1:{kwargs.get('task_id') or kwargs.get('session_id') or ''}".rstrip(":") or "l1",
            resume_session=resume_session,
        )
    except AskRefusal as refusal:
        return tool_error(refusal.message, ok=False, available=refusal.available)
    except Exception as exc:
        logger.warning("plobi_agent_ask: command build failed: %s", exc)
        return tool_error(f"问不了「{_label(entry)}」：{exc}", ok=False, agent=entry.name)

    started = time.time()
    try:
        result = _run_child(argv, env, cwd, limit)
    except Exception as exc:  # the child never gets to break the secretary's turn
        logger.warning("plobi_agent_ask: child run failed: %s", exc)
        return tool_error(
            f"问「{_label(entry)}」的时候出了错：{exc}", ok=False, agent=entry.name
        )

    if result["missing_binary"]:
        return tool_error("这台机器上找不到可执行的命令行，问不到任何 Agent。", ok=False)
    if result["timed_out"]:
        # R-089 / R-072：那一趟已经花掉的钱不能凭空消失。`_run_child` 在 kill 之后
        # 已经拿 5 秒 grace 把剩余 stdout 收了回来（`:652-655`），以前到这里就被扔掉——
        # 现在照原样交回，并**明说是半句**，绝不冒充完整回答（没收到就照实说没收到）。
        partial = _tail(result["stdout"])
        detail = (
            f"它已经说出来的部分照原样交回，**这是没说完的半句、不是完整回答**：{partial}"
            if partial else "它一个字都没吐出来（子进程的输出还没落到管道就被收了）。"
        )
        # 这个数是从哪儿来的要说给 L1：按它自己的耗时抬上去过，就说明「它本来就慢」，
        # 原样再问一次是白花一趟钱——这句只有乙档能给，默认值那趟没有历史可说。
        origin = (
            f"（这 {int(limit)} 秒是按它自己最近 {budget.runs} 趟的实际耗时给的，"
            "不是默认值——它本来就慢，原样再问一次多半还是一样。）"
            if budget.runs
            else ""
        )
        # 数的来路决定该建议什么：抬上去过还超时，「再问一次」就是骗它再花一趟钱。
        advice = (
            "要么把问题拆小一点，要么把 `agent_ask.timeout_seconds` 那一个旋钮调高——"
            "它就是这一问现在的下限。"
            if budget.runs
            else "可以再问一次，或把问题问得更具体。"
        )
        return tool_error(
            f"「{_label(entry)}」在 {int(limit)} 秒内没有答完。{origin}{detail}{advice}",
            ok=False,
            agent=entry.name,
            timeout_seconds=limit,
            partial_answer=partial,
        )
    if (result["returncode"] or 0) != 0:
        detail = _tail(result["stderr"]) or _tail(result["stdout"])
        return tool_error(
            f"「{_label(entry)}」这一轮没答上来（退出码 {result['returncode']}）。"
            + (f"它最后说的是：{detail}" if detail else ""),
            ok=False,
            agent=entry.name,
            exit_code=result["returncode"],
        )

    # 同一屏叫醒时那一屏的号是调用方给的，不必再去猜「最新那一条」——_new_session_id 那套
    # 时间地板 + agent_id 的猜测只在没有指定会话时用（旧嘴 `plobi_agent_ask` 那条路）。
    session_id = (resume_session or "").strip() or _new_session_id(entry, not_before=started)
    answer = _session_answer(entry, session_id) or _fallback_answer(result["stdout"])
    answer = answer.strip()
    if not answer:
        return tool_error(
            f"「{_label(entry)}」这一回什么也没说。",
            ok=False,
            agent=entry.name,
            session_id=session_id,
        )
    if len(answer) > _ANSWER_MAX_CHARS:
        answer = answer[:_ANSWER_MAX_CHARS]

    return json.dumps(
        {
            "ok": True,
            "agent": entry.name,
            "answer": answer,
            "session_id": session_id,
            "duration_seconds": round(result["duration"], 1),
        },
        ensure_ascii=False,
    )
