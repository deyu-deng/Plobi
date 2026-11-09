"""Provider/model inventory context — shared substrate for the dashboard
``/api/model/options``, the TUI ``model.options``/``model.save_key``
JSON-RPC handlers, and the interactive picker.

Before this module the three call-sites each duplicated:

1. The 17-LOC config-slice that pulls ``model.{default,name,provider,base_url}``,
   ``providers:``, and ``custom_providers:`` out of ``load_config()``;
2. The call into ``list_authenticated_providers`` with the resulting kwargs;
3. (TUI only) a 45-LOC post-pass that merges authenticated rows with
   unconfigured ``CANONICAL_PROVIDERS`` rows and emits ``authenticated``/
   ``auth_type``/``key_env``/``warning`` hints for the picker UI.

Consolidating those three steps into one entry point eliminates two bugs
the duplicates were hiding:

- The dashboard read ``cfg.get("custom_providers")`` directly, missing the
  v12+ keyed ``providers:`` form (which the TUI handled via
  ``get_compatible_custom_providers``).
- The TUI's canonical-merge keyed on ``is_user_defined`` to decide
  ordering. Section 3 of ``list_authenticated_providers`` sets
  ``is_user_defined=True`` even for canonical slugs that appear in the
  ``providers:`` config dict, which silently demoted them to the tail of
  the picker. ``_reorder_canonical`` keys on slug membership instead.

Substrate facts (verified May 2026):
- ``list_authenticated_providers`` already populates each row's
  ``models`` from the curated catalog (same source as the picker). Do
  NOT call ``provider_model_ids()`` per row to "freshen" — that bypasses
  curation and pulls in non-agentic models (Nous /models returns ~400
  IDs including TTS, embeddings, rerankers, image/video generators).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Optional


#: The local quota hub's own provider slug.  A routing value, never display copy —
#: 裁定 45.4 keeps the word off the screen, so the renderer only ever *matches*
#: it (the row it matches is the carrier every hub-backed pick joins against).
QUOTA_HUB_SLUG = "aigw"


# ─── Public types ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConfigContext:
    """Snapshot of the model + provider config every inventory caller
    needs. Built once via ``load_picker_context()``; the TUI overlays
    live agent state via ``with_overrides()`` before passing through.
    """

    current_provider: str
    current_model: str
    current_base_url: str
    user_providers: dict
    custom_providers: list

    def with_overrides(
        self,
        *,
        current_provider: Optional[str] = None,
        current_model: Optional[str] = None,
        current_base_url: Optional[str] = None,
    ) -> "ConfigContext":
        """Return a copy with truthy overrides applied.

        Truthy-only because the TUI reads agent attributes that may be
        empty strings before an agent is spawned — empties must NOT
        clobber the disk-config values.
        """
        kw: dict = {}
        if current_provider:
            kw["current_provider"] = current_provider
        if current_model:
            kw["current_model"] = current_model
        if current_base_url:
            kw["current_base_url"] = current_base_url
        return replace(self, **kw) if kw else self


def load_picker_context() -> ConfigContext:
    """Load the disk-config snapshot every consumer needs.

    Replaces the inline 17-LOC config-slice that ``web_server.py`` and
    ``tui_gateway/server.py`` (×2 sites) used to do.
    """
    from plobi_cli.config import get_compatible_custom_providers, load_config

    cfg = load_config()
    model_cfg = cfg.get("model", {})
    if isinstance(model_cfg, dict):
        current_model = model_cfg.get("default", model_cfg.get("name", "")) or ""
        current_provider = model_cfg.get("provider", "") or ""
        current_base_url = model_cfg.get("base_url", "") or ""
    else:
        # config.model can be a bare string in older configs.
        current_model = str(model_cfg) if model_cfg else ""
        current_provider = ""
        current_base_url = ""
    raw = cfg.get("providers")
    return ConfigContext(
        current_provider=current_provider,
        current_model=current_model,
        current_base_url=current_base_url,
        user_providers=raw if isinstance(raw, dict) else {},
        custom_providers=get_compatible_custom_providers(cfg),
    )


# ─── Public: payload builder ────────────────────────────────────────────


def build_models_payload(
    ctx: ConfigContext,
    *,
    explicit_only: bool = False,
    include_unconfigured: bool = False,
    picker_hints: bool = False,
    canonical_order: bool = False,
    pricing: bool = False,
    capabilities: bool = False,
    force_fresh_nous_tier: bool = False,
    refresh: bool = False,
    probe_custom_providers: bool = True,
    probe_current_custom_provider: bool = False,
    max_models: int | None = None,
) -> dict:
    """Build the ``{providers, model, provider}`` shape every consumer
    needs from a single substrate call.

    Flags:
    - ``explicit_only``: keep only providers the user explicitly configured
      (current provider, providers from config, or providers backed by
      provider-specific env vars). This hides ambient / auto-seeded
      credentials from desktop chat pickers.
    - ``include_unconfigured``: append ``CANONICAL_PROVIDERS`` rows that
      ``list_authenticated_providers`` didn't emit (TUI uses this to show
      the full provider universe in the picker).
    - ``picker_hints``: add ``authenticated``/``auth_type``/``key_env``/
      ``warning`` per row (TUI ``ModelPickerDialog`` shape).
    - ``canonical_order``: reorder canonical-slug rows to
      ``CANONICAL_PROVIDERS`` declaration order; truly-custom rows go
      last (TUI display order).
    - ``pricing``: enrich each row with formatted per-model pricing and,
      for Nous, ``free_tier``/``unavailable_models`` so the GUI picker can
      show $/Mtok columns and gate paid models on free accounts —
      mirroring the ``plobi model`` CLI picker. Adds network calls
      (pricing fetch + Nous tier check); only set for interactive pickers.
    - ``capabilities``: add a per-row ``capabilities`` map
      ``{model: {fast, reasoning}}`` so pickers can gate the model-options
      controls (fast toggle / reasoning) to what each model actually
      supports, instead of offering knobs the backend would reject.
    - ``force_fresh_nous_tier``: bypass the short Nous free-tier cache when
      selecting Portal-recommended Nous models and applying tier gating. Keep
      this false for UI picker opens; explicit auth/model flows can opt in
      when they need freshly-purchased credits to show up immediately.
    - ``refresh``: bust the per-provider model-id disk cache so every row
      re-fetches its live catalog. Set only for an explicit user-triggered
      "refresh models" action; normal picker opens leave it false to stay
      snappy on the 1h cache.
    - ``probe_custom_providers``: allow saved custom/provider endpoints to
      run live ``/models`` discovery while building the payload. GUI picker
      opens should leave this false unless the user explicitly refreshes; the
      row can still render its configured model immediately, and slow/offline
      local endpoints no longer block the dialog.
    - ``probe_current_custom_provider``: when ``probe_custom_providers`` is
      false, still live-probe the current custom endpoint. This keeps normal
      GUI/TUI picker opens fast while making the active custom provider's model
      list match the classic CLI picker.
    """
    from plobi_cli.model_switch import list_authenticated_providers

    rows = list_authenticated_providers(
        current_provider=ctx.current_provider,
        current_base_url=ctx.current_base_url,
        current_model=ctx.current_model,
        user_providers=ctx.user_providers,
        custom_providers=ctx.custom_providers,
        force_fresh_nous_tier=force_fresh_nous_tier,
        max_models=max_models,
        refresh=refresh,
        probe_custom_providers=probe_custom_providers,
        probe_current_custom_provider=probe_current_custom_provider,
    )

    moa_row = _moa_provider_row(ctx.current_provider)
    if moa_row is not None:
        rows = [moa_row] + [r for r in rows if str(r.get("slug", "")).lower() != "moa"]

    # A provider whose only remaining credential is a deleted env var must not
    # render as a selectable group (measured 2026-09-29: a DEEPSEEK group with 5
    # models after DEEPSEEK_API_KEY was removed from .env, the shell and
    # launchctl). Runs before the explicit filter so it applies to EVERY picker
    # surface — the desktop's chat/settings pickers (explicit_only=1) and the
    # session-scoped ``model.options`` RPC (no explicit_only) alike — while the
    # setup universe appended below via ``include_unconfigured`` is untouched.
    rows = _drop_unusable_credential_rows(rows, ctx)

    if explicit_only:
        rows = _filter_explicit_provider_rows(rows, ctx)
        # Desktop chat pickers request the explicit subset without the full
        # unconfigured provider universe. If the configured current provider
        # has lost its credential, list_authenticated_providers() omits it;
        # keep that one row visible so the UI can show the saved selection and
        # a re-auth affordance instead of appearing to jump to another provider.
        rows = list(rows) + _append_unconfigured_rows(
            rows, ctx, current_only=True
        )

    # --- The quota hub's own row ---
    # 裁定 46: the hub's catalogue is the single source of truth for the
    # subscription legs, and the hub is this machine's own local service — not
    # something the user has to declare in config.yaml to be routable (the
    # `model-providers/aigw` profile resolves its key and base URL by itself,
    # 裁定 81).  Its row is what the desktop's picker joins against, so it is
    # added here — AFTER the explicit-only filter, because that filter asks
    # "did the user configure this provider?" and the hub is not an answer to
    # that question.
    #
    # Measured 2026-11-08 on this machine: the hub answered 58 selectable ids
    # while the payload carried no hub row at all, so the desktop reported
    # "This channel is not currently offered by the gateway" for a gateway that
    # was offering 58 — and the switches the hub itself calls "connected"
    # changed nothing in the picker.
    if not any(str(row.get("slug", "")).lower() == QUOTA_HUB_SLUG for row in rows):
        hub_row = _quota_hub_row()

        if hub_row is not None:
            rows.append(hub_row)

    # --- Deduplicate: remove models from aggregators that overlap with
    # user-defined providers.  When a local proxy (e.g. litellm-proxy)
    # serves a model whose name also appears in an aggregator's curated
    # catalog, the picker would show the model under both providers.
    # Selecting it from the aggregator row sets model.provider to the
    # aggregator (e.g. openrouter) instead of the user's proxy — silently
    # breaking the call.  Filtering at the payload level keeps the
    # aggregator rows honest: they only show models the user can't get
    # from a more-specific provider.  (#45954)
    try:
        from plobi_cli.providers import is_routing_aggregator as _is_routing_aggregator
    except Exception:
        _is_routing_aggregator = None  # type: ignore[assignment]

    if _is_routing_aggregator is not None:
        user_models: set[str] = set()
        for row in rows:
            if row.get("is_user_defined"):
                user_models.update(m.lower() for m in (row.get("models") or []))
        if user_models:
            for row in rows:
                # A user's own configured provider is never an "aggregator
                # duplicate" of itself: user_models is built from these very
                # rows, and is_routing_aggregator() reports True for every
                # custom:* slug.  Without this guard the dedup strips a
                # user-defined custom provider's entire model list (all of it
                # lives in user_models), emptying its picker row.
                if row.get("is_user_defined"):
                    continue
                slug = row.get("slug", "")
                # Only strip overlaps from TRUE routing aggregators (OpenRouter,
                # custom:* proxies). Flat-namespace resellers (opencode-go /
                # opencode-zen) serve every listed model as a first-party model,
                # so their rows must keep models that a user's proxy happens to
                # share a name with — otherwise a subscription provider's own
                # catalog (minimax-m3, glm-5, deepseek-v4-flash, ...) is silently
                # gutted in the picker. (#47077)
                if not _is_routing_aggregator(slug):
                    continue
                original = row.get("models") or []
                filtered = [m for m in original if m.lower() not in user_models]
                if len(filtered) < len(original):
                    row["models"] = filtered
                    row["total_models"] = len(filtered)

    if include_unconfigured:
        rows = list(rows) + [r for r in _append_unconfigured_rows(rows, ctx) if str(r.get("slug", "")).lower() != "moa"]
    if picker_hints:
        _apply_picker_hints(rows)
    if canonical_order:
        rows = _reorder_canonical(rows)
    if pricing:
        _apply_pricing(rows, force_fresh_nous_tier=force_fresh_nous_tier)
    if capabilities:
        _apply_capabilities(rows)

    return {
        "providers": rows,
        "model": ctx.current_model,
        "provider": ctx.current_provider,
    }


#: Plobi 侧上下文预算档（用户 11-08 20:4x 拍板：厂商卖菜单就用厂商的；厂商只有一档
#: 或没写，就按那个模型自己的上限给几档）。These are the rungs; a model's own upper
#: bound is always the top one, because that is the window the turn runs at today.
_BUDGET_RUNGS: tuple[int, ...] = (32768, 65536, 131072, 262144, 524288, 1048576)


def _budget_ladder(ceiling: int | None) -> dict[str, Any] | None:
    """The Plobi-side budget menu for one model: a few rungs under its own ceiling.

    ``None`` means "nothing to choose": a ceiling that leaves a single rung has no
    menu, and a rung **above** the model's real window would be a promise the turn
    cannot keep.
    """
    if not isinstance(ceiling, int) or isinstance(ceiling, bool) or ceiling <= 0:
        return None
    rungs = sorted({rung for rung in _BUDGET_RUNGS if rung <= ceiling} | {ceiling})
    if len(rungs) < 2:
        return None
    return {"adjustable": True, "choices": rungs, "default": ceiling, "max": ceiling}


def _context_menu(published: Any, model: str, meta: Any) -> dict[str, Any] | None:
    """The window menu this model gets — the vendor's own if it sells one, else Plobi's.

    A vendor that sells a **menu** of windows for one model (Qoder: 200K / 400K / 1M)
    wrote the truth, and it goes through untouched. A vendor that writes a single
    window — or writes none at all — leaves the user with no dial at all, which is
    why「调上下文」was missing on the subscription legs (measured 2026-11-08: Zen
    writes one window per model; CodeBuddy's CLI advertises model names only, with
    no window anywhere in its list).

    There, Plobi offers the budget it can actually honour: a few rungs under that
    model's own ceiling, with the ceiling itself as the top rung — so a model nobody
    answered for still runs at exactly the window it runs at today.
    """
    if isinstance(published, dict) and len(published.get("choices") or []) > 1:
        return published

    ceiling: int | None = None
    for candidate in (
        published.get("max") if isinstance(published, dict) else None,
        getattr(meta, "context_window", None) if meta is not None else None,
    ):
        if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate > 0:
            ceiling = candidate
            break

    if ceiling is None:
        # Plobi's own table, then its blanket fallback — the same window the runtime
        # ends up with when nobody published one. No network here: this runs for every
        # model on every open of the model menu, and the full probe chain costs ~1.5 s
        # per model (measured 2026-11-08 on this machine).
        from agent.model_metadata import local_context_length

        ceiling = local_context_length(model)

    return _budget_ladder(ceiling)


def _apply_capabilities(rows: list[dict]) -> None:
    """Attach a ``{model: {fast, reasoning, …}}`` map to each provider row.

    `fast` mirrors ``model_supports_fast_mode`` (the same gate the runtime
    enforces). `reasoning` comes from the models.dev catalog when known and
    defaults to True otherwise — the effort dial is broadly accepted and a
    no-op on models that ignore it, whereas hiding it from a capable-but-
    uncatalogued model is the worse failure.

    For the subscription gateway (`aigw`) the hub itself answers, per model, which
    thinking levels and which context windows that model really has, and those keys
    ride along — a level the vendor does not sell must not be offered.
    """
    from plobi_cli.models import model_supports_fast_mode

    try:
        from agent.models_dev import get_model_capabilities
    except Exception:
        get_model_capabilities = None  # type: ignore[assignment]

    for row in rows:
        slug = row.get("slug") or ""

        # The subscription legs each publish, per model, what that model actually lets
        # the user change (its thinking levels, its windows, its output cap) — read once
        # per payload build, from the hub that already owns the catalog (裁定 46). An
        # empty answer means the hub is not there, not that the model has no knobs.
        hub_tuning: dict[str, dict] = {}
        # The models of a leg that is only *waiting* (a spent bucket that refills on
        # its own) stay in the picker — greyed out, with the time left on them
        # (user, 2026-11-08 21:3x). A leg the user can fix does not: its models are
        # not offered at all (「我配置不了就不应该出现在这个模型框里面呀」).
        hub_waiting: dict[str, dict] = {}
        if slug.lower() == "aigw":
            try:
                from plobi.agents.registry import aigw_api_key, aigw_base_url
                from plobi.quota.gateway import catalog_tuning

                hub_tuning = catalog_tuning(aigw_base_url(), aigw_api_key()) or {}
            except Exception:
                hub_tuning = {}
            try:
                from plobi.agents.registry import aigw_api_key, aigw_base_url
                from plobi_cli.models import hub_waiting_models

                hub_waiting = hub_waiting_models(aigw_api_key(), aigw_base_url()) or {}
            except Exception:
                hub_waiting = {}

        caps: dict[str, dict[str, Any]] = {}

        for model in row.get("models") or []:
            reasoning = True
            meta = None
            if get_model_capabilities is not None and slug:
                try:
                    meta = get_model_capabilities(slug, model)
                    if meta is not None:
                        reasoning = bool(meta.supports_reasoning)
                except Exception:
                    reasoning = True
                    meta = None

            caps[model] = {
                "fast": bool(model_supports_fast_mode(model)),
                "reasoning": reasoning,
            }
            knobs = hub_tuning.get(model) or {}
            for name in ("efforts", "default_effort", "context", "max_output"):
                if knobs.get(name) is not None:
                    caps[model][name] = knobs[name]
            if isinstance(knobs.get("efforts"), list):
                # A model that names its levels is a model that takes them; a leg that
                # lists none keeps the honest default above.
                caps[model]["reasoning"] = bool(knobs["efforts"])

            # 窗口菜单：厂商有菜单就用厂商的；没有就按这个模型自己的上限给 Plobi 侧
            # 的几档（用户 11-08 20:4x 拍板：「调上下文」不能只支持 effort）。
            menu = _context_menu(caps[model].get("context"), model, meta)
            if menu is not None:
                caps[model]["context"] = menu

            # 此刻用不了的模型：只标，不藏（用户 11-08 21:3x 拍板：能修的摘掉，
            # 等恢复的标灰并写剩余时间）。原因码是机器词，屏上由前端翻译。
            waiting = hub_waiting.get(model)
            if waiting:
                caps[model]["unavailable"] = waiting

        row["capabilities"] = caps


# ─── Internal: row post-processing ──────────────────────────────────────


def _append_unconfigured_rows(
    rows: list[dict],
    ctx: ConfigContext,
    *,
    current_only: bool = False,
) -> list[dict]:
    """Build fallback rows for canonical providers missing from ``rows``.

    Most missing canonical providers become empty setup skeletons. The one
    exception is the *current* configured provider: if config.yaml still points
    at it but credentials are presently unavailable, keep a visible row carrying
    the saved model so GUI pickers don't silently snap to some other provider.
    """
    from plobi_cli.auth import PROVIDER_REGISTRY
    from plobi_cli.models import CANONICAL_PROVIDERS, _PROVIDER_LABELS

    seen = {r["slug"].lower() for r in rows}
    cur = (ctx.current_provider or "").lower()
    cur_model = str(ctx.current_model or "").strip()
    extras: list[dict] = []
    for entry in CANONICAL_PROVIDERS:
        if entry.slug.lower() in seen:
            continue
        if current_only and entry.slug.lower() != cur:
            continue
        if entry.slug.lower() == cur:
            cfg = PROVIDER_REGISTRY.get(entry.slug)
            auth_type = cfg.auth_type if cfg else "api_key"
            key_env = (
                cfg.api_key_env_vars[0]
                if (cfg and cfg.api_key_env_vars)
                else ""
            )
            warning = (
                f"Configured provider missing usable credentials; paste {key_env} to reactivate. "
                "Showing the saved model only."
                if auth_type == "api_key" and key_env
                else "Configured provider is not authenticated; run `plobi model` to reactivate. "
                "Showing the saved model only."
            )
            extras.append(
                {
                    "slug": entry.slug,
                    "name": _PROVIDER_LABELS.get(entry.slug, entry.label),
                    "is_current": True,
                    "is_user_defined": False,
                    "models": [cur_model] if cur_model else [],
                    "total_models": 1 if cur_model else 0,
                    "source": "configured-current",
                    "authenticated": False,
                    "auth_type": auth_type,
                    "key_env": key_env,
                    "warning": warning,
                }
            )
            continue
        extras.append(
            {
                "slug": entry.slug,
                "name": _PROVIDER_LABELS.get(entry.slug, entry.label),
                "is_current": entry.slug.lower() == cur,
                "is_user_defined": False,
                "models": [],
                "total_models": 0,
                "source": "canonical",
            }
        )
    return extras


def _filter_explicit_provider_rows(rows: list[dict], ctx: ConfigContext) -> list[dict]:
    """Keep only rows backed by explicit user configuration.

    ``list_authenticated_providers`` intentionally discovers ambient / auto-
    seeded credentials (for example GitHub CLI -> Copilot). Desktop chat model
    pickers want the narrower subset the user explicitly configured for Plobi.
    """
    from plobi_cli.auth import is_provider_explicitly_configured

    current_slug = str(ctx.current_provider or "").strip().lower()
    declared = {str(k).strip().lower() for k in (ctx.user_providers or {})}
    kept: list[dict] = []
    for row in rows:
        slug = str(row.get("slug", "")).strip().lower()
        if not slug:
            continue
        if row.get("is_user_defined"):
            kept.append(row)
            continue
        # A slug the user wrote into config.yaml's ``providers:`` block IS an
        # explicit choice, even when the same slug also exists in
        # PROVIDER_REGISTRY (those rows are built with ``is_user_defined=False``,
        # so without this line the config entry would be honoured for the
        # credential and then silently filtered out of every explicit picker).
        # Measured 2026-10-20: ``providers.aigw`` in config.yaml, but the picker
        # only ever got ``wb-gateway`` — and with no hub row the desktop's
        # quota-backed picks could not be joined back to a provider at all.
        if slug in declared:
            kept.append(row)
            continue
        if current_slug and slug == current_slug:
            kept.append(row)
            continue
        if slug == "moa":
            # MoA is a virtual routing mode, not an independently configured
            # provider. Hide it from explicit-only pickers unless it is the
            # current provider (handled above) or the user explicitly wrote an
            # enabled MoA preset into config.yaml. Use raw config so the
            # DEFAULT_CONFIG preset does not make every desktop picker show MoA.
            if _raw_config_has_enabled_moa_preset():
                kept.append(row)
            continue
        if is_provider_explicitly_configured(slug):
            kept.append(row)
    return kept


def _drop_unusable_credential_rows(rows: list[dict], ctx: ConfigContext) -> list[dict]:
    """Drop picker rows whose only credential evidence is a dead ``env:<VAR>`` pool entry.

    ``list_authenticated_providers`` asks the runtime pool whether a provider is
    usable (``load_pool(slug).has_credentials()``), and the pool deliberately
    *retains* ``env:<VAR>`` entries after the variable disappears — a
    non-destructive read (#9331: a process that merely lacks the var must not
    delete another process's on-disk credential). The visible consequence on
    screen: a provider whose key was deleted still renders as a full model
    group, because ``~/.plobi/auth.json`` still carries
    ``credential_pool.<slug>`` with ``source="env:<VAR>"`` and a stale
    ``last_status``.

    This is the screen-level correction: ignore an ``env:``-sourced pool entry
    whose variable does not resolve to a usable secret *right now* — the same
    rule :func:`plobi_cli.auth.is_provider_explicitly_configured` already applies
    (#55790) — and drop the row when that was its only evidence. It never
    rewrites ``auth.json``; the stale row is ignored, not deleted.

    Deliberately narrow, and fail-open on anything it cannot judge:
      - the current provider's row always survives (a picker that silently
        snaps to another provider is the failure this module guards elsewhere);
      - user-defined rows survive — an endpoint in ``providers:`` /
        ``custom_providers:`` is a choice the user made, key inline or not;
      - a provider with no pool entries survives — its credential may live in
        ``auth.json``'s ``providers`` store or an external file (gh_cli, OAuth);
      - a pool entry from any other source (manual / device_code / PKCE) counts
        as live.

    Setup discovery is untouched: the ``include_unconfigured`` skeletons appended
    later by :func:`_append_unconfigured_rows` are how the setup flow *finds* a
    provider to paste a key into, and they carry no selectable models.
    """
    import os

    from plobi_cli.auth import has_usable_secret, read_credential_pool

    current = str(ctx.current_provider or "").strip().lower()
    declared = {str(k).strip().lower() for k in (ctx.user_providers or {})}
    kept: list[dict] = []
    for row in rows:
        slug = str(row.get("slug") or "").strip().lower()
        if not slug or slug == current or row.get("is_user_defined") or slug in declared:
            # ``slug in declared``: the user wrote this provider into config.yaml
            # (base_url / inline api_key / models). That entry IS the credential
            # evidence — a stale ``env:`` pool row from a shell that no longer
            # exports the variable must not outvote the config file. Without
            # this, a provider configured *only* through config.yaml vanished
            # from every picker the moment the shell stopped exporting its env
            # var (measured 2026-10-20 on ``providers.aigw``).
            kept.append(row)
            continue
        try:
            entries = [
                entry
                for entry in (read_credential_pool(slug) or [])
                if isinstance(entry, dict)
            ]
        except Exception:
            kept.append(row)
            continue
        if not entries:
            kept.append(row)
            continue
        live = False
        for entry in entries:
            source = str(entry.get("source") or "").strip()
            if not source.lower().startswith("env:"):
                live = True
                break
            env_var = source.split(":", 1)[1].strip()
            if env_var and has_usable_secret(os.getenv(env_var, "")):
                live = True
                break
        if live:
            kept.append(row)
    return kept


def _raw_config_has_enabled_moa_preset() -> bool:
    """Return True when the user's raw config explicitly enables MoA.

    ``load_config()`` includes ``DEFAULT_CONFIG["moa"].presets.default`` for
    everyone. Explicit-only model pickers must not treat that default as a user
    choice, but they should keep MoA visible once the user has saved at least
    one enabled preset (or an older flat MoA config) in their own config.yaml.
    """
    try:
        from plobi_cli.config import read_raw_config

        raw = read_raw_config()
    except Exception:
        return False

    if not isinstance(raw, dict):
        return False
    moa = raw.get("moa")
    if not isinstance(moa, dict):
        return False

    presets = moa.get("presets")
    if isinstance(presets, dict):
        for name, preset in presets.items():
            if not str(name or "").strip():
                continue
            if not isinstance(preset, dict):
                return True
            if preset.get("enabled", True):
                return True
        return False

    legacy_keys = {
        "reference_models",
        "aggregator",
        "reference_temperature",
        "aggregator_temperature",
        "max_tokens",
        "reference_max_tokens",
        "fanout",
    }
    return any(key in moa for key in legacy_keys) and bool(moa.get("enabled", True))


def _apply_picker_hints(rows: list[dict]) -> None:
    """Add ``authenticated``/``auth_type``/``key_env``/``warning`` per row.

    Mutates ``rows`` in-place. Rows already from
    ``list_authenticated_providers`` are marked ``authenticated=True``;
    the unconfigured skeleton rows from ``_append_unconfigured_rows`` get
    the picker's setup-hint shape.
    """
    from plobi_cli.auth import PROVIDER_REGISTRY

    for row in rows:
        if "authenticated" in row:
            continue
        # Distinguish authenticated rows (returned by
        # list_authenticated_providers) from skeleton rows (from
        # _append_unconfigured_rows). The skeleton rows have empty
        # `models` AND source="canonical"; authenticated rows have
        # populated `models` OR a non-canonical source.
        is_skeleton = row.get("source") == "canonical" and not row.get("models")
        row["authenticated"] = not is_skeleton
        if not is_skeleton or row.get("is_user_defined"):
            continue
        cfg = PROVIDER_REGISTRY.get(row["slug"])
        auth_type = cfg.auth_type if cfg else "api_key"
        key_env = (
            cfg.api_key_env_vars[0]
            if (cfg and cfg.api_key_env_vars)
            else ""
        )
        row["auth_type"] = auth_type
        row["key_env"] = key_env
        row["warning"] = (
            f"paste {key_env} to activate"
            if auth_type == "api_key" and key_env
            else f"run `plobi model` to configure ({auth_type})"
        )


def _reorder_canonical(rows: list[dict]) -> list[dict]:
    """Canonical slugs in ``CANONICAL_PROVIDERS`` declaration order;
    truly-custom rows last.

    Keys on slug membership, NOT ``is_user_defined`` — section 3 of
    ``list_authenticated_providers`` sets ``is_user_defined=True`` on
    rows from the ``providers:`` config dict even when the slug is
    canonical. Keying on the flag would silently demote canonical
    providers configured via the new keyed schema.
    """
    from plobi_cli.models import CANONICAL_PROVIDERS

    order = {e.slug: i for i, e in enumerate(CANONICAL_PROVIDERS)}
    canon = sorted(
        (r for r in rows if r["slug"] in order),
        key=lambda r: order[r["slug"]],
    )
    extras = [r for r in rows if r["slug"] not in order]
    return canon + extras


def _apply_pricing(
    rows: list[dict],
    *,
    force_fresh_nous_tier: bool = False,
) -> None:
    """Enrich each provider row with per-model pricing + Nous tier gating.

    Mutates ``rows`` in-place. For every row whose provider supports live
    pricing (openrouter / nous / novita) adds::

        row["pricing"] = {model_id: {"input": "$3.00", "output": "$15.00",
                                     "cache": "$0.30" | None, "free": bool}}

    For Nous additionally adds::

        row["free_tier"] = bool            # current account is free-tier
        row["unavailable_models"] = [...]  # paid models a free user can't pick

    Prices are pre-formatted via ``_format_price_per_mtok`` so the GUI just
    renders strings — identical formatting to the CLI picker. All failures
    are swallowed (best-effort): a row simply gets no ``pricing`` key.
    """
    from plobi_cli.models import (
        _format_price_per_mtok,
        check_nous_free_tier,
        get_pricing_for_provider,
        partition_nous_models_by_tier,
    )

    # Resolve Nous free-tier once (cached in models.py for the TTL window).
    nous_free_tier: Optional[bool] = None

    for row in rows:
        slug = str(row.get("slug", "")).lower()
        models = row.get("models") or []
        if not models:
            continue
        try:
            raw_pricing = get_pricing_for_provider(slug) or {}
        except Exception:
            raw_pricing = {}
        if not raw_pricing:
            continue

        formatted: dict[str, dict] = {}
        for mid in models:
            p = raw_pricing.get(mid)
            if not p:
                continue
            inp_raw = p.get("prompt", "")
            out_raw = p.get("completion", "")
            cache_raw = p.get("input_cache_read", "")
            inp = _format_price_per_mtok(inp_raw) if inp_raw != "" else ""
            out = _format_price_per_mtok(out_raw) if out_raw != "" else ""
            cache = _format_price_per_mtok(cache_raw) if cache_raw else None
            # A model is "free" when both input and output cost nothing.
            is_free = inp == "free" and (out == "free" or out == "")
            formatted[mid] = {
                "input": inp,
                "output": out,
                "cache": cache,
                "free": is_free,
            }

        if formatted:
            row["pricing"] = formatted

        if slug == "nous":
            try:
                if nous_free_tier is None:
                    nous_free_tier = check_nous_free_tier(
                        force_fresh=force_fresh_nous_tier
                    )
                row["free_tier"] = bool(nous_free_tier)
                if nous_free_tier:
                    _selectable, unavailable = partition_nous_models_by_tier(
                        list(models), raw_pricing, free_tier=True
                    )
                    row["unavailable_models"] = unavailable
                else:
                    row["unavailable_models"] = []
            except Exception:
                # Tier detection failed — fail open (no gating) so the user
                # is never blocked from picking a model.
                row["free_tier"] = False
                row["unavailable_models"] = []


def _quota_hub_row() -> dict | None:
    """The quota hub's own catalog row — the carrier every hub-backed pick joins against.

    The hub is not one of the user's configured providers: it is this machine's own
    local gateway (the desktop starts it), and 裁定 46 makes its catalogue the single
    source of truth for the subscription legs.  It therefore needs no ``config.yaml``
    entry to be routable — the ``model-providers/aigw`` profile resolves its key and
    base URL on its own (裁定 81) — but it DID need a row in this payload, because that
    row is what the desktop's picker joins against: without it the subscription models
    never reach the picker, and a switch the hub itself calls "connected" changes
    nothing.

    Measured 2026-11-08 on this machine: the hub answered 58 selectable ids while the
    payload carried no hub row at all, so the desktop reported "This channel is not
    currently offered by the gateway" for a gateway that was offering 58.

    ``None`` when the hub does not answer: an unreachable hub must never be reported
    as "you have no models" — the same rule the hub's own frame follows.
    """
    try:
        from plobi.agents.registry import aigw_api_key, aigw_base_url
        from plobi_cli.models import hub_selectable_model_ids, hub_waiting_models

        ids = hub_selectable_model_ids(aigw_api_key(), aigw_base_url())
    except Exception:  # noqa: BLE001 — a missing piece means "no row", not a crash
        return None

    if not ids:
        return None

    # The models of a leg that is only *waiting* (a spent bucket that refills on its
    # own) belong in this row too — greyed out, with the time left on them (user,
    # 2026-11-08 21:3x). They are NOT in the hub's selectable list (that is what
    # "waiting" means), so without this they would never reach the picker at all.
    # The verdict rides on `capabilities[model].unavailable` (`_apply_capabilities`),
    # which is the one channel that carries it.
    try:
        from plobi.agents.registry import aigw_api_key, aigw_base_url
        from plobi_cli.models import hub_waiting_models as _waiting

        waiting_ids = list(_waiting(aigw_api_key(), aigw_base_url()).keys())
    except Exception:  # noqa: BLE001 — no waiting models is a normal state
        waiting_ids = []
    ids = list(ids) + [mid for mid in waiting_ids if mid not in set(ids)]

    try:
        from plobi_cli.models import CANONICAL_PROVIDERS, _PROVIDER_LABELS

        label = next(
            (entry.label for entry in CANONICAL_PROVIDERS if entry.slug == QUOTA_HUB_SLUG),
            "订阅额度",
        )
        name = _PROVIDER_LABELS.get(QUOTA_HUB_SLUG, label)
    except Exception:  # noqa: BLE001 — the label is cosmetic, the row is not
        name = "订阅额度"

    return {
        "slug": QUOTA_HUB_SLUG,
        "name": name,
        "is_current": False,
        "is_user_defined": False,
        "models": list(ids),
        "total_models": len(ids),
        "source": "quota-hub",
        # The hub answered with a usable catalogue, so this row carries a working
        # credential.  Without it the renderer's own filter drops the row
        # (``authenticated === false``) and the whole subscription leg disappears
        # again — the very bug this row exists to fix.
        "authenticated": True,
        "auth_type": "api_key",
    }


def _moa_provider_row(current_provider: str = "") -> dict | None:
    """Build the virtual ``moa`` provider row for model pickers.

    Shared by the CLI inventory (:func:`build_models_payload`) and the gateway
    picker path (:func:`plobi_cli.model_switch.list_picker_providers`) so the
    row shape stays in one place. Returns ``None`` when no MoA presets exist.
    """
    try:
        from plobi_cli.config import load_config
        from plobi_cli.moa_config import normalize_moa_config

        cfg = normalize_moa_config(load_config().get("moa") or {})
        models = list(cfg.get("presets", {}).keys())
        if not models:
            return None
        return {
            "slug": "moa",
            "name": "Mixture of Agents",
            "is_current": (current_provider or "").lower() == "moa",
            "is_user_defined": False,
            "models": models,
            "total_models": len(models),
            "source": "virtual",
            "authenticated": True,
            "auth_type": "virtual",
            "warning": "Aggregator acts as the selected model; references provide analysis before each call.",
        }
    except Exception:
        return None
