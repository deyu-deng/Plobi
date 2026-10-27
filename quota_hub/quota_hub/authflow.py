"""Click-to-browser sign-in for every leg Plobi can route through.

Why this exists: the user's ruling (2026-10-26) is that **nothing he is asked to do
may require a terminal** — a leg is either already signed in (CodeBuddy / Cursor ride
his local session) or its 连接 button opens his browser. Before this, the only
browser path was the Antigravity PKCE flow, wired through a hardcoded `if` chain in
`electron/main.ts`; every other leg answered `auth not implemented for '<app>'`, and
the CLI families (Cline / Kiro / OpenCode / Gemini / Qwen) each own their OAuth in
their *own* login command, which prints a URL and waits.

The rule this module follows: **the CLI owns its credential.** We run its login
command, find the URL it printed, hand that URL to the browser, and wait for the
process to finish. We never read, copy, print or persist the token it writes — the
same boundary `CliProvider` keeps for chat.

Two kinds of row come out of this:

  * legs the hub aggregates (providers in the registry) — their plan is declared on
    the Provider itself, so this file never learns a product name;
  * legs Plobi drives as an ACP/CLI process but does not aggregate quota for
    (`gemini`, `qwen`) — declared once here, in `LOGIN_ONLY_TARGETS`, because a
    command shape has to be written down somewhere and this is the only place that
    should say it.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
import webbrowser

from . import login_ledger

# A login URL, as CLIs actually print it. Trailing punctuation a sentence ends with
# is dropped below rather than here: `https://example.com/login.` must not lose its
# last path character to a period that is part of the sentence.
_URL_RE = re.compile(r"https?://[^\s<>'\"]+")

#: Legs with no hub provider, listed by the binary the user already installed and the
#: argv that starts its sign-in. Verified on this machine 2026-10-26 from each CLI's
#: own `--help` — nothing here is guessed:
#:   cline  -> `cline auth [options] [provider]`
#:   kiro   -> `kiro-cli login [OPTIONS]`
#:   opencode -> `opencode auth <subcommand>` (subcommand shape NOT verified → absent)
#: Codex and Claude Code are not installed on this machine, so their argv is NOT
#: declared here either: an unverified argv would open a browser to the wrong thing
#: and read as a working button.
LOGIN_ONLY_TARGETS: dict[str, dict] = {
    "gemini-cli": {
        "name": "Gemini CLI",
        "binary": "gemini",
        # `gemini` has no `auth`/`login` subcommand (verified from --help): its first
        # interactive run performs the Google sign-in and opens the browser itself.
        # `--reauth` forces that path again.
        "argv": ["--reauth"],
    },
    "qwen-cli": {
        "name": "Qwen Code",
        "binary": "qwen",
        # qwen's OAuth tier is selected by auth type; `qwen auth` is marked removed.
        "argv": ["--auth-type=qwen-oauth"],
    },
}


def _resolve_binary(name: str) -> str | None:
    """Absolute path to an installed CLI, or None.

    Windows matters here: CreateProcess only appends `.exe`, so a bare npm name
    (`gemini` → `gemini.CMD`) cannot be spawned. Resolve through PATH instead of
    passing the name along — the same reason `agent/copilot_acp_client._spawnable`
    exists.
    """
    if not name:
        return None
    return shutil.which(name)


class LoginSession:
    """One in-flight sign-in for one leg. Deliberately holds no token."""

    def __init__(self, app_id: str, name: str, kind: str) -> None:
        self.app_id = app_id
        self.name = name
        self.kind = kind
        self.state = "pending"  # pending | ok | failed | unsupported
        self.url = ""
        self.detail = ""
        self.started_at = time.time()
        self.finished_at = 0.0
        self.task: asyncio.Task | None = None

    def as_dict(self) -> dict:
        return {
            "app": self.app_id,
            "name": self.name,
            "kind": self.kind,
            "state": self.state,
            "url": self.url,
            "detail": self.detail,
            "started_at": int(self.started_at),
            "finished_at": int(self.finished_at),
        }


class LoginManager:
    def __init__(self) -> None:
        self._sessions: dict[str, LoginSession] = {}

    def get(self, app_id: str) -> LoginSession | None:
        return self._sessions.get(app_id)

    async def wait(self, app_id: str) -> LoginSession | None:
        """Await the sign-in that `start()` launched.

        The HTTP route is fire-and-forget (the desktop polls `status()`), but a
        caller that owns a short-lived event loop — a test, or a CLI that wants to
        block until the browser round trip ends — must be able to join the task.
        Without this, `asyncio.run` tears the loop down while the child is still
        running, and on Windows cancelling a pending `proc.wait()` does not return:
        measured 2026-10-26, the whole interpreter hung inside
        `asyncio.runners._cancel_all_tasks`. That was the "test hangs" symptom, not
        a dead login command.
        """
        session = self._sessions.get(app_id)
        if session is None or session.task is None:
            return session
        try:
            await asyncio.shield(session.task)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            # The task records its own failure on the session; nothing to re-raise.
            pass
        return session

    def status(self) -> dict:
        return {app: s.as_dict() for app, s in self._sessions.items()}

    async def start(self, app_id: str, plan: dict) -> LoginSession:
        """Begin (or reuse) a sign-in. Never two at once for the same leg."""
        current = self._sessions.get(app_id)
        if current and current.state == "pending":
            return current

        session = LoginSession(app_id, plan.get("name", app_id), plan.get("kind", "none"))
        self._sessions[app_id] = session
        kind = plan.get("kind", "none")

        if kind == "oauth":
            # The provider owns a real OAuth flow (Antigravity: PKCE + local callback
            # + its own browser hand-off). Delegate; do not reimplement it here.
            session.task = asyncio.create_task(self._run_provider_oauth(app_id, session, plan))
        elif kind == "cli_login":
            session.task = asyncio.create_task(self._run_cli_login(session, plan))
        elif kind == "local_session":
            session.state = "ok"
            session.detail = plan.get("reason") or "这台桌面软件本来就已经登录了，不需要再登一次"
            session.finished_at = time.time()
        else:
            session.state = "unsupported"
            session.detail = plan.get("reason") or "这一条腿还没有接上点一下就能登录的路"
            session.finished_at = time.time()

        return session

    async def _run_cli_login(self, session: LoginSession, plan: dict) -> None:
        binary = _resolve_binary(plan.get("binary", ""))
        if not binary:
            session.state = "unsupported"
            session.detail = (
                f"这台机器上没有 {plan.get('binary') or '这个 CLI'}。Plobi 不代下载、不代安装"
                "（用户 2026-10-27 令：只扫描已有的产品），所以这条腿先不出现"
            )
            session.finished_at = time.time()
            return

        argv = [binary, *[str(a) for a in plan.get("argv") or []]]
        log = logging.getLogger("quota_hub")
        log.info("login start: %s (state stays token-free)", session.app_id)

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (OSError, ValueError) as exc:
            session.state = "failed"
            session.detail = f"起不动它的登录命令：{type(exc).__name__}: {exc}"
            session.finished_at = time.time()
            return

        opened = False
        tail: list[str] = []

        async def watch(stream) -> None:
            nonlocal opened
            while True:
                chunk = await stream.readline()
                if not chunk:
                    return
                text = chunk.decode(errors="replace").rstrip()
                if not text:
                    continue
                tail.append(text)
                del tail[:-30]
                if not opened:
                    match = _URL_RE.search(text)
                    if match:
                        session.url = _trim_url(match.group(0))
                        opened = True
                        # Opening a browser is UI, not I/O we should await on; a CLI
                        # that also opens its own browser makes this a harmless no-op.
                        try:
                            await asyncio.to_thread(webbrowser.open, session.url)
                        except Exception as exc:  # noqa: BLE001
                            log.warning("login: could not open %s: %s", session.url, exc)

        timeout = float(plan.get("timeout", 300.0))
        try:
            await asyncio.wait_for(
                asyncio.gather(watch(proc.stdout), watch(proc.stderr), proc.wait()),
                timeout=timeout,
            )
        except asyncio.CancelledError:
            # Never leave the child behind: on Windows an orphaned login window plus
            # a cancelled `proc.wait()` is what made the interpreter hang in teardown
            # (2026-10-26). Kill it, reap it, then let the cancellation through.
            session.state = "failed"
            session.detail = "登录过程被取消了（桌面关掉或换了一条腿）"
            session.finished_at = time.time()
            try:
                proc.kill()
                await asyncio.wait_for(proc.wait(), 5.0)
            except Exception:  # noqa: BLE001
                pass
            raise
        except asyncio.TimeoutError:
            proc.kill()
            session.state = "failed"
            session.detail = f"登录在 {timeout:.0f}s 内没有完成（窗口可能还开着，浏览器里那一步要本人走完）"
            session.finished_at = time.time()
            return

        session.finished_at = time.time()
        if proc.returncode == 0:
            session.state = "ok"
            session.detail = "登录命令自己跑完了；凭据归那个 CLI，我们没读也没存"
        else:
            session.state = "failed"
            session.detail = "；".join(tail[-2:])[:300] or f"登录命令退出码 {proc.returncode}"

        await self._finish(session, plan)

    async def _finish(self, session: LoginSession, plan: dict) -> None:
        """A leg that just signed in must not stay invisible in the catalog.

        The desktop polls this endpoint and reads `/v1/models` for the rows, so on
        success we re-discover accounts and re-read the provider's own model list.
        Failure to refresh is not a login failure and is swallowed on purpose: the
        sign-in did happen, and the next quota/health tick picks the catalog up.

        The ledger write comes first: a spawn-based leg has no credential we can
        read, so *this* is the evidence its `credential_health()` answers from. Without
        it the card would keep offering the `+` the user just made unnecessary.
        """
        on_success = plan.get("on_success")
        if session.state != "ok":
            return
        login_ledger.remember(session.app_id)
        if not callable(on_success):
            return
        try:
            result = on_success()
            if asyncio.iscoroutine(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("quota_hub").warning(
                "login refresh skipped for %s: %s: %s", session.app_id, type(exc).__name__, exc
            )

    async def _run_provider_oauth(self, app_id: str, session: LoginSession, plan: dict) -> None:
        runner = plan.get("runner")
        if not callable(runner):
            session.state = "unsupported"
            session.detail = "这条腿声明了 oauth，但没有给出可执行的登录入口"
            session.finished_at = time.time()
            return
        try:
            await asyncio.to_thread(runner)
            session.state = "ok"
            session.detail = "浏览器里那一步已经走完"
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            session.state = "failed"
            session.detail = f"{type(exc).__name__}: {exc}"
        finally:
            session.finished_at = time.time()
        await self._finish(session, plan)


def _trim_url(url: str) -> str:
    """Drop sentence punctuation that got glued onto a printed URL."""
    return url.rstrip(".,;:!?)）]}」』")


MANAGER = LoginManager()


def login_plan_for(registry, app_id: str) -> dict:
    """The plan for one leg: provider-declared first, then the login-only table."""
    key = (app_id or "").strip().lower()
    prov = registry.providers.get(key) if registry is not None else None
    if prov is not None:
        plan = prov.login_plan()
        plan.setdefault("name", getattr(prov, "display_name", "") or key)
        return plan

    if key in LOGIN_ONLY_TARGETS:
        entry = dict(LOGIN_ONLY_TARGETS[key])
        return {
            "kind": "cli_login",
            "name": entry.pop("name", key),
            "binary": entry.pop("binary", key),
            "argv": entry.pop("argv", []),
            "source": "login-only",
        }

    return {"kind": "none", "name": key, "reason": "hub 没有这一条腿"}


def auth_catalog(registry) -> list[dict]:
    """Every leg the desktop may offer a 连接 button for, with its live verdict."""
    rows: list[dict] = []
    seen: set[str] = set()

    for name, prov in (registry.providers.items() if registry else {}):
        # The card list IS the scan result (user ruling 2026-10-27). A product that is
        # not on this machine gets no card: Plobi does not download apps or CLIs, so a
        # row saying "not found here" would only be an invitation to go install one by
        # hand — which is the experience the ruling removes.
        probe = getattr(prov, "present", None)
        if callable(probe) and not probe():
            continue
        plan = prov.login_plan()
        plan.setdefault("name", getattr(prov, "display_name", "") or name)
        live, reason = registry.credential_health(prov)
        kind = plan.get("kind", "none")
        rows.append(
            {
                "id": name,
                "name": plan.get("name") or name,
                "kind": kind,
                "live": bool(live),
                "installed": True,
                "reason": reason,
                # The plan's own words when there is no click to offer. `reason` above
                # is the credential verdict ("ok", "credential_expired") — printing
                # that under a card would put a machine token on screen (measured
                # 2026-10-26: Codex/Claude/OpenCode cards read "ok").
                "login_reason": str(plan.get("reason") or "") if kind in {"none", "local_session"} else "",
                "aggregate": True,
            }
        )
        seen.add(name)

    for name, plan in LOGIN_ONLY_TARGETS.items():
        if name in seen:
            continue
        installed = _resolve_binary(plan.get("binary", "")) is not None
        if not installed:
            # Scan semantics: the CLI is not here, so there is no card to pick.
            continue
        rows.append(
            {
                "id": name,
                "name": plan.get("name", name),
                "kind": "cli_login",
                # `live` is a SIGN-IN verdict and nothing else. Having the binary on
                # PATH is `installed`, not "already signed in" — conflating the two
                # made the settings card read "已连接" and hide its button for a CLI
                # nobody had logged into (measured 2026-10-26: Gemini/Qwen both said
                # 已连接 with no way to sign in). For login-only legs we have no
                # credential probe, so the honest answer is "not signed in yet".
                "live": False,
                "installed": installed,
                "reason": "已装，还没登录过" if installed else "这台机器上还没装它的 CLI",
                "login_reason": "",
                "aggregate": False,
            }
        )

    return rows
