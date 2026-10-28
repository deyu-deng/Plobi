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
from .providers.apps.workbuddy import (
    WorkbuddyApiProvider,
    WorkbuddyCliProvider,
    WorkbuddyGuiProvider,
)
from .providers.base import Provider
from .providers.mock import MockProvider

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
#   cline_cli         -> Cline CLI (`cline --json`) NDJSON answer stream
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
        """Refresh every provider's model catalog from upstream exactly once.

        Safe to call concurrently (e.g. from the startup task and the first
        ``/v1/models`` request) — the lock + flag guarantee a single refresh.
        Failures are swallowed so the seed catalog keeps routing working.
        """
        if self._models_refreshed:
            return
        async with self._models_lock:
            if self._models_refreshed:
                return
            await self._refresh_all_models()
            self._models_refreshed = True

    async def _refresh_all_models(self) -> None:
        for name in self.providers:
            try:
                await self._refresh_provider_models(name)
            except Exception:
                pass

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
                    "live": live,
                    "live_reason": reason,
                    "selectable": usable,
                    "unselectable_reason": (
                        ""
                        if usable
                        else ("model_rejected" if refused else (reason if not live else "no_tools"))
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
