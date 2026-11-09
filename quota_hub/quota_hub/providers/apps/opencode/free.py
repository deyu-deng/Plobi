"""OpenCode Free as an http leg — the client's **own** local service (裁定 206 的落点).

为什么是它（2026-11-09 在这台机器上现读，OpenCode 2.x 的客户端自带服务）：

* 免费层在裸 http 上是**关着的**。官方文档 `https://opencode.ai/docs/console/models/`
  原文：「Free-tier chat models require OpenCode」；直连 `/inference/openai/v1` 恒 429
  `FreeUsageLimitError`。能吃到免费额度的通道只有 OpenCode 客户端自己。
* 客户端自己带着一个 http 服务：`opencode service`（后台服务）或 `opencode serve`（实例），
  监听 `http://127.0.0.1:<port>`，Basic 认证 `opencode:<password>`，口令在
  `~/.config/opencode/service.json`（**只有口令**，端口由 `opencode service status` 现读）。
  端口会变 ⇒ 端口与口令都是**每次现读**，不写死在配置里。

这一条腿的三段请求形状（实测读数，2026-11-09）：

    GET  {base}/api/model
      → 43 行，每行带 `cost`（数组，每档 `{input, output, cache:{read, write}}`）。
        **每档都是 0 的那 11 条就是免费池**（iceman-free / inkling-2-small-free /
        crouton-alpha-free / flint-40b-free / nemotron-3.5-super-vl-free / beam-free /
        mimo-v2.6-flash-free / deepseek-v4.1-flash-free / glm-5.3-flash-free /
        nemotron-3.5-lightning-free / big-pickle）。免费池按**上游自己写的价**裁，
        不抄一份会过期的名单。
    POST {base}/api/session            body {title, agent, model:{id,providerID}, permissions}
      → {"data":{"id":"ses_…"}}。`model` 必须带 `providerID`（少一个字段就是 400
        「Missing key at ["model"]["providerID"]」）；`permissions` 给 deny-all，
        `agent` 钉在只读的那个（`plan`）—— **这条腿是纯文本腿，不许它在本机动手**。
    POST {base}/api/session/{id}/prompt   body {text}
      → 200。**body 必须有 `text`**；模型不是在这里选的（选中在会话上）。
    GET  {base}/api/session/{id}/message
      → 助手消息的形状是**顶层 `content`**（不是 `parts`）：
        `{type:"assistant", agent, model:{id,providerID}, content:[{type:"text",text},
        {type:"reasoning",…}], finish, cost, tokens}`。

两条试过并作废的路（读数在案，裁定 206 §2 要求写清断在哪一步）：

* `POST /api/experimental/generate`（裸补全面）⇒ **503「Free usage limit reached」**。
  它走的是上游那条裸推理通道，不吃客户端身份 ⇒ 免费额度走不通。
* 请求体里塞 `tools` ⇒ **被静默忽略**（塞一个不存在的键同样回 200）。OpenAPI 全表里
  没有任何带 `tools` 的请求体 ⇒ **我们的工具进不去这条腿**。所以这一腿诚实地报
  `capabilities.tools = False`：工具在 OpenCode 客户端那边闭环，回到我们的是成稿，
  不是 `tool_calls`（裁定 204 那张表的同一判据）。要让工具进来，路子是让 OpenCode
  去调我们的 MCP 服务器（`WP-QUOTA-LEG-TOOLS-VIA-MCP` 那把刀），不是在这里假装有。

边界照旧守：口令只留在内存里，**不落盘、不进日志**；端口与口令每次现读；上游没写的
免费池我们不补；服务没起就说没起，不编一个「可用」。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import subprocess
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from ....providers.base import Account, Capabilities, Credential, Provider, UpstreamError

log = logging.getLogger("quota_hub")

#: OpenCode 的服务把口令放在这一个文件里（实测：只有 `password` 一个键）。
SERVICE_FILE_ENV = "OPENCODE_SERVICE_FILE"
#: 端口不在文件里 —— 它由 `opencode service status` 现读（实测输出就是一行 URL）。
SERVICE_URL_RE = re.compile(r"https?://[^\s\"']+")
#: 免费池判定用的上游字段名（`/api/model` 每行的 `cost` 数组）。
COST_KEYS = ("input", "output", "cache")
#: 服务没答「额度用完了」时我们自己留一段冷却（**不是读数，是兜底**）。
DEFAULT_FREE_COOLDOWN = 300.0
#: 一个回合的等待上限（秒）。OpenCode 的 agent 跑一整轮，比一次补全慢。
TURN_TIMEOUT = 240.0


def service_file() -> Path:
    """The file the client keeps its own service password in."""
    explicit = os.environ.get(SERVICE_FILE_ENV, "").strip()
    if explicit:
        return Path(explicit)
    return Path.home() / ".config" / "opencode" / "service.json"


def read_service_password() -> str:
    """The service's own password, or "". Never raises, never prints the value."""
    path = service_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    for name in ("password", "pass", "secret"):
        value = data.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def opencode_binary() -> str:
    """Locate the client's own binary — the service ships with it.

    Order: explicit override, PATH, this machine's portable dir, the desktop app's
    bundled copy. Same order the CLI-side provider uses.
    """
    override = os.environ.get("PLOBI_OPENCODE_CLI_PATH", "").strip()
    if override and Path(override).is_file():
        return override
    for name in ("opencode", "opencode-cli"):
        found = shutil.which(name)
        if found:
            return found
    candidates: list[Path] = []
    portable_root = os.environ.get("PLOBI_PORTABLES_CLI", "").strip()
    if portable_root:
        candidates.append(Path(portable_root) / "opencode" / "opencode.exe")
    candidates.append(Path("D:/Portables/cli/opencode/opencode.exe"))
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        candidates.append(Path(local) / "Programs" / "@opencodedesktop" / "resources" / "opencode-cli.exe")
    for candidate in candidates:
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            continue
    return ""


