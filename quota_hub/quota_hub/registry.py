"""Provider registry + model routing.

Builds provider instances from config and resolves a unified model id to the
provider (or providers) that should serve it. Supports:

  - concrete models: "antigravity/gemini-3-pro" -> that provider
  - aliases:         "gemini" -> "antigravity/gemini-3-pro"  (rename, in config)
  - routing rules:   a model (glob) -> an ordered list of candidate providers, so
                     the scheduler can fail over across apps that serve the same
                     model (e.g. multiple accounts / a mirrored model).

resolve() returns (provider_name | [provider_names], canonical_model). A list means
cross-provider failover; the scheduler only keeps providers that actually serve the
canonical model.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import time

import httpx

from . import model_health
from .providers.acp import AcpProvider
from .providers.apps.antigravity import AntigravityCliProvider, AntigravityProvider
from .providers.apps.claude_code import ClaudeCodeCliProvider
from .providers.apps.cline import ClineCliProvider
from .providers.apps.codex import CodexCliProvider
from .providers.apps.cursor import CursorProvider
from .providers.apps.kiro import KiroCliProvider
from .providers.apps.marvis import MarvisCliProvider, MarvisGuiProvider
from .providers.apps.opencode import OpenCodeCliProvider
from .providers.apps.opencode.api import OpencodeApiProvider
from .providers.apps.gemini.api import GeminiApiProvider
from .providers.apps.qoder.api import QoderProvider
from .providers.apps.workbuddy import (
    WorkbuddyApiProvider,
    WorkbuddyCliProvider,
    WorkbuddyGuiProvider,
)
from .providers.base import Provider
from .providers.mock import MockProvider

logger = logging.getLogger(__name__)

#: Ceiling on catalog-refresh passes for a leg that keeps answering with no models. Past
#: this the leg stays empty until its next sign-in, so a permanently-empty catalog costs a
#: handful of upstream calls rather than an endless poll.
_MODELS_MAX_PASSES = 6

# All upstream adapters are registered here. Add a new desktop app by writing a
# Provider subclass and listing it below; config keys (e.g. "cursor") map 1:1 to
# PROVIDER_CLASSES keys.
#
# Two compliant routes exist for every app that supports them:
#   * spawn-CLI   (CliProvider)  -> drive a locally-installed CLI subprocess
#   * GUI         (GuiProvider)  -> Windows UI Automation of a desktop GUI
# Non-compliant routes (token scraping / reverse-engineered API) are kept only as
# explicit, documented fallbacks and should be avoided for shipped features.
#
#   cursor            -> Cursor (gRPC/Connect protobuf; token OK, chat needs .proto)
#   antigravity       -> Google Antigravity Cloud Code (Gemini; reverse-engineered
#                        envelope, needs a captured OAuth token — NON-compliant)
#   antigravity_cli   -> Google `agy` CLI spawned as a subprocess (opendesign-style,
#                        uses the user's own Google quota, no token scraping)
#   codex_cli         -> OpenAI Codex CLI (`codex exec --json`) + native 5h/weekly
#                        quota from /backend-api/wham/usage
#   claude_code_cli   -> Anthropic Claude Code (`claude -p --output-format json`) +
#                        native 5h/weekly usage from api.anthropic.com/api/oauth/usage
#   opencode_cli      -> OpenCode (`opencode run`) + OpenCode Go 5h/weekly/monthly
#                        usage from opencode.ai/zen/go/v1/usage
#   kiro_cli          -> AWS Kiro CLI (`kiro-cli chat --no-interactive`) + model
#                        catalog from `--list-models --format json`
#   cline_cli         -> Cline CLI over its own ACP stdio port (`cline --acp`): models
#                        from `session/new`, tools via `mcpServers`, sign-in state from
#                        the CLI's own refusal
#   marvis_cli        -> any CLI-based Marvis (openmarvis / marvisx-cli / ...) spawned
#                        as a subprocess; config-driven, no token scraping
#   marvis_gui        -> Tencent consumer Marvis (marvis.qq.com): native GUI app with
#                        no CLI/API; Windows UI Automation — GUI analog of spawn-CLI.
#                        FRAGILE + Windows-only + needs a live GUI session.
#   workbuddy_api     -> legacy config-driven HTTP passthrough (openai/anthropic
#                        dialect) — NON-compliant unless you own the API key
#   workbuddy_cli     -> spawn a Workbuddy CLI subprocess (compliant)
#   workbuddy_gui     -> Windows UI Automation of the Workbuddy desktop (compliant)
#   workbuddy         -> HYBRID: prefer CLI, fall back to GUI (compliant, recommended)
#   mock              -> local, ToS-safe echo/static/fail/dead (dev, demo, e2e tests)
PROVIDER_CLASSES: dict[str, type[Provider]] = {
    "cursor": CursorProvider,
    "antigravity": AntigravityProvider,
    "antigravity_cli": AntigravityCliProvider,
    "codex_cli": CodexCliProvider,
    "claude_code_cli": ClaudeCodeCliProvider,
    "opencode_cli": OpenCodeCliProvider,
    # 裁定 204：本机 OAuth → 它自己的 OpenAI 兼容口（带工具），模型名取上游自报。
    "opencode_api": OpencodeApiProvider,
    # 裁定 204：本机 OAuth → Gemini 的 generateContent（原生 functionCall → tool_calls）。
    "gemini_api": GeminiApiProvider,
    "kiro_cli": KiroCliProvider,
    "cline_cli": ClineCliProvider,
    # 用户 10-31 令「能变成 API 的都做完」：Qoder 自己那扇签名的门
    # （`gateway.qoder.com.cn/algo/...`，设备码 PKCE 自登录 + COSY 签名 SSE）。
    # 回退到 ACP 只要把 `transport: acp` 加回 config —— 上面那行分支优先于这张表。
    "qoder": QoderProvider,
    "marvis_cli": MarvisCliProvider,
    "marvis_gui": MarvisGuiProvider,
    "workbuddy_api": WorkbuddyApiProvider,
    "workbuddy_cli": WorkbuddyCliProvider,
    "workbuddy_gui": WorkbuddyGuiProvider,
    # 2026-10-14: CLI 腿（--tools "" 纯文本）已退役，workbuddy 键现在绑定
    # CodeBuddy HTTP 通道（原 workbuddy_api 改名）。Hybrid 类保留在代码库供回滚。
    "workbuddy": WorkbuddyApiProvider,
    "mock": MockProvider,
}


def _matches(pattern: str, model: str) -> bool:
    if not pattern:
        return False
    if pattern == model:
        return True
    return fnmatch.fnmatch(model, pattern)


class Registry:
    def __init__(self, config: dict, http: httpx.AsyncClient):
        self.config = config
        self.http = http
        self.providers: dict[str, Provider] = {}
        self.model_to_provider: dict[str, str] = {}
        self.aliases: dict[str, str] = config.get("aliases", {})
        self.rules: list[dict] = config.get("routing", {}).get("rules", [])
        # Model-list refresh bookkeeping (see ensure_models_refreshed).
        self._models_lock = asyncio.Lock()
        self._models_refreshed = False
        self._models_attempt = 0.0
        self._models_passes = 0

    async def build(self):
        for name, pcfg in self.config.get("providers", {}).items():
            if not pcfg.get("enabled", True):
                continue
            # `transport: acp` is the operator's switch for "this leg can take our tools
            # through an ACP session" (see docs/tasks/quota-leg-tools-via-mcp-task.md).
            # It stays a config decision: adding such a leg never means editing a table.
            if str(pcfg.get("transport") or "").lower() == "acp":
                prov = AcpProvider({**pcfg, "leg": name}, self.http)
            else:
                prov = PROVIDER_CLASSES[name](pcfg, self.http)
            await prov.discover_accounts()
            self.providers[name] = prov
            for m in prov.served_models:
                self.model_to_provider[m] = name
        return self

    async def ensure_models_refreshed(self) -> None:
        """Refresh every provider's model catalog from upstream, retrying until no leg is
        left with nothing to offer.

        Safe to call concurrently (e.g. from the startup task and the first
        ``/v1/models`` request) — the lock + flag guarantee a single pass at a time.
        Failures are swallowed so the seed catalog keeps routing working.

        One pass is not enough for a leg whose only name source is upstream: measured
        2026-10-31 16:47, the hub booted with the Settings → VPN node down, Antigravity's
        ``fetchAvailableModels`` failed, and because that leg ships no seed list (deliberately)
        it kept zero models while its card still read signed-in and live — until a restart.
        So the flag stays down while a live leg with an account still has no catalog, and
        ``models_refresh_task`` re-arms within ``_MODELS_RETRY_AFTER`` and a bounded number
        of passes.
        """
        if self._models_refreshed:
            return
        async with self._models_lock:
            if self._models_refreshed:
                return
            self._models_attempt = time.monotonic()
            self._models_passes += 1
            await self._refresh_all_models()
            pending = self._legs_without_catalog()
            self._models_refreshed = not pending or self._models_passes >= _MODELS_MAX_PASSES
            if pending and self._models_refreshed:
                logger.info(
                    "model catalog still empty for %s after %d passes; giving up until next sign-in",
                    ", ".join(pending), self._models_passes,
                )

    #: How long to leave a leg with no catalog alone before asking upstream again (the
    #: total is capped by ``_MODELS_MAX_PASSES``). The floor keeps a picker poll from
    #: re-spawning a slow CLI leg every second.
    _MODELS_RETRY_AFTER = 60.0

    def _legs_without_catalog(self) -> list[str]:
        """Legs that *should* be able to answer: a live credential, a schedulable account,
        and no model to offer. Anything else is not a retry candidate — an unconfigured
        card or a leg with no account must not be polled forever."""
        out: list[str] = []
        for name, prov in self.providers.items():
            if prov.served_models or not any(a.schedulable() for a in prov.accounts):
                continue
            if self.credential_health(prov)[0]:
                out.append(name)
        return out

    def models_refresh_task(self) -> asyncio.Task:
        """The model-refresh task for this process, created on first ask and re-armed
        while a live leg still has no catalog.

        A reader that only wants the catalog can wait on this with a short timeout
        instead of doing the work itself — so a slow leg makes the answer *later*, never
        makes the request hang, and cancelling a waiter cannot take the refresh down
        with it.
        """
        task = getattr(self, "_refresh_task", None)
        if task is None:
            self._refresh_task = asyncio.create_task(self.ensure_models_refreshed())
        elif task.done() and not self._models_refreshed and (
            time.monotonic() - self._models_attempt >= self._MODELS_RETRY_AFTER
        ):
            self._refresh_task = asyncio.create_task(self.ensure_models_refreshed())
        return self._refresh_task

    async def _refresh_all_models(self) -> None:
        """Refresh every leg at once, not one after another.

        Measured 2026-10-29 on this machine: the spawn-based legs boot their own process
        before they answer (cline ~10 s, qwen ~25 s). Run in sequence they pushed the
        whole step past 35 s, and `/v1/models` waits on it — so a desktop that opened
        during those seconds saw a picker with nothing in it. Each leg is independent,
        so they run together and the slowest one sets the cost.
        """
        names = list(self.providers)
        await asyncio.gather(
            *(self._refresh_provider_models(name) for name in names),
            return_exceptions=True,
        )

    async def _refresh_provider_models(self, name: str) -> None:
        prov = self.providers.get(name)
        if prov is None:
            return
        acc = next((a for a in prov.accounts if a.schedulable()), None)
        if acc is None:
            # No usable account yet (e.g. token not injected) — keep seed.
            return
        result = await prov.refresh_models(acc)
        if result is not None:
            self._rebuild_model_index()

    def _rebuild_model_index(self) -> None:
        """Rebuild model_to_provider from the (possibly updated) served_models."""
        self.model_to_provider = {}
        for name, prov in self.providers.items():
            for m in prov.served_models:
                self.model_to_provider[m] = name

    def resolve(self, model: str) -> tuple:
        """Return (provider_or_list, canonical_model). Raises KeyError if unknown."""
        # 1) routing rules match on the RAW requested name (so "auto" can fan out)
        for rule in self.rules:
            if _matches(rule.get("match", ""), model):
                provs = rule.get("providers") or [rule["provider"]]
                target = rule.get("model", model)
                if target != model and target in self.model_to_provider:
                    model = target
                return provs, model
        # 2) alias rename (concrete model rename)
        model = self.aliases.get(model, model)
        if model in self.model_to_provider:
            return self.model_to_provider[model], model
        # 3) allow "provider/anything" even if not pre-declared
        if "/" in model:
            prov = model.split("/", 1)[0]
            if prov in self.providers:
                return prov, model
        raise KeyError(f"no route for model '{model}'")

    def credential_health(self, prov) -> tuple[bool, str]:
        """What a provider's own credential says right now — local, no network.

        Providers that hold a desktop-app credential implement `credential_health()`
        returning `(live, reason)`; anything else is assumed live, because "we did
        not ask" must not be reported as "it is broken".
        """
        probe = getattr(prov, "credential_health", None)
        if not callable(probe):
            return True, "ok"
        try:
            live, reason = probe()
            return bool(live), str(reason or ("live" if live else "unavailable"))
        except Exception as e:  # a broken probe is not evidence the leg works
            return False, f"probe_error:{type(e).__name__}"

    def _tuning_of(self, prov, model: str) -> dict:
        """One model's own adjustable surface, as the leg reports it.

        A leg that cannot answer is a leg that said nothing — never a leg that said
        "no choices" — so a broken `tuning()` falls back to the empty dict and the
        picker keeps its generic controls.
        """
        probe = getattr(prov, "tuning", None)
        if not callable(probe):
            return {}
        try:
            answer = probe(model)
        except Exception:  # noqa: BLE001 - a catalog row must not fail on a knob
            return {}
        return answer if isinstance(answer, dict) else {}

    def list_models(self, selectable_only: bool = False) -> list[dict]:
        out = []
        seen = set()
        now = int(time.time())
        for m in list(self.model_to_provider) + list(self.aliases):
            if m in seen:
                continue
            seen.add(m)
            prov_name = self.model_to_provider.get(m) or (m.split("/", 1)[0] if "/" in m else None)
            # aliases map to another model id; resolve provider via target
            if m in self.aliases:
                target = self.aliases[m]
                prov_name = self.model_to_provider.get(target, prov_name)
            prov = self.providers.get(prov_name) if prov_name else None
            caps = prov.capabilities().as_dict() if prov else {}
            live, reason = self.credential_health(prov) if prov else (False, "unknown_provider")
            refused = model_health.quarantined(m)
            # A model is *selectable* only when it can actually serve an agent turn.
            # The owner ruled on 2026-10-12 that no surface needs a `tools=False` leg
            # (L1, L2 and cron all need tool calls to come back into our loop), so a
            # text-only transport is not pickable — it stays usable as an offload
            # channel. Dead credentials drop out of that view too, and reappear on
            # their own once the desktop app renews them: no name list is involved.
            # A third way an id is not pickable: the upstream itself refused it twice
            # (`model_rejected`) — the config says the leg serves it, the traffic says
            # it does not right now, and the traffic is the measurement.
            usable = live and bool(caps.get("tools")) and not refused
            # Filtering is a *view*, not the endpoint's default. `/v1/models` is the
            # OpenAI-compat catalog and `plobi.quota.gateway` derives the quota apps
            # from it; silently shrinking it would make those surfaces report "no
            # such model" for legs that are merely expired. Every row carries its own
            # verdict instead, so a consumer that wants the strict view asks for it.
            if not usable and selectable_only:
                continue
            # A cooling leg is not "unknown": it is spent until a known moment. On
            # 2026-11-05 the opencode free pool took a 429 and parked for 21 hours, yet
            # this table still reported 41 models as live/ok until the first chat attempt,
            # so every surface promised "usable" and then ate an undated 503. Carrying the
            # remaining seconds and the end time on the row lets any reader answer both
            # "why can this not be picked" and "when does it come back".
            _probe = getattr(prov, "quota_retry_after", None) if prov else None
            retry_after = None
            if callable(_probe) and reason == "quota_exhausted":
                try:
                    retry_after = int(_probe())
                except Exception:  # one row's metadata must not fail the whole catalog
                    retry_after = None
                if retry_after is not None and retry_after <= 0:
                    retry_after = None
            out.append(
                {
                    "id": m,
                    "object": "model",
                    "created": now,
                    "owned_by": "quota_hub",
                    "permission": [],
                    "root": m,
                    "parent": None,
                    # Plobi / Plobi extensions (OpenAI clients ignore unknown keys)
                    "provider": prov_name,
                    "capabilities": caps,
                    # What this model itself says the user may change: its thinking levels,
                    # its context windows, its output cap. `{}` = the leg did not say
                    # (see `Provider.tuning`), which the picker reads as "keep the generic
                    # controls", not as "nothing is adjustable".
                    "tuning": self._tuning_of(prov, m),
                    "live": live,
                    "live_reason": reason,
                    "selectable": usable,
                    "unselectable_reason": (
                        ""
                        if usable
                        else ("model_rejected" if refused else (reason if not live else "no_tools"))
                    ),
                    # When does a cooling leg come back: seconds left plus a readable end
                    # time. The machine code stays in unselectable_reason ("quota_exhausted")
                    # so existing string matching keeps working; these two keys only add the
                    # deadline, letting a badge say "allowance spent, back at {available_at}"
                    # instead of an unavailable with no end.
                    "retry_after_seconds": retry_after,
                    "available_at": (
                        time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now + retry_after))
                        if retry_after
                        else None
                    ),
                }
            )
        return out

    async def refresh_quotas(self) -> None:
        """Poll every schedulable account's quota summary (best effort).

        One account's failure never stops the others and never raises out of
        here: the health endpoint must keep rendering the last snapshot.
        """
        for prov in self.providers.values():
            for acc in list(prov.accounts):
                if not acc.schedulable():
                    continue
                try:
                    await prov.refresh_quota(acc)
                except Exception:
                    continue
