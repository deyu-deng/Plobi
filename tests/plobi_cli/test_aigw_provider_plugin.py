"""Ruling 45 ① — `aigw` must be a resolvable runtime provider.

Guards for ``plugins/model-providers/aigw/``:

* ``resolve_provider("aigw")`` no longer raises ``invalid_provider`` (the name
  is written into 13 agent profiles, ``plobi/routing/models.py`` L2 defaults and
  the agenda-secretary template).
* Registering it is **purely additive**: every other provider keeps resolving to
  what it resolved to before, and the loopback base URL does not hijack local
  endpoints that belong to other providers (Ollama / vLLM on 127.0.0.1).

Deliberately *not* asserted: totals or counts — those are change-detectors.
"""

import pytest

from plobi_cli.auth import PROVIDER_REGISTRY, AuthError, resolve_provider
from providers import get_provider_profile, list_providers

_OTHER_PROVIDERS = (
    "minimax", "minimax-cn", "minimax-oauth", "deepseek", "zai", "kimi-coding",
    "kimi-coding-cn", "openrouter", "custom", "anthropic", "gemini", "xai",
    "nvidia", "bedrock", "azure-foundry", "stepfun", "arcee", "gmi", "novita",
    "fireworks", "kilocode", "opencode-zen", "openai-codex", "alibaba",
    "ollama-cloud", "huggingface", "qwen-oauth", "vertex", "xiaomi",
    "copilot", "copilot-acp", "nous", "lmstudio",
)

_ALIAS_TARGETS = (
    ("glm", "zai"), ("zhipu", "zai"), ("moonshot", "kimi-coding"),
    ("kimi", "kimi-coding"), ("google", "gemini"), ("x-ai", "xai"),
    ("claude", "anthropic"), ("github", "copilot"), ("ollama", "custom"),
    ("vllm", "custom"), ("llamacpp", "custom"), ("minimax_cn", "minimax-cn"),
    ("aws", "bedrock"), ("hf", "huggingface"), ("kilo", "kilocode"),
)


def test_aigw_resolves_as_a_provider():
    assert resolve_provider("aigw") == "aigw"


def test_aigw_profile_is_registered():
    profile = get_provider_profile("aigw")
    assert profile is not None
    assert profile.name == "aigw"
    assert profile in list_providers()


def test_aigw_is_in_the_provider_registry():
    cfg = PROVIDER_REGISTRY.get("aigw")
    assert cfg is not None
    assert cfg.auth_type == "api_key"


def test_aigw_base_url_points_at_the_local_gateway():
    """Base URL comes from the agents layer, not a second copy of env parsing."""
    from plobi.agents.registry import aigw_base_url

    assert get_provider_profile("aigw").base_url == aigw_base_url()


def test_aigw_key_env_vars_are_existing_ones():
    """No new PLOBI_* var was invented for this provider."""
    cfg = PROVIDER_REGISTRY["aigw"]
    assert cfg.api_key_env_vars == ("AIGW_API_KEY", "PLOBI_QUOTA_AIGW_KEY")
    assert cfg.base_url_env_var == "PLOBI_AIGW_URL"


def test_aigw_declares_no_aliases():
    """Aliases leak into the global alias table — the name already is `aigw`."""
    assert get_provider_profile("aigw").aliases == ()


# ── Purely-additive invariants ────────────────────────────────────────────


@pytest.mark.parametrize("provider", _OTHER_PROVIDERS)
def test_other_providers_still_resolve(provider):
    """Every other registered provider resolves to itself, aigw or not."""
    if provider not in PROVIDER_REGISTRY:
        pytest.skip(f"{provider} has no PROVIDER_REGISTRY entry in this environment")
    assert resolve_provider(provider) == provider


@pytest.mark.parametrize("requested,expected", _ALIAS_TARGETS)
def test_provider_aliases_unchanged(requested, expected):
    assert resolve_provider(requested) == expected


def test_auto_path_is_untouched():
    """`auto` must not short-circuit into the new profile."""
    try:
        resolved = resolve_provider("auto")
    except AuthError as exc:
        assert exc.code != "invalid_provider" or "aigw" not in str(exc)
        return
    assert resolved != "aigw"


def test_unknown_provider_still_rejected():
    with pytest.raises(AuthError) as exc:
        resolve_provider("definitely-not-a-provider")
    assert exc.value.code == "invalid_provider"


def test_loopback_base_url_does_not_hijack_other_local_endpoints():
    """`127.0.0.1` must not become a provider key — Ollama/vLLM live there too."""
    from agent.model_metadata import _URL_TO_PROVIDER, _infer_provider_from_url

    assert _infer_provider_from_url("http://127.0.0.1:11434/v1") != "aigw"
    assert _infer_provider_from_url("http://127.0.0.1:8000/v1") == "aigw"
    # a bare-host key would substring-match every loopback endpoint
    assert "127.0.0.1" not in _URL_TO_PROVIDER