def _run_service_command(binary: str, args: list[str], timeout: float = 15.0) -> str:
    """Run one `opencode` service command and return its stdout ("" on any failure)."""
    if not binary:
        return ""
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("[opencode_free] %s failed: %s", " ".join(args), type(exc).__name__)
        return ""
    return proc.stdout or ""


def read_service_url(binary: str) -> str:
    """The base URL of the client's own service, read live from `service status`."""
    out = _run_service_command(binary, ["service", "status"])
    match = SERVICE_URL_RE.search(out or "")
    return match.group(0).rstrip("/") if match else ""


def start_service(binary: str) -> str:
    """Ask the client to bring its service up, then read the URL back."""
    if not binary:
        return ""
    _run_service_command(binary, ["service", "start"], timeout=30.0)
    for _ in range(3):
        url = read_service_url(binary)
        if url:
            return url
        time.sleep(1.0)
    return ""


def cost_is_zero(row: dict) -> bool:
    """Whether the upstream wrote a zero bill for this model — i.e. it is in the free pool.

    Measured 2026-11-09: `/api/model` rows carry `cost` as an array of tiers, each
    `{"input": 0, "output": 0, "cache": {"read": 0, "write": 0}}`. A paid row has a
    non-zero number in one of them. **A row that says nothing about its price is not a
    refusal** — same rule the Zen leg follows ("上游没说不等于不覆盖").
    """
    cost = row.get("cost")
    if not isinstance(cost, list) or not cost:
        return False
    for tier in cost:
        if not isinstance(tier, dict):
            continue
        for key in COST_KEYS:
            value = tier.get(key)
            if isinstance(value, dict):
                value = value.get("read") if key == "cache" else value.get("value")
            try:
                if float(value or 0) != 0:
                    return False
            except (TypeError, ValueError):
                return False
    return True


