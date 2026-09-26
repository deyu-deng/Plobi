"""Soft-route acceptance for the two §8.2 utterances (A line).

Does not open the desktop. Default live path is ``hermes -z`` against the
active profile (no new REST). Unit tests mock ``send_turn``.

    python scripts/plobi/e2e_secretary.py --json
    python scripts/plobi/e2e_secretary.py --repeat 10 --json
    python scripts/plobi/e2e_secretary.py --direct --json   # no hermes -z; real chatlog + aigw
    python scripts/plobi/e2e_secretary.py --no-live   # score helpers only

Exit: 0 pass · 1 assertion/routing fail · 2 could not run (env/model).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
ASK_TOOL = "plobi_secretary_ask"
FORBIDDEN_L1 = frozenset({"terminal", "session_search", "code_execution", "computer_use"})
N3_MARKERS = ("【L1 派工简报】", "intent: refresh_agenda", "intent: write_briefing")

UTTERANCES: tuple[tuple[str, str], ...] = (
    ("明天的日常安排是什么", "refresh_agenda"),
    ("根据明天的日程写一段早报", "write_briefing"),
)

# WP-SEC-VOCAB: read + decide direct cases. Both answer from the shared agenda
# store (no chatlog, no L2 spawn, no N3). The decide case dismisses the row it
# seeds, so a run leaves nothing behind in the real agenda DB.
E2E_PENDING_TITLE = "[e2e] 秘书待确认用例"
DIRECT_READS: tuple[tuple[str, str, dict[str, Any]], ...] = (
    ("今天有什么安排", "query_agenda", {"range": "today"}),
    (
        f"把「{E2E_PENDING_TITLE}」忽略掉",
        "decide_pending",
        {"decision": "dismiss", "title": E2E_PENDING_TITLE},
    ),
    # WP-PROJECT-PORTFOLIO: project_status answers from the Mind vault (no
    # chatlog, no L2 spawn); plan_day uses the shared planner. Both must
    # return ``ok=true`` even when chatlog is dead.
    ("各项目怎么样了", "project_status", {}),
    ("排一下明天", "plan_day", {}),
)


@dataclass
class TurnScore:
    utterance: str
    intent: str
    asked: bool
    forbidden: list[str] = field(default_factory=list)
    route: str | None = None
    dead_honest: bool | None = None
    n3: bool | None = None
    has_events: bool | None = None
    ask_ok: bool | None = None
    tools: list[str] = field(default_factory=list)
    error: str = ""
    direct: bool = False

    @property
    def ok(self) -> bool:
        if self.error:
            return False
        if self.direct:
            if self.intent in {"query_agenda", "decide_pending", "project_status", "plan_day"}:
                return bool(self.ask_ok)
            if self.intent == "refresh_agenda":
                return bool(self.dead_honest) or bool(self.has_events) or bool(self.ask_ok)
            if self.intent == "write_briefing":
                if self.dead_honest:
                    return True
                return self.route in {"workbuddy", "fallback"}
            return False
        if not self.asked:
            return False
        if self.forbidden:
            return False
        if self.intent == "write_briefing" and self.route not in {"workbuddy", "fallback", None}:
            # None = live model did not return parseable JSON; still require the tool.
            pass
        if self.dead_honest is False:
            return False
        return True


def collect_tool_names(messages: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for row in messages:
        tool_name = row.get("tool_name") or row.get("name")
        if tool_name:
            names.append(str(tool_name))
        calls = row.get("tool_calls")
        if isinstance(calls, str):
            try:
                calls = json.loads(calls)
            except json.JSONDecodeError:
                calls = []
        if isinstance(calls, list):
            for call in calls:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function") if isinstance(call.get("function"), dict) else call
                name = (fn or {}).get("name") or call.get("name")
                if name:
                    names.append(str(name))
        content = str(row.get("content") or "")
        if ASK_TOOL in content:
            names.append(ASK_TOOL)
    return names


def infer_intent_from_tools_or_text(messages: list[dict[str, Any]], expected: str) -> str | None:
    blob = json.dumps(messages, ensure_ascii=False)
    if f'"intent": "{expected}"' in blob or f"intent={expected}" in blob:
        return expected
    if expected == "refresh_agenda" and "refresh_agenda" in blob:
        return expected
    if expected == "write_briefing" and "write_briefing" in blob:
        return expected
    return None


def extract_route(messages: list[dict[str, Any]], stdout: str = "") -> str | None:
    blob = json.dumps(messages, ensure_ascii=False) + "\n" + stdout
    if "workbuddy" in blob and "route" in blob:
        if '"route": "workbuddy"' in blob or "route=workbuddy" in blob or '"route":"workbuddy"' in blob:
            return "workbuddy"
    if "fallback" in blob and "route" in blob:
        if '"route": "fallback"' in blob or "route=fallback" in blob or '"route":"fallback"' in blob:
            return "fallback"
    return None


def dead_is_honest(messages: list[dict[str, Any]], stdout: str = "") -> bool | None:
    blob = (json.dumps(messages, ensure_ascii=False) + "\n" + stdout).lower()
    if "dead" in blob and ("true" in blob) and (
        '"dead": true' in blob or '"dead":true' in blob or '\\"dead\\": true' in blob or '\\"dead\\":true' in blob
    ):
        return True
    if "chatlog" in blob and any(word in blob for word in ("dead", "unreachable", "unavailable", "采集不通")):
        return True
    if "编造" in blob:
        return False
    return None


def n3_present(messages: list[dict[str, Any]], user_text: str) -> bool:
    texts = [str(row.get("content") or "") for row in messages]
    joined = "\n".join(texts)
    return user_text in joined and any(marker in joined for marker in N3_MARKERS)


def _agenda_has_events(payload: dict[str, Any]) -> bool:
    agenda = payload.get("agenda")
    if not isinstance(agenda, dict):
        return False
    events = agenda.get("events") or agenda.get("tomorrow_events") or []
    return isinstance(events, list) and len(events) > 0


def score_direct_turn(
    utterance: str,
    intent: str,
    payload: dict[str, Any],
    *,
    n3_messages: list[dict[str, Any]] | None = None,
) -> TurnScore:
    """Score a live ``run_secretary_ask`` payload (no hermes -z)."""
    stdout = json.dumps(payload, ensure_ascii=False)
    dead = bool(payload.get("dead"))
    has_events = _agenda_has_events(payload)
    route = payload.get("route") if intent == "write_briefing" else None
    if isinstance(route, str):
        route = route.strip() or None
    n3 = n3_present(n3_messages, utterance) if n3_messages is not None else None
    dead_honest = True if dead else dead_is_honest([], stdout)
    ask_ok = bool(payload.get("ok")) if payload else False
    error = ""
    if not payload:
        error = "empty secretary payload"
    elif intent in {"query_agenda", "decide_pending", "project_status", "plan_day"} and not ask_ok:
        error = payload.get("error") or f"{intent} did not answer"
    elif intent == "refresh_agenda" and not dead and not has_events and not ask_ok:
        error = payload.get("error") or "neither dead-honest nor events"
    elif intent == "write_briefing" and not dead and route not in {"workbuddy", "fallback"}:
        error = f"route={route!r} not in {{workbuddy, fallback}}"
    elif ask_ok and n3_messages is not None and n3 is False:
        error = "N3 missing from L2 state.db"
    return TurnScore(
        utterance=utterance,
        intent=intent,
        asked=True,
        route=route,
        dead_honest=dead_honest,
        n3=n3,
        has_events=has_events,
        ask_ok=ask_ok,
        tools=[ASK_TOOL],
        error=error,
        direct=True,
    )


def score_turn(
    utterance: str,
    intent: str,
    messages: list[dict[str, Any]],
    *,
    stdout: str = "",
    n3_messages: list[dict[str, Any]] | None = None,
) -> TurnScore:
    tools = collect_tool_names(messages)
    asked = ASK_TOOL in tools or ASK_TOOL in stdout
    inferred = infer_intent_from_tools_or_text(messages, intent)
    if inferred:
        asked = True
    forbidden = sorted({name for name in tools if name in FORBIDDEN_L1})
    route = extract_route(messages, stdout) if intent == "write_briefing" else None
    n3 = n3_present(n3_messages, utterance) if n3_messages is not None else None
    return TurnScore(
        utterance=utterance,
        intent=intent,
        asked=asked,
        forbidden=forbidden,
        route=route,
        dead_honest=dead_is_honest(messages, stdout),
        n3=n3,
        tools=tools,
    )


def hermes_home() -> Path:
    val = (os.environ.get("HERMES_HOME") or "").strip()
    if val:
        return Path(val)
    try:
        from hermes_constants import get_hermes_home

        return get_hermes_home()
    except Exception:
        return Path.home() / ".hermes"


def _load_messages_from_db(db_path: Path, since_unix: float | None = None) -> list[dict[str, Any]]:
    if not db_path.is_file():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "messages" not in tables:
            return []
        sql = "SELECT * FROM messages"
        params: tuple[Any, ...] = ()
        cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
        if since_unix is not None and "timestamp" in cols:
            sql += " WHERE timestamp >= ?"
            params = (since_unix,)
        sql += " ORDER BY id ASC" if "id" in cols else ""
        return [dict(row) for row in conn.execute(sql, params)]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def default_state_db(home: Path | None = None) -> Path:
    root = home or hermes_home()
    return root / "state.db"


def agenda_state_db(home: Path | None = None) -> Path:
    root = home or hermes_home()
    profile = "l2-agenda"
    try:
        from plobi.agents.registry import find_agenda_agent

        entry = find_agenda_agent()
        if entry is not None:
            profile = entry.profile_name
    except Exception:
        pass
    try:
        from hermes_cli.profiles import get_profile_dir

        return get_profile_dir(profile) / "state.db"
    except Exception:
        return root / "profiles" / profile / "state.db"


def send_turn_oneshot(text: str, *, timeout: int = 180) -> str:
    env = os.environ.copy()
    home = hermes_home()
    env.setdefault("HERMES_HOME", str(home))
    cmd = [sys.executable, "-m", "hermes_cli", "-z", text]
    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if proc.returncode != 0 and not out.strip():
        raise RuntimeError(f"hermes -z exited {proc.returncode}")
    return out


def run_direct_pair(
    ask: Callable[..., dict[str, Any]] | None = None,
) -> list[TurnScore]:
    """Call ``run_secretary_ask`` twice — real chatlog + aigw, no hermes -z."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    if ask is None:
        from plobi.agents.registry import run_secretary_ask as ask
    home = hermes_home()
    l2_db = agenda_state_db(home)
    scores: list[TurnScore] = []
    for utterance, intent in UTTERANCES:
        started = time.time()
        try:
            payload = ask(intent, utterance)
            if not isinstance(payload, dict):
                payload = {"ok": False, "error": f"non-dict payload: {payload!r}"}
            err = ""
        except Exception as exc:
            payload = {}
            err = str(exc)
        l2_msgs = _load_messages_from_db(l2_db, since_unix=started - 2)
        score = score_direct_turn(utterance, intent, payload, n3_messages=l2_msgs)
        if err:
            score.error = err
        scores.append(score)
    return scores


