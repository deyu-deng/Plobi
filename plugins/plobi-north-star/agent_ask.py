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
  (name → registry row → its profile + ``project_path``). The model never
  hands out an arbitrary profile name, archived rows are refused, and a
  refusal lists the names that *are* askable.
* **The 分身 runs as a subprocess** built from the shared
  :func:`plobi_cli.plobi_bin.resolve_plobi_argv` recipe plus the dispatcher's
  environment pinning (``PLOBI_HOME`` / ``PLOBI_PROFILE`` from the profile,
  ``TERMINAL_CWD`` pinned to the real project path, ``PLOBI_TUI`` dropped,
  ``--cli --accept-hooks chat -q``).
* **The question is attributed** in the text the child receives, so it knows
  who is asking and why.
* **The record is the child's own session.** It runs inside its own profile, so
  that profile's ``state.db`` already holds the exchange; the answer and the
  session id are read back out of it. No second transcript store.
* **Guards**: a bounded timeout (``config.yaml`` value, hard ceiling in code —
  never a ``PLOBI_*`` env var), ``--yolo`` is never in the argv, and every
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
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

from tools.registry import tool_error

logger = logging.getLogger(__name__)

PLUGIN_KEY = "plobi-north-star"
ASK_TOOLSET = "plobi_north_star"

#: Only project 分身 take questions. ``l2_agenda`` already has its own mouth
#: (``plobi_secretary_ask``) and the default profile *is* the asking secretary.
ASKABLE_ROLE = "l2_project"

#: Bounded wait for the child. ``timeout_seconds`` is a ``config.yaml`` knob
#: (``plugins.entries.plobi-north-star.agent_ask.timeout_seconds``) — behavioural
#: settings go there, never in ``.env`` or a new ``PLOBI_*`` var. The ceiling is
#: code-only on purpose: a bad value must not park the secretary indefinitely.
DEFAULT_TIMEOUT_SECONDS = 180.0
MIN_TIMEOUT_SECONDS = 5.0
MAX_TIMEOUT_SECONDS = 900.0

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
        "当面问一个项目分身一句话，拿回它本人现在的回答。用户问「某个项目现在怎么样」"
        "「这件事你手上是什么情况」时用它——别只读硬盘上那份笔记，那只是上次有人写字那一天"
        "的话，这个分身会自己回答。项目名照用户说的那样填就行（大小写、显示名都认），"
        "问题尽量用他的原话，不要替他改写。这次问答会留在被问的那个分身自己的会话记录里，"
        "回答同时带回它的会话号，方便事后回看。"
        "问不到（名字不对、已经归档、它答不上来、超时）会明确回错，并列出现在能问的项目名——"
        "照实说，不要拿笔记冒充分身的回答。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "description": "要问的那个项目分身的名字。",
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
    """Live project 分身 rows — the ones a question can be addressed to."""
    reg = registry if registry is not None else _load_registry()
    return [
        entry
        for entry in reg.entries()
        if not entry.archived and entry.role == ASKABLE_ROLE
    ]


def _available_names(entries) -> list[str]:
    return sorted({entry.name for entry in entries})


def _label(entry) -> str:
    return ((entry.display_name or "").strip() or entry.name)


def _matches(entry, needle: str) -> bool:
    """Accept the registry key, the display name, or the profile name."""
    names = {entry.name, entry.display_name, entry.profile or entry.name}
    return needle in {name.strip().casefold() for name in names if name}


def _roster_hint(entries) -> str:
    names = _available_names(entries)
    return "、".join(names) if names else "（现在一个都没有）"


