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

#: Openest first guess; override per channel with the env var below.
DEFAULT_PERMISSION_MODE = "dontAsk"

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
    chosen = (mode or os.environ.get(PERMISSION_MODE_ENV, "").strip() or DEFAULT_PERMISSION_MODE)
    if chosen not in PERMISSION_MODES:
        chosen = DEFAULT_PERMISSION_MODE
    return ("-y", f"--permission-mode={chosen}")


def extra_args(agent: str = AGENT_CODEBUDDY) -> tuple[str, ...]:
    env = AGY_EXTRA_ARGS_ENV if agent == AGENT_AGY else EXTRA_ARGS_ENV
    raw = os.environ.get(env, "").strip()
    return tuple(part for part in raw.split() if part) if raw else ()


def is_sandboxed() -> bool:
    """Whether the child really runs isolated. Without a container runtime this
    must stay False, and the report must say "非沙箱放开"."""
    return os.environ.get(SANDBOX_ENV, "").strip().lower() in {"1", "true", "yes"}


def default_workdir() -> Path:
    base = Path(tempfile.gettempdir()) / "plobi-offload" / time.strftime("%Y%m%d")
    base.mkdir(parents=True, exist_ok=True)
    return base


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


def _parse_envelope(stdout: str) -> tuple[str, dict[str, Any]]:
    """Return (answer text, usage dict) from ``--output-format json``.

    The two agents do not share an envelope: agy answers one object, CodeBuddy
    answers a **list** of turn records. Both shapes are handled, and an
    unrecognised one yields empty readings rather than a crash — a channel that
    dies while parsing a finished run loses the evidence for nothing.
    """
    text_out = (stdout or "").strip()
    try:
        payload = json.loads(text_out)
    except json.JSONDecodeError:
        return text_out, {}

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

    return ("\n".join(parts) or text_out), usage


def _cost(usage: dict[str, Any]) -> tuple[Optional[float], str]:
    """Money, or an honest "cannot price". Never 0 for unknown (R-073)."""
    total = usage.get("total_cost_usd")
    if isinstance(total, (int, float)):
        return float(total), "reported_by_agent"
    if not usage:
        return None, "no_usage_reported"
    return None, "usage_reported_without_price"


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
    flags = permission_flags(agent, permission_mode)
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
    if sandboxed:
        env["CODEBUDDY_IS_SANDBOX"] = "1"
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

    text, usage = _parse_envelope(completed.stdout or "")
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

    return OffloadResult(
        agent=agent, cwd=str(workdir), permission_flags=flags, sandboxed=sandboxed,
        exit_code=completed.returncode, duration_s=round(duration, 2), argv=argv,
        usage=usage, cost=cost, cost_status=cost_status, artifacts=artifacts,
        text=text, ok=ok, error=error, produced=produced,
    )