def _seed_e2e_pending() -> str:
    """预置一条待确认（决定用例的靶子），返回它的 id。

    ``source`` 只能用冻结词表里的来源；这条是**一次运行内创建并忽略掉**的临时行
    （无 ``prev_value`` → dismiss 真删），不会在真实日程库里留痕。
    """
    from datetime import datetime, timedelta

    from plobi.agenda.service import AgendaService

    day = datetime.now().date() + timedelta(days=1)
    start_at = datetime.combine(day, datetime.min.time()).replace(hour=10).isoformat()
    service = AgendaService()
    return service.ingest_candidate(
        title=E2E_PENDING_TITLE, start_at=start_at, source="wechat"
    ).event.id


def run_direct_reads(
    ask: Callable[..., dict[str, Any]] | None = None,
) -> list[TurnScore]:
    """WP-SEC-VOCAB direct cases: query today + decide the preset pending row.

    Both are store reads — a dead collector must not matter. The decide case
    dismisses the seeded row (no ``prev_value`` → real delete), so the run
    leaves no residue in the agenda DB.
    """
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    if ask is None:
        from plobi.agents.registry import run_secretary_ask as ask

    try:
        _seed_e2e_pending()
        seed_error = ""
    except Exception as exc:
        seed_error = f"seed failed: {exc}"

    scores: list[TurnScore] = []
    for utterance, intent, kwargs in DIRECT_READS:
        try:
            payload = ask(intent, utterance, **kwargs)
            if not isinstance(payload, dict):
                payload = {"ok": False, "error": f"non-dict payload: {payload!r}"}
            err = seed_error
        except Exception as exc:
            payload = {}
            err = str(exc)
        score = score_direct_turn(utterance, intent, payload)
        if err:
            score.error = err
        scores.append(score)
    return scores


