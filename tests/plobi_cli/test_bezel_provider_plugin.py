"""`bezel` 是**传输**，不是 provider —— 名册里那几家软件各自才是 provider。

（本档原名守裁定 45 ①「`bezel` 必须是一个能解析的 runtime provider」，11-18
第三处已把这个裁定反过来：主人 11-17 18:5x「bezel 是内部名称，provider 下面
适配的是一个一个软件名，而不是 bezel。」下面保留的断言分两段读：

* 上半段（本文件的旧名）：注册是**纯加法** —— 别家 provider 一个都不许被改；
  回环地址不许抢别人家（Ollama / vLLM 在 127.0.0.1）。
* 下半段（``11-18 第三处`` 起）：`bezel` 只剩传输这一身份，不上屏、不进 picker、
  不在注册表；软件名才是可选的 provider。

刻意不断言的：总数、条数 —— 那些是变更探测器，不是契约。
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
    ("kimi", "kimi-coding"), ("x-ai", "xai"),
    ("claude", "anthropic"), ("github", "copilot"), ("ollama", "custom"),
    ("vllm", "custom"), ("llamacpp", "custom"), ("minimax_cn", "minimax-cn"),
    ("aws", "bedrock"), ("hf", "huggingface"), ("kilo", "kilocode"),
)


def test_bezel_base_url_points_at_the_local_gateway():
    """Base URL comes from the agents layer, not a second copy of env parsing."""
    from plobi.agents.registry import bezel_base_url

    assert get_provider_profile("bezel").base_url == bezel_base_url()


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


def test_软件名抢在别名表前面():
    """名册里出现了 `google` 这家软件 ⇒ 它先被当成**软件名**，不再是 `gemini` 的别名。

    同 `grok` 的裁定 47：网关名册说了算的那一格，别名表让路。想直连 Gemini 就写
    `gemini`；写 `google` 意思是「Google 那条腿（走网关）」。
    """
    assert resolve_provider("google") == "google"
    assert resolve_provider("gemini") == "gemini"


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



# ---------------------------------------------------------------------------
# 11-18 第三处：`bezel` **不是 provider**，它是名册里那几家软件共用的传输。
#
# 主人 11-17 18:5x：「bezel 是内部名称。provider 下面适配的是一个一个软件名，
# 而不是 bezel。」所以下面这几条把原来的「bezel 是 provider」全部反过来钉死：
# 它不在注册表、不在 picker、不上屏；软件名才是 provider。
# ---------------------------------------------------------------------------


def test_bezel_is_no_longer_a_provider():
    assert "bezel" not in PROVIDER_REGISTRY


def test_bezel_is_no_longer_in_the_picker():
    from plobi_cli.models import CANONICAL_PROVIDERS

    assert all(p.name != "bezel" for p in list_providers())
    canonical = {e.slug for e in CANONICAL_PROVIDERS}
    assert "bezel" not in canonical


def test_bezel_is_only_the_group_owner():
    from plobi.agents.registry import bezel_base_url
    from providers.hub_software import GROUP_ID, GROUP_OWNER

    assert GROUP_ID == "bezel"
    # 组 owner 不是一行可选 provider：不进 picker、不在注册表；但内部路径还能按这个
    # 名字拿到**组级**凭据兜底与请求字段（名册里那几家软件共用这一份）。
    assert GROUP_OWNER not in list_providers()
    profile = get_provider_profile("bezel")
    assert profile is GROUP_OWNER
    assert profile.base_url == bezel_base_url()
    assert profile.fallback_api_key()


def test_each_software_is_its_own_provider():
    for software in ("workbuddy", "opencode-zen-free", "qoder-cn", "agy-cli"):
        profile = get_provider_profile(software)
        assert profile is not None, software
        assert profile.name == software
        assert software in PROVIDER_REGISTRY


def test_software_profiles_share_the_gateway_endpoint_and_credentials():
    from plobi.agents.registry import BEZEL_KEY_ENV_VARS, BEZEL_URL_ENV_VARS, bezel_base_url

    profile = get_provider_profile("workbuddy")
    assert profile.base_url == bezel_base_url()
    assert list(profile.env_vars) == list(BEZEL_KEY_ENV_VARS) + list(BEZEL_URL_ENV_VARS)
    assert profile.fallback_api_key() == get_provider_profile("bezel").fallback_api_key()


def test_shared_loopback_endpoint_names_no_single_software():
    """所有成员共用 `127.0.0.1:8000`：反查猜不出是哪一家，就不许猜。"""
    from agent.model_metadata import _URL_TO_PROVIDER, _infer_provider_from_url

    assert _infer_provider_from_url("http://127.0.0.1:8000/v1") is None
    assert "127.0.0.1:8000" not in _URL_TO_PROVIDER
    assert "127.0.0.1" not in _URL_TO_PROVIDER


def test_local_ollama_is_still_not_hijacked():
    from agent.model_metadata import _infer_provider_from_url

    assert _infer_provider_from_url("http://127.0.0.1:11434/v1") != "bezel"
