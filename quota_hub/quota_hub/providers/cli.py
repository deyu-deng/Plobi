"""Spawn-based (opendesign-style) provider base class.

Instead of reverse-engineering a desktop app's private API or scraping its tokens,
a CliProvider drives a *locally-installed, already-authenticated* coding-agent CLI
as a subprocess -- the same pattern Open Design uses to drive claude/codex/
cursor-agent/plobi. The CLI consumes its own quota; we only do process I/O and
translate the result into the OpenAI ChatCompletion shape. Zero reverse-
engineering, minimal ToS risk.

Subclasses / config only need to declare:
  - binary        : command on PATH (or absolute path / script file)
  - interpreter   : optional (e.g. python) used to run a script `binary`
  - prompt_flag   : argv flag that carries the prompt            (e.g. "-p")
  - args_template : optional argv skeleton with {prompt} / {model}
                    (needed when the prompt is positional: `codex exec <p>`)
  - model_flag    : optional argv flag for the model             (e.g. "--model")
  - served_models : tuple of unified model ids this provider answers
  - model_map     : unified id -> CLI model string
  - extra_args    : fixed argv appended to every invocation
  - isolate_workspace : run in a private scratch cwd (agent CLIs can edit files)

The conversation is flattened into a single prompt and sent statelessly; the CLI
gets full context each turn (correct for a gateway, which is itself stateless).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from .. import login_ledger
from .base import Account, Capabilities, Credential, Provider, UpstreamError

# Phrases a CLI prints when it has no session. Deliberately narrow: matching only
# what real CLIs emit, so an odd model answer that happens to contain the word
# "login" is not mistaken for an auth failure.
_NOT_SIGNED_IN = re.compile(
    r"authentication required|please use /login|please run .?/login|not logged in"
    r"|sign in to your account|please (?:log|sign) in",
    re.IGNORECASE,
)

# The model list a CLI prints in its own --help (CodeBuddy Code wording).
_SUPPORTED_MODELS = re.compile(r"Currently supported:\s*\(([^)]*)\)", re.IGNORECASE)
_HELP_TIMEOUT = 20.0


def _as_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return str(content)


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def default_workspace(name: str) -> str:
    """A private cwd for agent CLIs that may edit files.

    Chat routed through the hub is a single question, not a coding session; the
    CLI must not get the gateway's own working tree (or the user's repo) as cwd.
    Root precedence: ``QUOTA_HUB_WORKSPACE_ROOT`` → ``PLOBI_HOME/quota_hub-workspaces``
    → ``~/.quota_hub/workspaces``. The directory is created by ``_spawn``; merely
    constructing/discovering a provider writes nothing.
    """
    root = os.environ.get("QUOTA_HUB_WORKSPACE_ROOT", "").strip()
    if not root:
        home = os.environ.get("PLOBI_HOME", "").strip()
        root = str(Path(home) / "quota_hub-workspaces") if home else str(
            Path.home() / ".quota_hub" / "workspaces"
        )
    return str(Path(root) / name)


def _chunk(cid: str, model: str, text=None, finish=None) -> dict:
    delta = {} if text is None else {"content": text}
    return {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


class CliProvider(Provider):
    """Generic subprocess-driven provider. Override flags / MODE_MAP in subclass."""

    name = "cli"
    served_models: tuple[str, ...] = ()
    # Stateless spawn: no native tool protocol, no vision, no embeddings.
    # Stream is "fake" (whole stdout chunked) but clients may still request stream.
    default_capabilities = Capabilities(
        stream=True,
        tools=False,
        vision=False,
        embeddings=False,
        sessionful=False,
        compliance="compliant",
    )

    # --- config knobs (subclass defaults, overridable per provider in config) ---
    binary: str = ""
    interpreter: str | None = None
    prompt_flag: str = "-p"
    args_template: list[str] = []
    model_flag: str = "--model"
    extra_args: list[str] = []
    timeout: float = 120.0
    cwd: str | None = None
    #: argv that starts this CLI's OWN sign-in flow (Cline: ``("auth",)``).
    #: None means "not declared" — and "we have not wired this" is a different claim
    #: from "this leg is broken", so the desktop must show the first one. Declare it
    #: only after reading the CLI's own `--help`: an invented argv would open a
    #: browser at the wrong place and read as a working button.
    login_args: tuple[str, ...] | None = None
    #: A person has to click through a window, so this is far looser than chat.
    login_timeout: float = 300.0
    #: This CLI refuses its own login without a terminal (measured 2026-10-28:
    #: `cline auth` → "interactive auth setup requires a TTY"). Those get a real window
    #: instead of a pipe — see `authflow.LoginManager._run_console_login`.
    login_tty: bool = False
    #: Agent CLIs (codex/claude/opencode/kiro/cline) can edit files; give them a
    #: private scratch directory instead of whatever tree the gateway was launched
    #: from. ``workspace`` config overrides the path.
    isolate_workspace: bool = False
    #: JSON-output CLIs cannot be line-streamed as plain text; run once and emit
    #: the parsed answer as a single chunk (still a valid OpenAI stream).
    stream_from_chat: bool = False
    model_map: dict[str, str] = {}
    #: Public id prefix for discovered models. A composed provider (WorkbuddyHybrid)
    #: sets this to its own name so this class's registry key never leaks into the
    #: model ids clients see. Empty = fall back to ``self.name``.
    model_prefix: str = ""
    models_refreshed: bool = False

    def __init__(self, config, http):
        super().__init__(config, http)
        self.binary = config.get("binary", self.binary)
        self.interpreter = config.get("interpreter", self.interpreter)
        self.prompt_flag = config.get("prompt_flag", self.prompt_flag)
        self.args_template = list(config.get("args_template", self.args_template))
        self.model_flag = config.get("model_flag", self.model_flag)
        self.extra_args = list(config.get("extra_args", self.extra_args))
        self.timeout = float(config.get("timeout", self.timeout))
        self.cwd = config.get("cwd") or None
        if not self.cwd and self.isolate_workspace:
            self.cwd = config.get("workspace") or default_workspace(self.name)
        self.model_map = {**self.model_map, **config.get("model_map", {})}
        self.env_extra = {k: str(v) for k, v in config.get("env", {}).items()}
        # A config file may declare the login argv for a CLI we have not verified
        # ourselves; that is the owner's call on their own machine, not a guess of ours.
        if "login_args" in config:
            self.login_args = tuple(str(a) for a in (config.get("login_args") or []))
        self.login_timeout = float(config.get("login_timeout", self.login_timeout))
        if "login_tty" in config:
            self.login_tty = bool(config.get("login_tty"))
        if config.get("models"):
            self.served_models = tuple(config["models"])
        self.binary_path: str | None = None

    # --- presence --------------------------------------------------------
    def present(self) -> bool:
        """A spawn-based leg exists exactly when its binary does. Nothing to install."""
        return self._binary_present()

    # --- sign-in ---------------------------------------------------------
    def login_plan(self) -> dict:
        """Spawn-based legs sign in through their OWN login command.

        The plan is honest about the two reasons it cannot work yet — the CLI is not
        on PATH, and nobody has read its login argv out of `--help`. Both report as
        "not wired on our side", never as a credential failure, because the user
        would otherwise go looking for a sign-in that is not the problem.
        """
        if self.login_args is None:
            return {
                "kind": "none",
                "reason": "这一条腿的登录命令还没从它自己的 --help 里核出来",
            }
        return {
            "kind": "cli_login",
            "binary": self.binary,
            "argv": list(self.login_args),
            "timeout": self.login_timeout,
            "tty": bool(self.login_tty),
            "name": self.display_name or self.name,
        }

    # --- discovery -------------------------------------------------------
    def _binary_present(self) -> bool:
        if self.interpreter:
            return Path(self.binary).is_file()
        return shutil.which(self.binary) is not None or Path(self.binary).is_file()

    # --- credential ------------------------------------------------------
    def credential_health(self) -> tuple[bool, str]:
        """A CLI leg is signed in only if Plobi ran its login and it exited 0.

        The default verdict in the registry is "live, ok" for anything that does not
        answer — reasonable for a leg that owns a credential store we can read, and a
        lie for a leg we have never asked. Spawn-based CLIs fall in the second group:
        their token lives inside the CLI, so the only evidence we are allowed to have
        is the sign-in we watched happen. Without it the card keeps its `+`.
        """
        if login_ledger.verified(self.name):
            return True, "ok"
        return False, "not_signed_in"

    async def discover_accounts(self) -> list[Account]:
        import logging

        if not self._binary_present():
            # Non-fatal: gateway still boots; this provider simply has no
            # schedulable account (the scheduler skips providers with none).
            logging.getLogger("quota_hub").warning(
                "[%s] binary '%s' not found on PATH — provider disabled until the "
                "CLI is installed & on PATH.",
                self.name,
                self.binary,
            )
            self.accounts = []
            return []

        if self.interpreter:
            self.binary_path = self.binary
        else:
            wp = shutil.which(self.binary)
            self.binary_path = wp if wp else (self.binary if Path(self.binary).is_file() else None)

        declared = self.config.get("accounts") or [{"id": f"{self.name}-default"}]
        accs: list[Account] = []
        for i, a in enumerate(declared):
            accs.append(
                Account(
                    id=a.get("id", f"{self.name}-{i}"),
                    provider=self.name,
                    label=a.get("label", f"{self.binary} seat"),
                    # auth is owned by the CLI itself; per-account CLI args ride in
                    # cred.extra so build_invocation() can pick them up.
                    cred=Credential(access_token="cli", extra={"args": list(a.get("args", []))}),
                )
            )
        self.accounts = accs
        return accs

    async def ensure_fresh(self, acc: Account) -> None:
        # The CLI owns its own session/auth; nothing to refresh here.
        if not self.binary_path:
            raise UpstreamError(401, f"{self.name}: CLI binary missing", reauth=True)

    async def refresh_models(self, acc: Account) -> list[str] | None:
        """Read the CLI's own advertised catalog and replace our served list.

        A seeded catalog rots on the first CLI upgrade — measured on this machine:
        the WorkBuddy-embedded CLI advertises ``glm-5.0 / kimi-k2.5`` while the
        standalone package 2.161.4 advertises an entirely different set
        (``glm-5.3 / kimi-k3-1 / deepseek-v4-pro …``). Hand-copying that list into
        config would be 裁定 46's second-copy problem moved one layer down, so the
        catalog is re-read from the CLI's own ``--help``.

        Follows the Provider contract set by AntigravityProvider.refresh_models:
        return the **short** ids on success and replace ``served_models`` +
        ``model_map`` here, or return ``None`` on any failure so the Registry keeps
        the seed. Never raises — a model-list read must not break chat routing.
        """
        if not self.binary_path:
            return None
        argv = ([self.interpreter] if self.interpreter else []) + [self.binary_path, "--help"]
        env = dict(os.environ)
        env.update(self.env_extra)
        if self.cwd:
            os.makedirs(self.cwd, exist_ok=True)
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=self.cwd,
                env=env,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_HELP_TIMEOUT)
        except (OSError, ValueError) as exc:
            logging.getLogger("quota_hub").warning(
                "%s model refresh skipped: %s: %s", self.name, type(exc).__name__, exc
            )
            return None
        match = _SUPPORTED_MODELS.search(_strip_ansi(stdout.decode(errors="replace")))
        if not match:
            return None
        short = [n.strip() for n in match.group(1).split(",") if n.strip()]
        if not short:
            logging.getLogger("quota_hub").warning(
                "%s model refresh returned an empty list; keeping seed", self.name
            )
            return None
        # A composed provider (WorkbuddyHybrid) owns the public id space, so it sets
        # model_prefix; without that the inner "workbuddy_cli" name would leak into
        # the unified model ids and change what clients see.
        prefix = self.model_prefix or self.name
        self.served_models = tuple(f"{prefix}/{m}" for m in short)
        self.model_map = {f"{prefix}/{m}": m for m in short}
        self.models_refreshed = True
        logging.getLogger("quota_hub").info(
            "%s: live model catalog = %d model(s)", self.name, len(short)
        )
        return short

    # --- prompt / invocation --------------------------------------------
    def format_prompt(self, oai_req: dict) -> str:
        """Flatten the OpenAI conversation into one text prompt (stateless)."""
        parts = []
        for m in oai_req.get("messages", []):
            role = m.get("role")
            text = _as_text(m.get("content"))
            if not text:
                continue
            if role == "system":
                parts.append(f"[system]\n{text}")
            elif role == "user":
                parts.append(f"[user]\n{text}")
            elif role == "assistant":
                parts.append(f"[assistant]\n{text}")
            else:
                parts.append(text)
        return "\n\n".join(parts)

    def _cli_model(self, oai_model: str) -> str:
        if oai_model in self.model_map:
            return self.model_map[oai_model]
        if "/" in oai_model:
            return oai_model.split("/", 1)[1]
        return oai_model

    def build_invocation(self, acc: Account, oai_req: dict) -> list[str]:
        argv: list[str] = []
        if self.interpreter:
            argv.append(self.interpreter)
        argv.append(self.binary_path or self.binary)
        argv += list(self.extra_args)
        argv += list(acc.cred.extra.get("args", []))
        prompt = self.format_prompt(oai_req)
        model = self._cli_model(oai_req["model"])

        if self.args_template:
            used_prompt = False
            used_model = False
            for token in self.args_template:
                if token == "{prompt}":
                    argv.append(prompt)
                    used_prompt = True
                    continue
                if token == "{model_flag}":
                    if self.model_flag and model:
                        argv += [self.model_flag, model]
                        used_model = True
                    continue
                if token == "{model}":
                    if model:
                        argv.append(model)
                        used_model = True
                    continue
                rendered = token.replace("{prompt}", prompt).replace("{model}", model)
                if rendered != token:
                    used_prompt = used_prompt or "{prompt}" in token
                    used_model = used_model or "{model}" in token
                argv.append(rendered)
            if not used_prompt:
                # A template without the placeholder is still usable; keep the
                # prompt in the conventional slot instead of dropping it.
                argv += [self.prompt_flag, prompt] if self.prompt_flag else [prompt]
            if self.model_flag and model and not used_model:
                argv += [self.model_flag, model]
            return argv

        argv += [self.prompt_flag, prompt]
        # Only append a model flag when a real model was resolved — some CLIs
        # error on an empty/None --model, and many default sensibly without it.
        if self.model_flag and model:
            argv += [self.model_flag, model]
        return argv

    # --- response parsing ------------------------------------------------
    def parse_response(self, stdout: str, oai_req: dict) -> dict:
        text = _strip_ansi(stdout).replace("\r\n", "\n").strip()
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": oai_req["model"],
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": text},
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    # --- execution -------------------------------------------------------
    async def _spawn(self, acc: Account, oai_req: dict):
        argv = self.build_invocation(acc, oai_req)
        env = dict(os.environ)
        env.update(self.env_extra)
        if self.cwd:
            # Isolated agent cwd: created lazily so discovery/tests stay read-only.
            os.makedirs(self.cwd, exist_ok=True)
        return await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
            env=env,
        )

    async def chat(self, acc: Account, oai_req: dict) -> dict:
        try:
            proc = await self._spawn(acc, oai_req)
        except FileNotFoundError as e:
            raise UpstreamError(503, f"{self.name}: cannot launch CLI: {e}")
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise UpstreamError(
                504,
                f"{self.name}: CLI timed out after {self.timeout}s",
                retryable=True,
                cooldown=10.0,
            )
        if proc.returncode != 0:
            err = (stderr or b"").decode(errors="replace").strip()[:500]
            raise UpstreamError(502, f"{self.name}: CLI exited {proc.returncode}: {err}")
        text = stdout.decode(errors="replace")
        self._assert_usable(text, (stderr or b"").decode(errors="replace"))
        return self.parse_response(text, oai_req)

    def _assert_usable(self, stdout_text: str, stderr_text: str) -> None:
        """Refuse to turn a broken CLI run into a plausible-looking answer.

        Measured on CodeBuddy Code: a signed-out CLI exits **0** and puts
        "Authentication required. Please use /login …" on stderr (embedded build) or
        on stdout (some paths) — so a returncode check alone lets it through, and the
        gateway serves either the hint text or an empty string as a real completion.
        That is the worst possible failure shape: upstream code cannot tell it apart
        from a model that chose to say nothing.

        Two rules, both honest about what we know:
          * the run *mentions* being signed out -> reauth, so the scheduler marks the
            account REAUTH instead of answering;
          * the run succeeded but produced no text -> a failure too. An empty
            completion is never a legitimate gateway answer for a chat channel.
        """
        combined = f"{stdout_text}\n{stderr_text}"
        if _NOT_SIGNED_IN.search(combined):
            raise UpstreamError(
                401,
                f"{self.name}: CLI is not signed in — run its login flow on this machine",
                reauth=True,
            )
        if not stdout_text.strip():
            raise UpstreamError(
                502,
                f"{self.name}: CLI exited 0 but printed nothing",
                retryable=True,
                cooldown=15.0,
            )

    def stream_text(self, raw: bytes, acc: Account | None = None) -> str:
        """One chunk of the CLI's stdout → the text a streaming client should see.

        Default: the bytes as printed. A leg whose stream is JSONL overrides this to pull
        the assistant delta out of each line — that is how Kiro streams the model's token
        groups instead of handing the whole answer back at the end, without a second copy
        of the process plumbing below.
        """
        return _strip_ansi(raw.decode(errors="replace"))

    async def chat_stream(self, acc: Account, oai_req: dict) -> AsyncIterator[dict]:
        if self.stream_from_chat:
            # JSON-output CLIs must be parsed as a whole. Run once, then emit the
            # parsed answer as one chunk so callers still get a valid stream.
            resp = await self.chat(acc, oai_req)
            cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
            model = oai_req["model"]
            text = resp["choices"][0]["message"]["content"]
            if text:
                yield _chunk(cid, model, text)
            yield _chunk(cid, model, None, finish="stop")
            return

        try:
            proc = await self._spawn(acc, oai_req)
        except FileNotFoundError as e:
            raise UpstreamError(503, f"{self.name}: cannot launch CLI: {e}")
        cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        model = oai_req["model"]
        stdout = proc.stdout
        collected: list[str] = []
        stderr_text = ""
        try:
            assert stdout is not None
            while True:
                try:
                    line = await asyncio.wait_for(stdout.readline(), self.timeout)
                except asyncio.TimeoutError:
                    raise UpstreamError(
                        504, f"{self.name}: CLI stream timed out", retryable=True, cooldown=10.0
                    )
                if not line:
                    break
                text = self.stream_text(line, acc)
                if text:
                    collected.append(text)
                    yield _chunk(cid, model, text)
            # flush any trailing bytes emitted without a final newline
            rest = await stdout.read()
            if rest:
                text = self.stream_text(rest, acc)
                if text:
                    collected.append(text)
                    yield _chunk(cid, model, text)
            # Drain stderr before waiting: a CLI that complains loudly on stderr
            # would otherwise fill the unread pipe buffer and block the child.
            if proc.stderr is not None:
                stderr_text = (await proc.stderr.read()).decode(errors="replace")
            try:
                await asyncio.wait_for(proc.wait(), 5.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        finally:
            if proc.returncode is None:
                with_proc = True
                try:
                    proc.kill()
                except Exception:
                    with_proc = False
                if with_proc:
                    await proc.wait()
        if proc.returncode != 0:
            raise UpstreamError(
                502,
                f"{self.name}: CLI exited {proc.returncode}: {stderr_text.strip()[:500]}",
            )
        # Same rule as the non-stream path: a signed-out or silent CLI run is a
        # failure, not an answer. Raising here means the client gets an error
        # instead of a clean finish on empty content.
        self._assert_usable("".join(collected), stderr_text)
        yield _chunk(cid, model, None, finish="stop")