def resolve_target(name: str, *, registry=None, entries=None):
    """Map what the model asked for onto exactly one live registry row.

    Raises :class:`AskRefusal` — never a bare ``KeyError``/``AttributeError`` —
    for an unknown name, an archived row, or a row that is not a project 分身,
    always listing the names that *are* askable so the secretary can correct
    itself instead of guessing at an identifier it never saw.
    """
    needle = (name or "").strip().casefold()
    reg = registry if registry is not None else _load_registry()
    if entries is None:
        entries = askable_entries(reg)
    available = _available_names(entries)

    if not needle:
        raise AskRefusal(
            f"要问哪个项目分身？现在能问的有：{_roster_hint(entries)}", available
        )

    hits = [entry for entry in entries if _matches(entry, needle)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        who = "、".join(sorted(_label(entry) for entry in hits))
        raise AskRefusal(
            f"「{name}」对上了好几个分身（{who}），说个更具体的名字。", available
        )

    # Not askable — but is it a row that exists? Archived rows are refused by
    # name (裁定 19: they stay on disk and hide from the default list), and a
    # non-project row belongs to a different mouth.
    others = [entry for entry in reg.entries() if _matches(entry, needle)]
    if any(entry.archived for entry in others):
        raise AskRefusal(
            f"「{name}」已经归档了，问不到它。现在能问的有：{_roster_hint(entries)}",
            available,
        )
    if others:
        raise AskRefusal(
            f"「{name}」不是能当面问的项目分身。现在能问的有：{_roster_hint(entries)}",
            available,
        )
    raise AskRefusal(
        f"没有「{name}」这个项目分身。现在能问的有：{_roster_hint(entries)}", available
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


def build_child_command(entry, prompt: str) -> tuple[list[str], dict[str, str], Optional[str]]:
    """Return ``(argv, env, cwd)`` for one face-to-face question.

    Mirrors ``plobi_cli.kanban_db._default_spawn`` — the same binary resolver and
    the same environment pinning — minus the board variables (this is a
    conversation, not a task handoff) and with **no force-approval flag of any
    kind**: a child that wants to do something dangerous stops and asks, and the
    bounded timeout is what keeps that from eating the secretary's turn.
    """
    from plobi_cli.plobi_bin import resolve_plobi_argv
    from plobi_cli.profiles import resolve_profile_env

    profile = entry.profile_name
    child_env = dict(os.environ)
    try:
        child_env["PLOBI_HOME"] = resolve_profile_env(profile)
    except FileNotFoundError as exc:
        raise AskRefusal(
            f"「{_label(entry)}」还没在这台机器上落地，问不到它。",
            _available_names(askable_entries()),
        ) from exc
    child_env["PLOBI_PROFILE"] = profile

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
    # a ``display.interface: tui``) would send a quiet run into Ink, which bails
    # out on a non-TTY stdout. ``--cli`` is the precedence pin; dropping the env
    # var covers older builds found earlier on PATH.
    child_env.pop("PLOBI_TUI", None)

    argv = [
        *resolve_plobi_argv(),
        "-p",
        profile,
        "--cli",
        # The child runs under a profile-scoped PLOBI_HOME, so it sees that
        # profile's shell-hook allowlist rather than the secretary's; pass it
        # explicitly the same way the dispatcher does for workers.
        "--accept-hooks",
        "chat",
        "-q",
        prompt,
    ]
    return argv, child_env, cwd or None


# ---------------------------------------------------------------------------
# The child's own session IS the record — read the answer back out of it
# ---------------------------------------------------------------------------


def _child_state_db(entry) -> Optional[Path]:
    try:
        from plobi_cli.profiles import get_profile_dir

        return get_profile_dir(entry.profile_name) / "state.db"
    except Exception:
        return None


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
    the answer. The ``not_before`` floor is why the secretary's own (older)
    session row can't be mistaken for the child's.
    """
    db_path = _child_state_db(entry)
    if db_path is None:
        return ""
    rows = _query_rows(
        db_path,
        "SELECT id FROM sessions WHERE started_at >= ? "
        "ORDER BY started_at DESC LIMIT 1",
        (not_before - 2.0,),
    )
    return str(rows[0][0] or "") if rows else ""


def _session_answer(entry, session_id: str) -> str:
    """The child's final answer text, from its own session record."""
    if not session_id:
        return ""
    db_path = _child_state_db(entry)
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


def handle_agent_ask(args: dict, **kwargs) -> str:
    """Ask one project 分身 a question; hand its answer back to the secretary.

    Every branch returns a JSON string — this must never raise into the L1
    conversation loop.
    """
    args = args if isinstance(args, dict) else {}
    name = str(args.get("agent") or "").strip()
    question = str(args.get("question") or "").strip()

    try:
        entries = askable_entries()
    except Exception as exc:  # an unreadable roster ≠ a broken turn
        logger.warning("plobi_agent_ask: registry unreadable: %s", exc)
        return tool_error("读不到项目分身名册，这次问不了。", ok=False)

    try:
        entry = resolve_target(name, entries=entries)
    except AskRefusal as refusal:
        return tool_error(refusal.message, ok=False, available=refusal.available)

    if not question:
        return tool_error(
            f"要问「{_label(entry)}」什么？question 不能为空。", ok=False, agent=entry.name
        )

    limit = timeout_seconds()
    try:
        argv, env, cwd = build_child_command(entry, compose_question(question, entry=entry))
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
        return tool_error("这台机器上找不到可执行的命令行，问不到任何分身。", ok=False)
    if result["timed_out"]:
        return tool_error(
            f"「{_label(entry)}」在 {int(limit)} 秒内没有答完，这次先记下问不到。"
            "可以再问一次，或把问题问得更具体。",
            ok=False,
            agent=entry.name,
            timeout_seconds=limit,
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

    session_id = _new_session_id(entry, not_before=started)
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
