"""The picker's model list for a provider is that provider's own `/models` when it has one.

User 2026-11-08: 「获取的模型列表也和官网的不一样，过时了」. The rows used to come from the
models.dev snapshot only; for the providers that publish an OpenAI-compatible catalog, the
live answer wins (key-gated, ten-minute cache, and every failure keeps the snapshot).
"""
from __future__ import annotations

import plobi_cli.model_switch as ms


def _reset_cache():
    ms._LIVE_MODEL_CACHE.clear()


def test_a_configured_provider_reports_its_live_catalog_and_caches_it(monkeypatch):
    _reset_cache()
    calls: list[tuple] = []
    monkeypatch.setattr(
        "plobi_cli.auth.resolve_api_key_provider_credentials",
        lambda slug: {"provider": slug, "api_key": "k", "base_url": "https://x/v1", "source": "env"},
        raising=False,
    )
    monkeypatch.setattr(
        "plobi_cli.models.fetch_api_models",
        lambda key, base, timeout=5.0, **kw: calls.append((key, base)) or ["live-a", "live-b"],
        raising=False,
    )

    assert ms._live_models_for("deepinfra") == ["live-a", "live-b"]
    assert ms._live_models_for("deepinfra") == ["live-a", "live-b"]
    assert len(calls) == 1, "the picker opens often; a live read must not repeat per open"


def test_without_a_key_nothing_is_dialed(monkeypatch):
    _reset_cache()
    calls: list[int] = []
    monkeypatch.setattr(
        "plobi_cli.auth.resolve_api_key_provider_credentials",
        lambda slug: {"provider": slug, "api_key": "", "base_url": "https://x/v1"},
        raising=False,
    )
    monkeypatch.setattr(
        "plobi_cli.models.fetch_api_models",
        lambda *a, **kw: calls.append(1) or ["nope"],
        raising=False,
    )

    assert ms._live_models_for("deepinfra") == []
    assert calls == [], "a provider the user never configured must cost nothing"


def test_an_unreadable_catalog_keeps_the_snapshot(monkeypatch):
    _reset_cache()

    def boom(slug):
        raise RuntimeError("auth store locked")

    monkeypatch.setattr("plobi_cli.auth.resolve_api_key_provider_credentials", boom, raising=False)

    assert ms._live_models_for("deepinfra") == []

    monkeypatch.setattr(
        "plobi_cli.auth.resolve_api_key_provider_credentials",
        lambda slug: {"provider": slug, "api_key": "k", "base_url": "https://x/v1"},
        raising=False,
    )
    monkeypatch.setattr("plobi_cli.models.fetch_api_models", lambda *a, **kw: None, raising=False)

    assert ms._live_models_for("deepinfra") == [], "an endpoint that could not answer is not a list"


def test_only_the_snapshot_stale_providers_are_read_live():
    # The set is deliberate: opening the picker must not fan out to every provider.
    assert "deepinfra" in ms._LIVE_MODEL_PROVIDERS
    assert "alibaba-cn" in ms._LIVE_MODEL_PROVIDERS
    assert "openai-api" not in ms._LIVE_MODEL_PROVIDERS
