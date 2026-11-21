"""Provider profile base class.

A ProviderProfile declares everything about an inference provider in one place:
auth, endpoints, client quirks, request-time quirks. The transport reads this
instead of receiving 20+ boolean flags.

Provider profiles are DECLARATIVE — they describe the provider's behavior.
They do NOT own client construction, credential rotation, or streaming.
Those stay on AIAgent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Sentinel for "omit temperature entirely" (Kimi: server manages it)
OMIT_TEMPERATURE = object()


def _profile_user_agent() -> str:
    """Return a ``plobi-cli/<version>`` UA string, with a stable fallback.

    Used by ``ProviderProfile.fetch_models`` so the catalog probe is not
    served the default ``Python-urllib/<ver>`` UA — some providers
    (OpenCode Zen, etc.) sit behind a WAF that returns 403 for that.
    """
    try:
        from plobi_cli import __version__ as _ver  # lazy: avoid layer cycle at import time
        return f"plobi-cli/{_ver}"
    except Exception:
        return "plobi-cli"


@dataclass
class ProviderProfile:
    """Base provider profile — subclass or instantiate with overrides."""

    # ── Identity ─────────────────────────────────────────────
    name: str
    api_mode: str = "chat_completions"
    aliases: tuple = ()

    # 组归属：本条 profile 属于某个 ``ProviderGroup`` 时写组 id（见文件末尾）。
    # 空串 = 不属于任何组。组级凭据/端点/请求字段归组的 ``owner``；成员只在
    # 「名字」这件事上是独立的。
    group: str = ""

    # ── Human-readable metadata ───────────────────────────────
    display_name: str = ""       # e.g. "GMI Cloud" — shown in picker/labels
    description: str = ""        # e.g. "GMI Cloud (multi-model direct API)" — picker subtitle
    signup_url: str = ""         # e.g. "https://www.gmicloud.ai/" — shown during setup

    # ── Auth & endpoints ─────────────────────────────────────
    env_vars: tuple = ()
    base_url: str = ""
    models_url: str = ""  # explicit models endpoint; falls back to {base_url}/models
    auth_type: str = "api_key"   # api_key|oauth_device_code|oauth_external|copilot|aws_sdk
    supports_health_check: bool = True  # False → doctor skips /models probe for this provider

    # ── Vision support ────────────────────────────────────────
    # True when the provider's API accepts image content inside
    # tool-result messages natively.  Set on providers that expose
    # multimodal models via tool results (Anthropic Messages API,
    # OpenAI Chat Completions, Gemini, MiniMax, etc.).
    # Falls back to model-catalog lookup when False and the provider
    # has no registered profile.
    supports_vision: bool = False

    # True when the provider's API accepts list-type tool message
    # content (multipart with image_url parts).  Defaults to True for
    # backward compatibility.  Set to False for providers that accept
    # multimodal user messages but reject list-type tool content
    # (e.g. Xiaomi MiMo, which returns 400 "text is not set").
    supports_vision_tool_messages: bool = True

    # ── Model catalog ─────────────────────────────────────────
    # fallback_models: curated list shown in /model picker when live fetch fails.
    # Only agentic models that support tool calling should appear here.
    fallback_models: tuple = ()

    # hostname: base hostname for URL→provider reverse-mapping in model_metadata.py
    # e.g. "api.gmi-serving.com". Derived from base_url when empty.
    hostname: str = ""

    # ── Client-level quirks (set once at client construction) ─
    default_headers: dict[str, str] = field(default_factory=dict)

    # ── Request-level quirks ─────────────────────────────────
    # Temperature: None = use caller's default, OMIT_TEMPERATURE = don't send
    fixed_temperature: Any = None
    default_max_tokens: int | None = None
    default_aux_model: str = (
        ""  # cheap model for auxiliary tasks (compression, vision, etc.)
    )
    # empty = use main model

    # ── Hooks (override in subclass for complex providers) ───

    def get_hostname(self) -> str:
        """Return the provider's base hostname for URL-based detection.

        Uses self.hostname if set explicitly, otherwise derives it from base_url.
        e.g. 'https://api.gmi-serving.com/v1' → 'api.gmi-serving.com'
        """
        if self.hostname:
            return self.hostname
        if self.base_url:
            from urllib.parse import urlparse
            return urlparse(self.base_url).hostname or ""
        return ""

    def prepare_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Provider-specific message preprocessing.

        Called AFTER codex field sanitization, BEFORE developer role swap.
        Default: pass-through.
        """
        return messages

    def build_extra_body(
        self, *, session_id: str | None = None, **context: Any
    ) -> dict[str, Any]:
        """Provider-specific extra_body fields.

        Merged into the API kwargs extra_body. Default: empty dict.
        """
        return {}

    def build_api_kwargs_extras(
        self,
        *,
        reasoning_config: dict | None = None,
        **context: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Provider-specific kwargs split between extra_body and top-level api_kwargs.

        Returns (extra_body_additions, top_level_kwargs).
        The transport merges extra_body_additions into extra_body, and
        top_level_kwargs directly into api_kwargs.

        This split exists because some providers put reasoning config in
        extra_body (OpenRouter: extra_body.reasoning) while others put it
        as top-level api_kwargs (Kimi: api_kwargs.reasoning_effort).

        Default: ({}, {}).
        """
        return {}, {}

    def get_max_tokens(self, model: str | None) -> int | None:
        """Return the default max_tokens cap for *model*.

        Overrideable hook for providers that need per-model output caps —
        e.g. a relay that fronts several upstream backends, each with a
        different completion-token limit. The transport calls this when
        the user hasn't set an explicit max_tokens.

        Default: return self.default_max_tokens (the static profile field),
        ignoring the model name. Override in a subclass to vary the cap
        per-model.
        """
        return self.default_max_tokens

    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 8.0,
    ) -> list[str] | None:
        """Fetch the live model list from the provider's models endpoint.

        Returns a list of model ID strings, or None if the fetch failed or
        the provider does not support live model listing.

        Resolution order for the endpoint URL:
          1. self.models_url  (explicit override — use when the models
             endpoint differs from the inference base URL, e.g. OpenRouter
             exposes a public catalog at /api/v1/models while inference is
             at /api/v1)
          2. base_url (caller override — user-configured model.base_url)
          3. self.base_url + "/models"  (standard OpenAI-compat fallback)

        The default implementation sends Bearer auth when api_key is given
        and forwards self.default_headers. Override to customise auth, path,
        response shape, or to return None for providers with no REST catalog.

        Callers must always fall back to the static _PROVIDER_MODELS list
        when this returns None.
        """
        effective_base = base_url or self.base_url
        url = (self.models_url or "").strip()
        if not url:
            if not effective_base:
                return None
            url = effective_base.rstrip("/") + "/models"

        import json
        import urllib.request

        from plobi_cli.urllib_security import open_credentialed_url

        req = urllib.request.Request(url)
        if api_key:
            req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Accept", "application/json")
        # Some providers (e.g. OpenCode Zen) sit behind a WAF that blocks
        # the default ``Python-urllib/<ver>`` User-Agent.  Set a generic
        # plobi-cli UA so the catalog endpoint is reachable.
        req.add_header("User-Agent", _profile_user_agent())
        for k, v in self.default_headers.items():
            req.add_header(k, v)

        try:
            with open_credentialed_url(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
            items = data if isinstance(data, list) else data.get("data", [])
            return [m["id"] for m in items if isinstance(m, dict) and "id" in m]
        except Exception as exc:
            logger.debug("fetch_models(%s): %s", self.name, exc)
            return None


@dataclass(frozen=True)
class ProviderGroup:
    """一个 provider **组节点**：组级行为归 ``owner``，成员各是一个 provider。

    为什么要有这一层：有些腿的关系不是「第 N 个 provider」，而是「一个控制面 + N 个
    可选的软件名」。它们共用一份凭据、一个端点、一套请求字段，却在 picker 里必须
    是 N 行。扁平注册表里这 N 个名字和别家 provider 同层，「控制面」这件事就没有
    对象可指 —— 判断只能散成一堆 ``is_hub_software(...)``。组节点把「谁承载组级
    行为、谁是成员」写成结构，散落判断收敛成 ``owns()`` 一处。

    约定：

    * ``owner`` 承载组级行为（凭据兜底、``base_url``、请求字段）。它**不必**是
      ``_REGISTRY`` 的成员 —— 通常是 ``get_provider_profile()`` 查不到时的兜底身份，
      这样它不会平白多出一行可选 provider。
    * ``members`` 是组里**各自可选**的 provider 名字，顺序即 UI 顺序；成员在
      ``_REGISTRY`` 里各有一条 profile，``profile.group`` 指回 ``group_id``。
    * 组节点本身**不进** ``list_providers()``：组是结构，不是一行可选 provider。
    """

    group_id: str
    display_name: str
    description: str
    owner: ProviderProfile | None = None
    members: tuple[str, ...] = ()
    member_labels: dict[str, str] = field(default_factory=dict)
    show_owner_in_picker: bool = False

    # ── Name resolution ──────────────────────────────────────

    @staticmethod
    def _key(name: Any) -> str:
        return str(name or "").strip().lower()

    def owns_member(self, name: Any) -> bool:
        """这个名字是不是本组的**成员**（可单独选的那几家）。"""
        return self._key(name) in self.members

    def owns_owner(self, name: Any) -> bool:
        """这个名字是不是**组自己**（组 id 或 owner 名）。"""
        key = self._key(name)
        if not key:
            return False
        return key == self._key(self.group_id) or (
            self.owner is not None and key == self._key(self.owner.name)
        )

    def owns(self, name: Any) -> bool:
        """这个名字归本组管吗（成员或组自己）。

        「这家实际上由本组在服务」只需要问这一句 —— 别再散着比名字。
        """
        return self.owns_member(name) or self.owns_owner(name)

    @property
    def member_ids(self) -> frozenset[str]:
        return frozenset(self.members)

    def member_label(self, name: Any) -> str:
        """成员在屏上念的名字；不认识的成员退回它自己的 id。"""
        key = self._key(name)
        return self.member_labels.get(key) or key

    def member_for_model(self, model_id: Any) -> str | None:
        """模型 id 的前缀 ⇒ 组里服务它的那家成员；认不出来返回 ``None``。

        组的模型 id 约定是 ``<member>/<model>``，所以「哪一家在服务这个模型」，
        数据里本来就有。
        """
        prefix = str(model_id or "").strip().split("/", 1)[0].strip().lower()
        return prefix if prefix in self.members else None

    def models_for_member(self, member: Any, models: list[str]) -> list[str]:
        """组的总清单 ⇒ 这家成员自己那几条。

        组 id 直接调用时给全量（内部路径要的就是一张总表）；成员名调用时只留
        自己前缀下的，否则切到某一家会看见别家的模型。
        """
        key = self._key(member)
        if not self.owns_member(key):
            return list(models)
        return [
            m for m in models
            if str(m or "").strip().split("/", 1)[0].strip().lower() == key
        ]

    def transport_for(self, name: Any) -> str:
        """配置里那个名字 ⇒ **走哪条腿**。

        组级凭据、端点、请求字段都挂在 ``owner`` 身上，所以成员（软件名）要落到
        ``group_id`` 那条腿；组自己与别的名字原样返回。这是旧的
        ``transport_for_software`` 唯一还想表达的意思：不问「叫什么」，只问「谁在跑」。
        """
        key = self._key(name)
        if self.owns_member(key):
            return self._key(self.group_id)
        return key