def run_pair(
    send_turn: Callable[[str], str] | None = None,
    *,
    live: bool = False,
) -> list[TurnScore]:
    sender = send_turn or (send_turn_oneshot if live else None)
    if sender is None:
        raise RuntimeError("no send_turn; pass a mock or --live")
    home = hermes_home()
    l1_db = default_state_db(home)
    l2_db = agenda_state_db(home)
    scores: list[TurnScore] = []
    for utterance, intent in UTTERANCES:
        started = time.time()
        try:
            stdout = sender(utterance)
            err = ""
        except Exception as exc:
            stdout = ""
            err = str(exc)
        l1_msgs = _load_messages_from_db(l1_db, since_unix=started - 2)
        l2_msgs = _load_messages_from_db(l2_db, since_unix=started - 2)
        score = score_turn(utterance, intent, l1_msgs, stdout=stdout, n3_messages=l2_msgs or None)
        if err:
            score.error = err
        scores.append(score)
    return scores


def summarize(
    runs: list[list[TurnScore]],
    *,
    extra: list[TurnScore] | None = None,
) -> dict[str, Any]:
    total = len(runs)
    hits = 0
    for pair in runs:
        if len(pair) == 2 and all(turn.ok for turn in pair):
            hits += 1
    asked = sum(1 for pair in runs for turn in pair if turn.asked)
    possible = total * 2
    summary: dict[str, Any] = {
        "repeat": total,
        "pair_pass": hits,
        "pair_rate": (hits / total) if total else 0.0,
        "ask_hits": asked,
        "ask_rate": (asked / possible) if possible else 0.0,
        "runs": [[asdict(turn) for turn in pair] for pair in runs],
    }
    if extra:
        summary["direct_reads"] = [asdict(turn) for turn in extra]
        summary["direct_reads_pass"] = sum(1 for turn in extra if turn.ok)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plobi secretary soft-route e2e")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--live", action="store_true", default=True)
    parser.add_argument("--no-live", action="store_true")
    parser.add_argument(
        "--direct",
        action="store_true",
        help="skip hermes -z; call run_secretary_ask (real chatlog + aigw)",
    )
    args = parser.parse_args(argv)
    if args.no_live and not args.direct:
        print(json.dumps({"skip": "no-live", "utterances": [u for u, _ in UTTERANCES]}, ensure_ascii=False))
        return 0
    runs: list[list[TurnScore]] = []
    extra: list[TurnScore] = []
    try:
        for _ in range(max(1, args.repeat)):
            if args.direct:
                runs.append(run_direct_pair())
            else:
                runs.append(run_pair(live=True))
        if args.direct:
            extra = run_direct_reads()
    except Exception as exc:
        payload = {"error": str(exc), "exit": 2}
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else f"e2e cannot run: {exc}")
        return 2
    summary = summarize(runs, extra=extra)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(
            f"pair {summary['pair_pass']}/{summary['repeat']} "
            f"ask {summary['ask_hits']}/{summary['repeat'] * 2} "
            f"rate={summary['ask_rate']:.0%}"
        )
        for pair in runs:
            for turn in pair:
                flag = "ok" if turn.ok else "FAIL"
                print(f"  [{flag}] {turn.intent} asked={turn.asked} tools={turn.tools} route={turn.route} n3={turn.n3} {turn.error}")
        for turn in extra:
            flag = "ok" if turn.ok else "FAIL"
            print(f"  [{flag}] {turn.intent} asked={turn.asked} tools={turn.tools} {turn.error}")
    if summary["pair_pass"] < summary["repeat"]:
        return 1
    if any(not turn.ok for turn in extra):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
