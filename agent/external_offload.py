"""Offload one real task to a local external agent (WP-EXTERNAL-AGENT-OFFLOAD).

Two rulings govern this module:

* 裁定 99 一 — the child runs on **the agent's own** most-permissive mode. That is
  the point of the offload: the external agent does the work with its own tools
  and its own subscription quota. It is NOT permission to relax Plobi's own
  gates: nothing here touches ``tools.terminal_tool``'s approval thread-local
  (GHSA-qg5c-hvr5-hjgr) or ``pre_tool_call``. When the external agent calls back
  into a Plobi tool over MCP, our gate still asks and still fails closed.
* 裁定 99 四 — no spend ceiling, but every run must answer four things:
  **who did it / in which directory / which permission flags were passed / what
  it cost**. A cost we cannot price is reported as ``None`` with a
  ``cost_status``, never as ``0`` (R-073).

The permission mode comes from config/env so a channel can be dialled back to a
restricted mode; there is deliberately no hardcoded-unoverridable bypass here.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

#: Modes CodeBuddy's own ``--help`` lists. Which one really answers zero prompts
#: is a measured fact, not a reading comprehension result — see the task brief.
PERMISSION_MODES = ("acceptEdits", "bypassPermissions", "default", "plan", "dontAsk", "auto")

#: The default is the mode that was MEASURED to actually do the work, not the one
#: that merely sounds permissive. ``dontAsk`` was the first guess here and it is
#: "do not ask — deny": two product-path runs at 22:2x and 19:0x both exited 0,
#: burned ~114,700 input tokens, wrote zero artifacts, and the agent's own reply
#: was that Write/Bash/PowerShell were denied. ``bypassPermissions`` is the mode
#: that produced real files (``fib.py`` + ``out.txt``) with zero approvals.
DEFAULT_PERMISSION_MODE = "bypassPermissions"

PERMISSION_MODE_ENV = "PLOBI_OFFLOAD_CODEBUDDY_PERMISSION_MODE"
COMMAND_ENV = "PLOBI_OFFLOAD_CODEBUDDY_COMMAND"
EXTRA_ARGS_ENV = "PLOBI_OFFLOAD_CODEBUDDY_EXTRA_ARGS"
SANDBOX_ENV = "PLOBI_OFFLOAD_CODEBUDDY_SANDBOX"

#: agy has no permission-mode ladder — one switch auto-approves everything, the
#: absence of it prompts. Which way a run went is therefore a single boolean, and
#: it still comes from config so a channel can be dialled back to restricted.
AGY_SKIP_ENV = "PLOBI_OFFLOAD_AGY_SKIP_PERMISSIONS"
AGY_COMMAND_ENV = "PLOBI_OFFLOAD_AGY_COMMAND"
AGY_EXTRA_ARGS_ENV = "PLOBI_OFFLOAD_AGY_EXTRA_ARGS"

#: Where an offload child finds the proxy. Deliberately NOT a literal
#: 127.0.0.1:7890 — that is this box's dev-time Clash port and changes with the
#: network. Unset, we fall through to the repo's ONE proxy reader.
PROXY_ENV = "PLOBI_OFFLOAD_PROXY"

AGENT_CODEBUDDY = "codebuddy"
AGENT_AGY = "agy"

#: Where an offload may NOT run: the four repositories on this box.
FORBIDDEN_CWD_NAMES = ("Plobi-Desktop", "Plobi-Harmony-App", "Code", "Docs")


@dataclasses.dataclass(frozen=True)
class OffloadResult:
    """The four required answers, plus the evidence behind them."""

    agent: str
    cwd: str
    permission_flags: tuple[str, ...]
    sandboxed: bool
    exit_code: Optional[int]
    duration_s: float
    argv: tuple[str, ...]
    usage: dict[str, Any]
    cost: Optional[float]
    cost_status: str
    artifacts: tuple[dict[str, Any], ...]
    text: str
    ok: bool
    error: str
    #: Whether the run actually left a file behind — an offload with no product did not work.
    produced: bool = False
    #: Which proxy (if any) the child was given, and where it came from.
    proxy: str = ""
    proxy_source: str = "none"
    #: The厂商 session id, read out of the envelope — "" when the envelope carried none.
    #: `WP-OFFLOAD-SESSION-HANDLE`: without this the入口面③ third level has nothing to
    #: jump back into, because the id was in the回包 all along and our parser dropped it.
    external_session: str = ""
    #: Which key that id came from (`result.session_id` / `sessionId` / `conversation_id` / "").
    external_session_source: str = ""

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _which_prefer_executable(name: str) -> str:
    """Resolve a launcher CreateProcess can actually start.

    A bare PATH hit may be a ``.ps1`` (PowerShell resolves those; ``Popen`` does
    not — that is what made the ACP client's default command unstartable on
    Windows). Prefer ``.exe``/``.cmd``/``.bat`` over an extensionless or script hit.
    """
    for ext in (".cmd", ".exe", ".bat"):
        found = shutil.which(name + ext)
        if found:
            return found
    return shutil.which(name) or ""


def resolve_command(agent: str = AGENT_CODEBUDDY) -> str:
    if agent == AGENT_AGY:
        return os.environ.get(AGY_COMMAND_ENV, "").strip() or _which_prefer_executable("agy")
    override = os.environ.get(COMMAND_ENV, "").strip()
    if override:
        return override
    if agent != AGENT_CODEBUDDY:
        return ""
    return _which_prefer_executable("codebuddy")


def permission_flags(agent: str = AGENT_CODEBUDDY, mode: Optional[str] = None) -> tuple[str, ...]:
    """The permissive flags for one run, taken from config — never hardcoded on.

    CodeBuddy's ``-y`` alone does not satisfy "zero human approval": its own help
    says HIGH/CRITICAL still prompt. The mode is the lever, and it stays
    dial-downable. agy has no ladder — one switch, also config-gated.
    """
    if agent == AGENT_AGY:
        skip = os.environ.get(AGY_SKIP_ENV, "1").strip().lower()
        return ("--dangerously-skip-permissions",) if skip in {"1", "true", "yes"} else ()
    if agent != AGENT_CODEBUDDY:
        return ()
    requested = (mode or os.environ.get(PERMISSION_MODE_ENV, "").strip())
    if not requested:
        requested = DEFAULT_PERMISSION_MODE
    if requested not in PERMISSION_MODES:
        # Never fall back to a default here: the default is now the most open
        # mode, so silently substituting it would turn a typo'd env value into a
        # full bypass.
        raise ValueError(
            f"unknown CodeBuddy permission mode {requested!r}; "
            f"refusing to guess (advertised: {', '.join(PERMISSION_MODES)})"
        )
    return ("-y", f"--permission-mode={requested}")


def extra_args(agent: str = AGENT_CODEBUDDY) -> tuple[str, ...]:
    env = AGY_EXTRA_ARGS_ENV if agent == AGENT_AGY else EXTRA_ARGS_ENV
    raw = os.environ.get(env, "").strip()
    return tuple(part for part in raw.split() if part) if raw else ()


def resolve_proxy() -> tuple[str, str]:
    """Return ``(proxy_url, source_label)`` for an offload child.

    Reads the explicit knob first, then the repo's ONE proxy resolver
    (``agent.process_bootstrap._get_proxy_for_base_url`` — it already honours
    HTTPS_PROXY/HTTP_PROXY/ALL_PROXY, their lowercase forms and NO_PROXY).
    Nothing here re-implements that order: a second narrow reader is how a
    proxy fix silently stops applying to one caller (裁定 94).
    """
    explicit = os.environ.get(PROXY_ENV, "").strip()
    if explicit:
        return explicit, PROXY_ENV
    try:
        from agent.process_bootstrap import _get_proxy_for_base_url

        found = _get_proxy_for_base_url(None)
        if found:
            return found, "process env (shared resolver)"
    except Exception:  # noqa: BLE001 - no resolver means "no proxy", not "no run"
        pass
    return "", "none"


def is_sandboxed() -> bool:
    """Whether the child really runs isolated. Without a container runtime this
    must stay False, and the report must say "非沙箱放开"."""
    return os.environ.get(SANDBOX_ENV, "").strip().lower() in {"1", "true", "yes"}


def default_workdir() -> Path:
    """Where an offload lands when the caller names no cwd.

    NOT ``tempfile.gettempdir()``: on this box that is ``C:\\Users\\...\\Temp``,
    and ``D:\\_POLICY`` forbids letting run data land on C.  ``PLOBI_HOME`` is the
    policy-compliant scratch root and is already profile-aware, so an offload's
    product lands next to the rest of its data.  Temp is only the fallback for a
    machine where that root cannot be created.
    """
    day = time.strftime("%Y%m%d")
    try:
        from plobi_constants import get_plobi_home

        base = get_plobi_home() / "offload" / day
        base.mkdir(parents=True, exist_ok=True)
        return base
    except Exception:  # noqa: BLE001 - unreadable home must not kill the run
        base = Path(tempfile.gettempdir()) / "plobi-offload" / day
        base.mkdir(parents=True, exist_ok=True)
        return base


def egress_is_reachable(proxy_url: str, timeout: float = 3.0) -> tuple[bool, str]:
    """Can we actually open a socket to the exit we are about to hand the child?

    This is checked BEFORE the child spawns because the child cannot be trusted to
    report it honestly: with a dead proxy, ``agy`` exits 1 saying "Please sign in to
    view available models" — a credential-sounding message that would send a reader
    to re-login instead of to the engine that is not listening.  A TCP connect is
    enough: it separates "nothing is listening there" from "the task failed later",
    and it costs nothing.
    """
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(proxy_url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme in ("https",) else 1080)
    if not host:
        return False, f"egress {proxy_url!r} has no host to connect to"
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, ""
    except OSError as exc:
        return False, f"egress {proxy_url!r} is not reachable ({type(exc).__name__}: {exc})"


def project_egress_onto(env: dict[str, str]) -> str:
    """Let the ONE routing table own the child's network keys.

    Returns a source label, or ``""`` when the caller declared no table (then the
    inherited environment stands and nothing here pretends to have decided).
    This matters because the table can say "these hosts go direct, everything
    else via the proxy", and hand-writing three proxy keys both drops that bypass
    list and leaves a stale inherited ``NO_PROXY`` in front of it — which is a
    routing answer the child cannot audit after the fact.
    """
    try:
        from agent.egress.env_projection import apply_egress_env
        from agent.egress.rules import load_table
    except Exception:  # noqa: BLE001 - no egress module means no table to honour
        return ""
    try:
        table = load_table()
    except Exception as exc:  # a malformed table must not become "no routing"
        raise ValueError(f"network.egress table is unreadable, refusing to guess routing: {exc}") from exc
    # Two different states, and only one of them means "no opinion":
    # `Table.unreadable` is set when the config could not be READ at all (the
    # spawn path deliberately stays inert there — a broken config pipe must not
    # take the terminal down), while an empty table list means the user declared
    # nothing.  An offload that spends real quota refuses in both unreadable
    # cases; the inherited environment is not a routing decision anyone made.
    unreadable = getattr(table, "unreadable", "")
    if unreadable:
        raise ValueError(
            f"network.egress could not be read ({unreadable}) — refusing to run an external "
            "agent with no routing decided; fix the config (or set "
            f"{PROXY_ENV}) and run again"
        )
    if not getattr(table, "active", False):
        return ""
    apply_egress_env(env, table)
    return "network.egress"


def cwd_is_allowed(path: Path | str) -> tuple[bool, str]:
    """Refuse to point an offload at one of the four repos (裁定 99 二.3)."""
    resolved = Path(path).resolve()
    for parent in (resolved, *resolved.parents):
        if parent.name in FORBIDDEN_CWD_NAMES:
            return False, f"refusing to offload inside the repository tree: {parent}"
    if not resolved.is_dir():
        return False, f"cwd does not exist: {resolved}"
    return True, ""


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()[:12]


def _collect_artifacts(root: Path, before: dict[str, float]) -> tuple[dict[str, Any], ...]:
    out = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.startswith(".offload-"):
            # Our own audit files are evidence about the run, not products of it.
            continue
        mtime = path.stat().st_mtime
        if path.name not in before or mtime > before[path.name]:
            with contextlib.suppress(OSError):
                out.append({"path": str(path), "bytes": path.stat().st_size, "sha256_12": _digest(path)})
    return tuple(out)


def _keep_evidence(root: Path, completed: "subprocess.CompletedProcess[str]") -> None:
    """Persist the raw envelope so a finished run can be re-read without paying again."""
    with contextlib.suppress(OSError):
        (root / ".offload-envelope.json").write_text(completed.stdout or "", encoding="utf-8")
        (root / ".offload-stderr.txt").write_text(completed.stderr or "", encoding="utf-8")


def _session_handle(records: list[dict[str, Any]]) -> tuple[str, str]:
    """The external session id plus **which key it was read from**.

    Contract from ``Docs/tasks/offload-session-handle-task.md``: CodeBuddy answers a
    record array where the process records carry ``sessionId`` and only the final
    ``type == "result"`` record carries ``session_id``; agy answers one object with
    ``conversation_id``. The ``result`` record wins because it is the厂商 closing the
    turn, and when the two disagree we say so in the source cell
    (``result.session_id!=sessionId``) instead of quietly picking one — a frontend that
    has to build a resume command needs to know the ids did not agree.

    Reading nothing is a reading: ``("", "")``. Never a placeholder.
    """
    def _str(record: dict[str, Any], key: str) -> str:
        value = record.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else ""

    result_id = ""
    for record in reversed(records):
        if record.get("type") == "result" and (found := _str(record, "session_id")):
            result_id = found
            break

    process_id = ""
    for record in records:
        if (found := _str(record, "sessionId")):
            process_id = found
            break

    if result_id:
        if process_id and process_id != result_id:
            return result_id, "result.session_id!=sessionId"
        return result_id, "result.session_id"
    if process_id:
        return process_id, "sessionId"

    for record in records:
        if (found := _str(record, "conversation_id")):
            return found, "conversation_id"

    return "", ""


def _parse_envelope(stdout: str) -> tuple[str, dict[str, Any], str, str]:
    """Return (answer text, usage dict, external session id, source key).

    The two agents do not share an envelope: agy answers one object, CodeBuddy
    answers a **list** of turn records. Both shapes are handled, and an
    unrecognised one yields empty readings rather than a crash — a channel that
    dies while parsing a finished run loses the evidence for nothing.
    """
    text_out = (stdout or "").strip()
    try:
        payload = json.loads(text_out)
    except json.JSONDecodeError:
        return text_out, {}, "", ""

    records = payload if isinstance(payload, list) else [payload]
    records = [r for r in records if isinstance(r, dict)]

    usage: dict[str, Any] = {}
    parts: list[str] = []
    for record in records:
        if isinstance(record.get("usage"), dict):
            usage = record["usage"]
        for key in ("result", "text", "response"):
            value = record.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value)
        error = record.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            parts.append(f"[agent error] {error['message']}")

    return ("\n".join(parts) or text_out), usage, *_session_handle(records)


def _cost(usage: dict[str, Any]) -> tuple[Optional[float], str]:
    """Money, or an honest "cannot price". Never 0 for unknown (R-073)."""
    total = usage.get("total_cost_usd")
    if isinstance(total, (int, float)):
        return float(total), "reported_by_agent"
    if not usage:
        return None, "no_usage_reported"
    return None, "usage_reported_without_price"


_UNREACHABLE_MARKERS = (
    "dial tcp", "connectex", "connection attempt failed", "timed out",
    "timeout", "no such host", "unreachable", "econnrefused", "connection refused",
)


def _looks_unreachable(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in _UNREACHABLE_MARKERS)


def main(argv: Optional[list[str]] = None) -> int:
    """``python -m agent.external_offload --task "..." [--agent agy] [--cwd DIR]``

    The product-side entry the L2 offload call site uses. A scratch script under
    the temp dir is not a product path: it can pass while the real call site is
    broken, which is exactly how the proxy gap stayed invisible.
    """
    import argparse

    parser = argparse.ArgumentParser(description="Offload one task to a local external agent")
    parser.add_argument("--task", required=True)
    parser.add_argument("--agent", default=AGENT_CODEBUDDY, choices=(AGENT_CODEBUDDY, AGENT_AGY))
    parser.add_argument("--cwd", default="")
    parser.add_argument("--permission-mode", default="")
    parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args(argv)

    result = run_task(
        args.task,
        cwd=args.cwd or None,
        agent=args.agent,
        permission_mode=args.permission_mode or None,
        timeout=args.timeout,
    )
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return 0 if result.ok else 1


def run_task(
    task: str,
    *,
    cwd: Optional[Path | str] = None,
    agent: str = AGENT_CODEBUDDY,
    permission_mode: Optional[str] = None,
    timeout: float = 900.0,
) -> OffloadResult:
    """Hand one real task to a local external agent and report the four answers."""
    workdir = Path(cwd).resolve() if cwd else default_workdir()
    allowed, why = cwd_is_allowed(workdir)
    try:
        flags = permission_flags(agent, permission_mode)
    except ValueError as exc:
        return OffloadResult(
            agent=agent, cwd=str(workdir), permission_flags=(), sandboxed=is_sandboxed(),
            exit_code=None, duration_s=0.0, argv=(), usage={}, cost=None,
            cost_status="not_started", artifacts=(), text="", ok=False, error=str(exc),
        )
    sandboxed = is_sandboxed()

    def fail(message: str) -> OffloadResult:
        return OffloadResult(
            agent=agent, cwd=str(workdir), permission_flags=flags, sandboxed=sandboxed,
            exit_code=None, duration_s=0.0, argv=(), usage={}, cost=None,
            cost_status="not_started", artifacts=(), text="", ok=False, error=message,
        )

    if not allowed:
        return fail(why)
    binary = resolve_command(agent)
    if not binary:
        return fail(f"external agent {agent!r} has no drivable command on this machine")

    argv = (binary, "-p", task, "--output-format", "json", *flags, *extra_args(agent))
    env = dict(os.environ)
    proxy, proxy_source = resolve_proxy()
    knob = bool(os.environ.get(PROXY_ENV, "").strip())
    if proxy and knob:
        # An explicit per-channel override wins outright — including over a
        # declared table, because "I named this exit for this channel" is the
        # user speaking louder than the routing config.
        for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
                    "https_proxy", "http_proxy", "all_proxy"):
            env[key] = proxy
    elif not knob:
        try:
            projected = project_egress_onto(env)
        except ValueError as exc:
            return fail(str(exc))
        if projected:
            proxy = env.get("HTTPS_PROXY", "") or env.get("https_proxy", "")
            proxy_source = projected
        elif proxy:
            for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
                        "https_proxy", "http_proxy", "all_proxy"):
                env[key] = proxy
    if sandboxed:
        env["CODEBUDDY_IS_SANDBOX"] = "1"
    # Ask before spending: a dead exit must not turn into a child that reports
    # something else (agy says "Please sign in") and a task that burns tokens.
    if proxy:
        reachable, why = egress_is_reachable(proxy)
        if not reachable:
            return fail(why)
    before = {p.name: p.stat().st_mtime for p in workdir.rglob("*") if p.is_file()}

    started = time.time()
    try:
        completed = subprocess.run(  # noqa: S603 — argv is built above from config
            list(argv), cwd=str(workdir), env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return OffloadResult(
            agent=agent, cwd=str(workdir), permission_flags=flags, sandboxed=sandboxed,
            exit_code=None, duration_s=time.time() - started, argv=argv, usage={},
            cost=None, cost_status="unknown", artifacts=(), text="", ok=False,
            error=f"external agent did not finish within {timeout:.0f}s",
        )
    except OSError as exc:
        return fail(f"cannot start {binary}: {exc}")
    duration = time.time() - started

    text, usage, external_session, external_session_source = _parse_envelope(completed.stdout or "")
    cost, cost_status = _cost(usage)
    artifacts = _collect_artifacts(workdir, before)
    _keep_evidence(workdir, completed)
    # An offload that exits 0 but leaves nothing behind did not do the job.
    # Measured on this box: codebuddy with `-y --permission-mode=dontAsk` returns
    # exit 0 after 53s and 114k input tokens, having written nothing, because its
    # Write/Bash tools are disabled in print mode — it never asked, because it
    # never could. Reading that as success would silently ship a dead channel.
    produced = bool(artifacts)
    ok = completed.returncode == 0 and bool(text or produced)
    error = ""
    if completed.returncode != 0:
        error = ((completed.stderr or "").strip()[:400] or f"exit={completed.returncode}")
    elif not produced:
        error = "exit 0 but no artifact was written — the agent did not actually do the work"
        ok = False

    if _looks_unreachable(error):
        # Measured: agy is a Go binary that dials straight out. With no proxy in
        # the environment and DNS handing back a Clash fake-IP (198.18.0.0/15),
        # it dies on oauth2 with a connect error. Saying only "exit=1" sends the
        # next reader chasing a login or a quota problem that does not exist.
        hint = (
            "network unreachable from the offload child"
            + ("" if proxy else f" — no proxy resolved; set {PROXY_ENV} or export HTTPS_PROXY")
            + f" ({error[:160]})"
        )
        error = hint
        ok = False

    return OffloadResult(
        agent=agent, cwd=str(workdir), permission_flags=flags, sandboxed=sandboxed,
        exit_code=completed.returncode, duration_s=round(duration, 2), argv=argv,
        usage=usage, cost=cost, cost_status=cost_status, artifacts=artifacts,
        text=text, ok=ok, error=error, produced=produced,
        proxy=proxy, proxy_source=proxy_source,
        external_session=external_session, external_session_source=external_session_source,
    )


if __name__ == "__main__":
    raise SystemExit(main())