class OpenCodeFreeProvider(Provider):
    """The Free pool, reached over http through the client's own service."""

    name = "opencode_free"
    display_name = "OpenCode Free"
    # tools=False 是**实测**的结论，不是偷懒：请求体里的 `tools` 被静默忽略
    # （见文件头的读数），工具在 OpenCode 客户端那边闭环。stream=True 指的是
    # 「能接住一个 stream=true 的请求」——上游没有 token 级流式，所以整段折成
    # 一个 chunk 交付（与 Zen 腿同一手法）。
    # ⚠️ 上游那一行自己写着 `capabilities: {tools: true}` —— 那是**模型**在 OpenCode
    #    自己的 agent 里会不会用工具，不是**我们**的通道能不能带工具。这一格说的是
    #    后者（裁定 204 的判据：回到我们的是成稿还是 tool_calls）⇒ 这里仍然是 false。
    default_capabilities = Capabilities(stream=True, tools=False, sessionful=False)

    def __init__(self, config: dict, http) -> None:
        super().__init__(config, http)
        self.timeout = float(config.get("timeout", TURN_TIMEOUT))
        #: An explicitly configured base wins (that is how a test or a pinned port is
        #: pointed at one instance); otherwise the client's own service is asked.
        self._configured_base = str(config.get("base_url") or "").strip().rstrip("/")
        self._base_url = self._configured_base
        self._password = ""
        self._binary = ""
        self._started_service = False
        #: Set when the upstream says the free bucket is empty (measured 2026-11-09:
        #: the client lane answers the same `FreeUsageLimitError` the bare lane does).
        self._free_blocked_until = 0.0
        #: What the last probe said. The registry asks for the health synchronously,
        #: so this is the answer it gets — never a guess.
        self._live = False
        #: The upstream's rows, kept so `tuning()` can answer with their own numbers.
        self._model_rows: dict[str, dict] = {}

    # --- where the service lives -------------------------------------------
    def _service_base(self) -> str:
        if self._base_url:
            return self._base_url
        if not self._binary:
            self._binary = opencode_binary()
        if self._configured_base:
            self._base_url = self._configured_base
            return self._base_url
        url = read_service_url(self._binary)
        if not url and not self._started_service:
            # The client starts its own service on demand; so may we, once.
            self._started_service = True
            url = start_service(self._binary)
        if url:
            self._base_url = url
        return self._base_url

    def _auth_header(self) -> str:
        if not self._password:
            self._password = read_service_password()
        if not self._password:
            return ""
        token = base64.b64encode(f"opencode:{self._password}".encode()).decode()
        return f"Basic {token}"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "User-Agent": "plobi-quota-hub/1.0"}
        auth = self._auth_header()
        if auth:
            headers["Authorization"] = auth
        return headers

    # --- presence / credential ----------------------------------------------
    def present(self) -> bool:
        """The client is installed. (Whether its service runs is a separate question.)"""
        return bool(opencode_binary())

    def login_plan(self) -> dict:
        # The credential is the client's own session: Plobi has no sign-in to run here
        # (the client logs itself in), and the service is started by the client. So the
        # honest answer is "managed by that app" — never a button we cannot honour.
        return {
            "kind": "local_session",
            "reason": "在 OpenCode 里登录一次，它的免费额度就归这条腿用",
            "name": self.display_name,
        }

    async def _probe(self) -> tuple[bool, str]:
        """Is the service answering right now? (one cheap GET, no model turn)"""
        base = self._service_base()
        if not base:
            return False, "opencode service unreachable"
        try:
            res = await self.http.get(f"{base}/api/model", headers=self._headers(), timeout=15.0)
        except Exception as exc:  # noqa: BLE001 - transport errors are not verdicts
            log.warning("[opencode_free] service probe failed: %s", type(exc).__name__)
            return False, "opencode service unreachable"
        if res.status_code == 200:
            self._live = True
            return True, "ok"
        if res.status_code == 401:
            # The client re-keys its service when it restarts ⇒ the password on file is
            # stale. That is a re-auth, not a network problem.
            self._live = False
            return False, "credential_expired"
        self._live = False
        return False, "opencode service unreachable"

    def credential_health(self) -> tuple[bool, str]:
        # Synchronous by contract (the registry asks without the network). The probe
        # itself is async, so the answer here is the last known one — and it is only
        # ever "ok" after the service actually answered.
        if self._quota_remaining() > 0:
            # The upstream said the bucket is empty — that is a fact about the quota,
            # not about the credential, and it is what the card must show.
            return False, "quota_exhausted"
        if self._live:
            return True, "ok"
        return False, "opencode service unreachable"

    def quota_retry_after(self) -> int | None:
        """Seconds left on the free bucket, None when this leg is not cooling."""
        remaining = self._quota_remaining()
        if remaining <= 0:
            return None
        return max(1, int(remaining))

    def _quota_remaining(self) -> float:
        return max(
            float(self._free_blocked_until or 0.0) - time.time(),
            self.cooldown_remaining(),
        )

    # --- accounts / models --------------------------------------------------
    async def discover_accounts(self) -> list[Account]:
        live, reason = await self._probe()
        if not live:
            self.accounts = []
            self.served_models = ()
            log.info("[opencode_free] service not usable (%s); the leg stays empty", reason)
            return []
        self._base_url = self._base_url or self._service_base()
        self._password = self._password or read_service_password()
        self.accounts = [
            Account(
                id=f"{self.name}-default",
                provider=self.name,
                label=self.display_name,
                cred=Credential(access_token=self._password),
            )
        ]
        await self.refresh_models(self.accounts[0])
        return self.accounts

    async def refresh_models(self, acc: Account) -> list[str] | None:
        """The upstream's own list, cut down to what it prices at zero."""
        base = self._service_base()
        if not base:
            return None
        try:
            res = await self.http.get(f"{base}/api/model", headers=self._headers(), timeout=30.0)
        except Exception as exc:  # noqa: BLE001
            log.warning("[opencode_free] model list unreachable: %s", type(exc).__name__)
            return None
        if res.status_code != 200:
            log.warning("[opencode_free] model list refused with HTTP %s", res.status_code)
            return None
        try:
            body = res.json()
        except ValueError:
            return None
        rows = body.get("data") if isinstance(body, dict) else None
        free = [
            str(row.get("id")).strip()
            for row in (rows or [])
            if isinstance(row, dict) and str(row.get("id") or "").strip() and cost_is_zero(row)
        ]
        #: The upstream's own rows, keyed by the id we publish them under — a row
        #: carries more than its name (its window, its output cap), and both are
        #: answers we must not guess.
        self._model_rows = {
            f"{self.name}/{str(row.get('id')).strip()}": row
            for row in (rows or [])
            if isinstance(row, dict) and str(row.get("id") or "").strip()
        }
        self.served_models = tuple(f"{self.name}/{model}" for model in free)
        log.info(
            "[opencode_free] %d model(s) listed by the upstream, %d priced at zero",
            len(rows or []),
            len(free),
        )
        return list(self.served_models)

    def tuning(self, model: str) -> dict:
        """What this model lets the user change, taken from the upstream's own row.

        Measured 2026-11-09: every row carries the model's window (``limit.context``)
        and its output cap (``limit.output``). The upstream sells no *menu* of windows
        for these models, so the window is published the way a fixed window is
        published elsewhere: one value, explicitly not adjustable.
        """
        row = self._model_rows.get(model) or {}
        limit = row.get("limit") if isinstance(row.get("limit"), dict) else {}
        out: dict[str, Any] = {}

        window = limit.get("context")
        if isinstance(window, int) and not isinstance(window, bool) and window > 0:
            out["context"] = {
                "adjustable": False,
                "choices": [window],
                "default": window,
                "max": window,
            }

        cap = limit.get("output")
        if isinstance(cap, int) and not isinstance(cap, bool) and cap > 0:
            out["max_output"] = cap

        return out

    async def ensure_fresh(self, acc: Account) -> None:
        # The password is the client's own; it changes only when the client restarts
        # its service, and then the file changes with it. Re-read it every turn.
        password = read_service_password()
        if password:
            self._password = password
            acc.cred.access_token = password

    # --- the turn ------------------------------------------------------------
    @staticmethod
    def _bare_model(model: str) -> str:
        """`opencode_free/iceman-free` → `iceman-free`."""
        return model.split("/", 1)[1] if "/" in model else model

    @staticmethod
    def _transcript(oai_req: dict) -> str:
        """Flatten the OpenAI request into the one text this lane accepts.

        The session lane takes a single `text` (measured). The request's history is
        therefore written into it: system first, then the turns, each labelled.
        Tool parts cannot ride along (the upstream ignores `tools` entirely), so a tool
        message is written out as what it is — a result we already have.
        """
        lines: list[str] = []
        for message in oai_req.get("messages") or []:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "user")
            content = message.get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts: list[str] = []
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "text":
                        parts.append(str(part.get("text") or ""))
                    elif part.get("type") == "tool_result":
                        parts.append(f"[{part.get('name') or 'tool'}] {part.get('content') or ''}")
                text = "\n".join(p for p in parts if p)
            else:
                text = ""
            if not text.strip():
                continue
            lines.append(f"{role}: {text}" if role != "user" else text)
        return "\n\n".join(lines).strip()

    def _refuse(self, res) -> UpstreamError:
        """Map the service's own answers onto our policy."""
        text = (getattr(res, "text", "") or "")[:400]
        status = getattr(res, "status_code", 0)
        if status == 401:
            return UpstreamError(401, "opencode service refused our password", reauth=True)
        if status == 404:
            return UpstreamError(404, text, retryable=False)
        if status in (429, 503) and "free usage limit" in text.lower():
            # The upstream does not say when the bucket refills on this lane ⇒ we do
            # not invent a deadline: park for a bounded while and let the next turn
            # ask again. (The Zen lane gets a real `retry-after`; this one does not.)
            wait = DEFAULT_FREE_COOLDOWN
            try:
                wait = float(res.headers.get("retry-after") or 0.0) or wait
            except (AttributeError, ValueError):
                pass
            self.note_cooldown(wait, "quota_exhausted")
            return UpstreamError(429, text, retryable=True, cooldown=wait)
        return UpstreamError(status or 502, text, retryable=status in (429, 500, 502, 503))

    async def _create_session(self, model: str) -> str:
        """One session, pinned to the model, to a read-only agent, with every permission denied.

        `agent: "plan"` is the client's own read-only agent; `permissions: deny-all` is
        the belt to its braces — **this leg must not touch the machine**. Measured
        2026-11-09: both fields are accepted by `POST /api/session`.
        """
        base = self._service_base()
        body = {
            "title": "plobi quota hub (opencode free)",
            "agent": str(self.config.get("agent") or "plan"),
            "model": {"id": model, "providerID": str(self.config.get("provider_id") or "opencode")},
            "permissions": [{"action": "*", "resource": "*", "effect": "deny"}],
        }
        res = await self.http.post(f"{base}/api/session", headers=self._headers(), json=body, timeout=30.0)
        if res.status_code != 200:
            raise self._refuse(res)
        try:
            payload = res.json()
        except ValueError as exc:
            raise UpstreamError(502, f"opencode answered a non-JSON body: {exc}") from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        sid = str((data or {}).get("id") or "") if isinstance(data, dict) else ""
        if not sid:
            raise UpstreamError(502, "opencode returned no session id")
        return sid

    async def _ask(self, sid: str, text: str) -> dict:
        """Put one message in and wait for the answer.

        The service answers the POST itself with `200` and then reports the outcome in
        the session's message list (measured 2026-11-09: a spent free bucket comes back
        as an `idle` row with `outcome: "failed"` and an `error` object, plus an
        assistant row with `finish: "error"` and an empty `content`). **So the verdict
        is read from the messages, not from the POST's status code.**
        """
        base = self._service_base()
        res = await self.http.post(
            f"{base}/api/session/{sid}/prompt", headers=self._headers(), json={"text": text}, timeout=60.0
        )
        if res.status_code != 200:
            raise self._refuse(res)

        deadline = time.time() + self.timeout
        while time.time() < deadline:
            listing = await self.http.get(f"{base}/api/session/{sid}/message", headers=self._headers(), timeout=30.0)
            if listing.status_code != 200:
                raise self._refuse(listing)
            try:
                payload = listing.json()
            except ValueError as exc:
                raise UpstreamError(502, f"opencode answered a non-JSON body: {exc}") from exc
            rows = payload.get("data") if isinstance(payload, dict) else None
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                error = row.get("error")
                if isinstance(error, dict) and error:
                    raise self._row_error(error)
                if row.get("type") != "assistant":
                    continue
                content = row.get("content")
                if not isinstance(content, list):
                    continue
                answer = "".join(
                    str(seg.get("text") or "")
                    for seg in content
                    if isinstance(seg, dict) and seg.get("type") == "text"
                )
                if answer.strip():
                    return {"text": answer, "row": row}
            await _sleep(1.2)
        raise UpstreamError(504, "opencode did not answer in time", retryable=True)

    def _row_error(self, error: dict) -> UpstreamError:
        """Map an error the service reported inside the session's messages."""
        message = str(error.get("message") or "")[:400]
        status = error.get("status")
        kind = str(error.get("type") or "")
        if status == 429 or kind == "provider.quota" or "free usage limit" in message.lower():
            wait = DEFAULT_FREE_COOLDOWN
            self._free_blocked_until = time.time() + wait
            # On disk too: a restart must not forget that the bucket is empty (the
            # picker would offer these models again and the user would only find out
            # by clicking one — user, 2026-11-08 21:2x).
            self.note_cooldown(wait, "quota_exhausted")
            return UpstreamError(429, message, retryable=True, cooldown=wait)
        return UpstreamError(int(status) if isinstance(status, int) else 502, message, retryable=False)

    async def chat(self, acc: Account, oai_req: dict) -> dict:
        await self.ensure_fresh(acc)
        model = self._bare_model(str(oai_req.get("model") or ""))
        text = self._transcript(oai_req)
        if not text:
            raise UpstreamError(400, "empty request", retryable=False)
        sid = await self._create_session(model)
        answer = await self._ask(sid, text)
        self._live = True
        self.clear_cooldown()  # a served turn is the proof that the bucket is not empty
        row = answer["row"]
        tokens = row.get("tokens") if isinstance(row.get("tokens"), dict) else {}
        usage = {
            "completion_tokens": int(tokens.get("output") or 0),
            "prompt_tokens": int(tokens.get("input") or 0),
            "total_tokens": int(tokens.get("input") or 0) + int(tokens.get("output") or 0),
        }
        return {
            "id": f"chatcmpl-{row.get('id') or int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": str(oai_req.get("model") or model),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": answer["text"]},
                    "finish_reason": str(row.get("finish") or "stop"),
                }
            ],
            "usage": usage,
        }

    async def chat_stream(self, acc: Account, oai_req: dict) -> AsyncIterator[dict]:
        """One folded chunk: the upstream has no token-level stream on this lane."""
        result = await self.chat(acc, oai_req)
        choice = (result.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        yield {
            "choices": [{"delta": message, "finish_reason": choice.get("finish_reason") or "stop", "index": 0}],
            "created": result.get("created"),
            "id": result.get("id"),
            "model": result.get("model"),
            "object": "chat.completion.chunk",
        }


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)
