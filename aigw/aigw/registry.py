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

import fnmatch
import time

import httpx

from .providers.base import Provider
from .providers.cursor import CursorProvider
from .providers.antigravity import AntigravityProvider
from .providers.workbuddy import WorkbuddyProvider
from .providers.mock import MockProvider

# All upstream adapters are registered here. Add a new desktop app by writing a
# Provider subclass and listing it below; config keys (e.g. "cursor") map 1:1 to
# PROVIDER_CLASSES keys.
#   cursor       -> Cursor (gRPC/Connect protobuf; token OK, chat needs .proto)
#   antigravity  -> Google Antigravity Cloud Code (Gemini; envelope # VERIFY)
#   workbuddy    -> generic config-driven passthrough (openai/anthropic dialect)
#   mock         -> local, ToS-safe echo/static/fail/dead (dev, demo, e2e tests)
PROVIDER_CLASSES: dict[str, type[Provider]] = {
    "cursor": CursorProvider,
    "antigravity": AntigravityProvider,
    "workbuddy": WorkbuddyProvider,
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

    async def build(self):
        for name, pcfg in self.config.get("providers", {}).items():
            if not pcfg.get("enabled", True):
                continue
            cls = PROVIDER_CLASSES[name]
            prov = cls(pcfg, self.http)
            await prov.discover_accounts()
            self.providers[name] = prov
            for m in prov.served_models:
                self.model_to_provider[m] = name
        return self

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

    def list_models(self) -> list[dict]:
        out = []
        seen = set()
        now = int(time.time())
        for m in list(self.model_to_provider) + list(self.aliases):
            if m in seen:
                continue
            seen.add(m)
            out.append({
                "id": m,
                "object": "model",
                "created": now,
                "owned_by": "aigw",
                "permission": [],
                "root": m,
                "parent": None,
            })
        return out
