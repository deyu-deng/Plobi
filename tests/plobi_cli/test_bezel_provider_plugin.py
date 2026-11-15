"""Ruling 45 ① — `bezel` must be a resolvable runtime provider.

Guards for ``plugins/model-providers/bezel/``:

* ``resolve_provider("bezel")`` no longer raises ``invalid_provider`` (the name
  is written into the agent profiles and ``plobi/routing/models.py`` L2 default).
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


def test_bezel_resolves_as_a_provider():
    assert resolve_provider("bezel") == "bezel"


def test_bezel_profile_is_registered():
    profile = get_provider_profile("bezel")
    assert profile is not None
    assert profile.name == "bezel"
    assert profile in list_providers()


def test_bezel_is_in_the_provider_registry():
    cfg = PROVIDER_REGISTRY.get("bezel")
    assert cfg is not None
    assert cfg.auth_type == "api_key"


def test_bezel_base_url_points_at_the_local_gateway():
    """Base URL comes from the agents layer, not a second copy of env parsing."""
    from plobi.agents.registry import bezel_base_url

    assert get_provider_profile("bezel").base_url == bezel_base_url()


def test_bezel_key_env_vars_are_existing_ones():
    """No new PLOBI_* var was invented for this provider.

    清单与顺序的**唯一**定义点在 :data:`plobi.agents.registry.BEZEL_KEY_ENV_VARS`
    （WP-BEZEL-CRED-SOURCE / 裁定 81）——这里断言的是"插件用的就是那一份"，
    不是再抄一遍顺序：抄一遍就会有第二个真相，网关那边加一个 ``BEZEL_API_KEY``
    之后这条就变成一条只证明自己过时的红灯。
    """
    from plobi.agents.registry import BEZEL_KEY_ENV_VARS, BEZEL_URL_ENV_VARS

    cfg = PROVIDER_REGISTRY["bezel"]
    assert cfg.api_key_env_vars == BEZEL_KEY_ENV_VARS
    # 唯一的新变量只能是本插件自己的那把（``PLOBI_`` 前缀），其余必须是上游既有的。
    assert {v for v in cfg.api_key_env_vars if v.startswith("PLOBI_")} == {
        "PLOBI_QUOTA_BEZEL_KEY"
    }
    # 端点变量同理：``PROVIDER_REGISTRY`` 是从 ``env_vars`` 里挑出第一个 ``*_URL``
    # 落成 ``base_url_env_var``（见 plobi_cli/auth.py 的自动注册段），所以这里只能
    # 断言"它就是 registry 那份清单的首位"。写死 ``PLOBI_BEZEL_URL`` 的那版后来变成
    # 了一条只证明自己过时的红灯——桌面端把 ``PLOBI_BEZEL_URL`` 提到首位之后，
    # 解析出来的就是它，而两处行为都对。
    assert cfg.base_url_env_var == BEZEL_URL_ENV_VARS[0]
    assert cfg.base_url_env_var in BEZEL_URL_ENV_VARS


def test_bezel_declares_no_aliases():
    """Aliases leak into the global alias table — the name already is `bezel`."""
    assert get_provider_profile("bezel").aliases == ()


# ── Purely-additive invariants ────────────────────────────────────────────


@pytest.mark.parametrize("provider", _OTHER_PROVIDERS)
def test_other_providers_still_resolve(provider):
    """Every other registered provider resolves to itself, bezel or not."""
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
        assert exc.code != "invalid_provider" or "bezel" not in str(exc)
        return
    assert resolved != "bezel"


def test_unknown_provider_still_rejected():
    with pytest.raises(AuthError) as exc:
        resolve_provider("definitely-not-a-provider")
    assert exc.value.code == "invalid_provider"


def test_loopback_base_url_does_not_hijack_other_local_endpoints():
    """`127.0.0.1` must not become a provider key — Ollama/vLLM live there too."""
    from agent.model_metadata import _URL_TO_PROVIDER, _infer_provider_from_url

    assert _infer_provider_from_url("http://127.0.0.1:11434/v1") != "bezel"
    assert _infer_provider_from_url("http://127.0.0.1:8000/v1") == "bezel"
    # a bare-host key would substring-match every loopback endpoint
    assert "127.0.0.1" not in _URL_TO_PROVIDER
