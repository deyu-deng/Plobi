"""网关凭据只有一个解析顺序 —— 而且它现在挂在**软件名**上，不是 `bezel`。

裁定 81: ``plobi_cli.auth`` owns "what did the user give us explicitly", the
provider profile owns "where to fall back when nothing was given", and the chat
path and the model-probing path enter through the same resolver.  Nothing may
``or`` its way to a private answer, and an unresolvable credential must be
visible rather than skipped.

Assertions are relational on purpose: they compare *sources* and *who answered*,
never a key literal — the real values stay in ``.env`` and ``bezel/config.yaml``.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load_by_path(relpath: str, module_name: str):
    """Load a module a normal import can't reach (hyphenated dir / scripts/)."""
    spec = importlib.util.spec_from_file_location(module_name, REPO / relpath)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# Imported, not path-loaded: the whole point is that every consumer shares *this*
# module object, so a second copy under another name could not prove it.
from plobi.agents import registry

# 11-18 第三处：`bezel` 不再是 provider（插件已删），22 家软件各自是 provider，
# 共用网关那一份凭据。这些断言原本挂在插件上，现在挂在 `providers.hub_software`。
from providers import hub_software

doctor = _load_by_path("scripts/plobi/doctor.py", "plobi_bezel_cred_doctor")


@pytest.fixture(autouse=True)
def clean_gateway_env(monkeypatch):
    """No inherited BEZEL_* values, and ``.env`` only mirrors the process env."""
    for name in (*registry.BEZEL_KEY_ENV_VARS, "BEZEL_KEY", *registry.BEZEL_URL_ENV_VARS):
        monkeypatch.delenv(name, raising=False)
    # The resolver prefers ~/.plobi/.env over the process env; mirror env only so
    # a developer's real home file cannot decide the outcome of these tests.
    monkeypatch.setattr(
        "plobi_cli.config.get_env_value_prefer_dotenv",
        lambda name: os.environ.get(name, ""),
    )


@pytest.fixture
def gateway_config(tmp_path, monkeypatch):
    """Point every consumer at a throwaway gateway config and return its key."""
    declared = "sk-test-gateway-declared"
    path = tmp_path / "config.yaml"
    path.write_text(
        "server:\n  host: 0.0.0.0\n  port: 8000\n"
        f"  api_key: {declared}\nroutes: []\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(registry, "BEZEL_GATEWAY_CONFIG_PATH", path)
    return declared


# ─── 必做 1.5: one definition point for the names and the default ──────────


def test_profile_and_agents_layer_share_one_env_var_list():
    """软件 profile 必须 import agents 层那两个元组，不许自己再抄一遍。"""
    assert hub_software._KEY_VARS is registry.BEZEL_KEY_ENV_VARS
    assert hub_software._URL_VARS is registry.BEZEL_URL_ENV_VARS


def test_auth_table_picks_up_the_declared_names():
    """The bridge must hand the resolver exactly the agents layer's names."""
    from plobi_cli.auth import PROVIDER_REGISTRY

    assert "bezel" not in PROVIDER_REGISTRY, "机器标识不该再是一张 provider 卡"
    cfg = PROVIDER_REGISTRY["workbuddy"]
    assert cfg.api_key_env_vars == registry.BEZEL_KEY_ENV_VARS
    assert cfg.base_url_env_var in registry.BEZEL_URL_ENV_VARS


@pytest.mark.parametrize(
    "relpath",
    ["scripts/plobi/doctor.py", "providers/hub_software.py"],
)
def test_no_second_copy_of_the_credential_lives_elsewhere(relpath):
    """Guard against re-adding a private default or an unlisted env var name.

    ``BEZEL_KEY`` is the gateway server's own variable — reading it on the Plobi
    side was one of the two lists this cut collapsed.
    """
    source = (REPO / relpath).read_text(encoding="utf-8")
    assert '"BEZEL_KEY"' not in source, f"{relpath} reads a var the list doesn't declare"
    assert registry.BEZEL_DEV_DEFAULT_API_KEY not in source, (
        f"{relpath} carries its own copy of the gateway default key"
    )


def test_fallback_value_follows_the_gateway_config(gateway_config):
    """Rotate ``server.api_key`` ⇒ the agents layer sends the new value."""
    assert registry.gateway_declared_api_key() == gateway_config
    assert registry.bezel_api_key() == gateway_config
    assert registry.bezel_credential_source() == "gateway config"


def test_placeholder_form_is_expanded_not_sent_verbatim(tmp_path, monkeypatch):
    """``${VAR:-default}`` is what the gateway itself expands; we must agree."""
    path = tmp_path / "placeholder.yaml"
    path.write_text("server:\n  api_key: ${BEZEL_KEY:-sk-inner-default}\n", encoding="utf-8")
    monkeypatch.setattr(registry, "BEZEL_GATEWAY_CONFIG_PATH", path)
    assert registry.gateway_declared_api_key() == "sk-inner-default"

    monkeypatch.setenv("BEZEL_KEY", "sk-from-the-gateways-own-var")
    assert registry.gateway_declared_api_key() == "sk-from-the-gateways-own-var"


def test_missing_gateway_config_falls_back_to_the_shipped_default(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "BEZEL_GATEWAY_CONFIG_PATH", tmp_path / "absent.yaml")
    assert registry.gateway_declared_api_key() == ""
    assert registry.bezel_api_key() == registry.BEZEL_DEV_DEFAULT_API_KEY
    assert registry.bezel_credential_source() == "development default"


def test_doctor_and_agents_layer_cannot_drift_apart(gateway_config, monkeypatch):
    """The invariant 必做 1.5 asked for: one answer, whichever side asks."""
    assert doctor.bezel_api_key() == registry.bezel_api_key() == gateway_config

    # An explicit value outranks the fallback for both, and doctor says so —
    # naming the sources without ever naming either value.
    explicit = registry.BEZEL_KEY_ENV_VARS[0]
    monkeypatch.setenv(explicit, "sk-test-explicit-elsewhere")
    assert doctor.bezel_api_key() == "sk-test-explicit-elsewhere"
    conflict = doctor.bezel_key_conflict()
    assert conflict and explicit in conflict
    assert gateway_config not in conflict
    assert "sk-test-explicit-elsewhere" not in conflict

    monkeypatch.delenv(explicit)
    assert doctor.bezel_key_conflict() == ""


# ─── 必做 2: one order, one entry point, chat and probe alike ──────────────


def test_explicit_env_value_outranks_the_declared_fallback(monkeypatch):
    from plobi_cli.auth import resolve_api_key_provider_credentials

    marker = "sk-test-explicit-wins"
    monkeypatch.setenv(registry.BEZEL_KEY_ENV_VARS[0], marker)
    creds = resolve_api_key_provider_credentials("workbuddy")
    assert creds["api_key"] == marker
    # Source names the explicit var, not the fallback: the order is a real order.
    assert creds["source"] == registry.BEZEL_KEY_ENV_VARS[0]


def test_fallback_is_observable_when_nothing_explicit_is_set(monkeypatch):
    """Nothing in the table ⇒ the profile's declared fallback answers, and we can
    see it happened. 断言「发生了什么」，不是断言等于哪个字符串。"""
    from plobi_cli.auth import resolve_api_key_provider_credentials

    monkeypatch.setattr("agent.credential_pool.load_pool", lambda provider: None)
    creds = resolve_api_key_provider_credentials("workbuddy")
    assert creds["source"].startswith("profile_fallback:")
    assert creds["api_key"] == registry.bezel_api_key()
    assert creds["api_key"], "the gateway credential resolved to nothing"


def test_chat_path_and_probe_path_resolve_the_same_answer():
    """``--provider workbuddy`` and the model picker may not disagree (裁定 81)."""
    from plobi_cli.auth import resolve_api_key_provider_credentials
    from plobi_cli.runtime_provider import resolve_runtime_provider

    probed = resolve_api_key_provider_credentials("workbuddy")
    chat = resolve_runtime_provider(requested="workbuddy", target_model="workbuddy/x")
    assert chat["api_key"] == probed["api_key"]
    assert chat["base_url"] == probed["base_url"]
    assert chat["source"] == probed["source"]


def test_profile_no_longer_fills_the_key_at_the_call_site():
    """第二个 ``or`` 已经没了：``fetch_models`` 走基类实现，钥匙只从兜底来。"""
    assert "fetch_models" not in vars(hub_software.HubSoftwareProfile)
    assert callable(hub_software.HubSoftwareProfile.fallback_api_key)


def test_every_software_answers_with_the_gateway_credential():
    """22 家软件各自是 provider，凭据却只有网关那一份 —— 每一家都得答得出。"""
    from plobi_cli.auth import resolve_api_key_provider_credentials

    for sid in hub_software.HUB_SOFTWARE:
        creds = resolve_api_key_provider_credentials(sid)
        assert creds["api_key"], f"{sid} 没解析出凭据"
        assert creds["source"].startswith("profile_fallback:")


def test_the_transport_name_is_not_a_provider_but_keeps_the_credential():
    """`bezel` 只剩传输这一个身份：不进注册表、不上屏，但内部路径仍拿得到凭据。

    这几条腿共用一份钥匙，传输名的 profile 兜底就是它们共用的那个答案。
    """
    import providers

    assert hub_software.TRANSPORT_NAME == "bezel"
    assert "bezel" not in {p.name for p in providers.list_providers()}
    assert providers.get_provider_profile("bezel").fallback_api_key() == registry.bezel_api_key()


# ─── 必做 3: no silent skip, and doctor doesn't stay green on a mismatch ───


class _StubProfile:
    """Minimal api-key profile so the probe path can be watched without network."""

    auth_type = "api_key"
    base_url = "http://127.0.0.1:59999/v1"
    fallback_models = ()
    env_vars = ("STUB_KEY",)

    def __init__(self, models):
        self._models = models
        self.calls = []

    def fetch_models(self, *, api_key=None, base_url=None, timeout=8.0):
        self.calls.append({"api_key": api_key, "base_url": base_url})
        return list(self._models)


def _probe_stub(monkeypatch, creds_side_effect, models=("stub/model-a",)):
    import providers
    from plobi_cli import models as pmodels

    stub = _StubProfile(list(models))
    monkeypatch.setattr(providers, "get_provider_profile", lambda name: stub)
    monkeypatch.setattr(
        "plobi_cli.auth.resolve_api_key_provider_credentials", creds_side_effect
    )
    return stub, pmodels


def test_probe_reports_a_missing_credential_instead_of_skipping_it(monkeypatch, caplog):
    from plobi_cli.auth import AuthError

    def boom(provider_id):
        raise AuthError("no credential", provider=provider_id, code="missing_api_key")

    stub, pmodels = _probe_stub(monkeypatch, boom)
    with caplog.at_level(logging.INFO, logger="plobi_cli.models"):
        pmodels.provider_model_ids("stubprovider")

    assert stub.calls == [], "a probe without a credential must not hit the endpoint"
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "stubprovider" in text and "STUB_KEY" in text, (
        "the skip must name the provider and the credential it looked for"
    )


def test_probe_uses_the_declared_fallback_and_says_so(monkeypatch, caplog):
    def ok(provider_id):
        return {
            "provider": provider_id,
            "api_key": "sk-test-resolved",
            "base_url": "http://127.0.0.1:59999/v1",
            "source": "profile_fallback:bezel",
        }

    stub, pmodels = _probe_stub(monkeypatch, ok, models=["stub/live"])
    with caplog.at_level(logging.INFO, logger="plobi_cli.models"):
        ids = pmodels.provider_model_ids("stubprovider")

    assert stub.calls == [{"api_key": "sk-test-resolved", "base_url": "http://127.0.0.1:59999/v1"}]
    assert "stub/live" in ids
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "declared fallback" in text


def test_doctor_downgrades_a_green_row_when_the_keys_disagree(
    monkeypatch, gateway_config
):
    """A gateway that answers is not enough: a key it shouldn't accept is red/yellow."""
    monkeypatch.setattr(
        doctor,
        "http_get_json",
        lambda url, timeout=2.5, headers=None: (
            200,
            {"data": [{"id": "workbuddy/glm-5.3", "provider": "workbuddy"}]},
        ),
    )
    assert doctor.check_bezel("http://127.0.0.1:8000/v1")["color"] == doctor.GREEN

    monkeypatch.setenv(
        registry.BEZEL_KEY_ENV_VARS[0], "sk-test-not-what-gateway-expects"
    )
    row = doctor.check_bezel("http://127.0.0.1:8000/v1")
    assert row["color"] != doctor.GREEN
    assert "server.api_key" in row["detail"]
    assert gateway_config not in row["detail"]


def test_doctor_401_says_the_gateway_rejected_our_credential(monkeypatch):
    monkeypatch.setattr(
        doctor, "http_get_json", lambda url, timeout=2.5, headers=None: (401, None)
    )
    row = doctor.check_bezel("http://127.0.0.1:8000/v1")
    assert row["color"] == doctor.RED
    assert "rejected our credential" in row["detail"]
    # The row says *where* the credential came from, never what it is.
    assert row["credential"].startswith(("env:", "gateway config", "development default"))


# ─── 11-19: one list of env names, written on both sides ──────────────────────
def test_electron_reads_the_same_env_names_in_the_same_order():
    """``registry.BEZEL_KEY_ENV_VARS`` 与 electron 的 ``ENV_ORDER`` 同名同序。

    两张表是同一份约定写了两遍，历史上各演化过一次：Python 认
    ``PLOBI_QUOTA_BEZEL_KEY``，electron 认 ``PLOBI_BEZEL_API_KEY``。只设一个
    名字的用户，另一端静默回退到 ``bezel/config.yaml`` 或 dev default ——
    症状是「key 明明配了，网关还 401」。11-19 合并到 ``PLOBI_BEZEL_API_KEY``
    且不留别名；这条断言钉的是**名字本身**，不是解析逻辑（解析逻辑早有
    parity 覆盖，名字没有，才让分叉活了下来）。
    """
    source = (REPO / "apps/desktop/electron/bezel-key-reconcile.ts").read_text(
        encoding="utf-8"
    )
    match = re.search(r"ENV_ORDER\s*=\s*\[([^\]]*)\]", source)
    assert match, "bezel-key-reconcile.ts 里找不到 ENV_ORDER"
    order = tuple(re.findall(r"""['"]([A-Z0-9_]+)['"]""", match.group(1)))
    assert order == registry.BEZEL_KEY_ENV_VARS


def test_main_process_ladder_reads_no_key_name_outside_that_list():
    """主进程那条 ``a || b || dev-default`` 阶梯不许再冒出第三个名字。

    阶梯是手写的 ``process.env.X || process.env.Y``，最容易成为下一次分叉的
    出生地：加一个名字不报错、也不影响任何 parity 断言。这里只取以
    ``BEZEL_API_KEY`` 结尾的 env 名（``PLOBI_BEZEL_DIR`` / ``_PORT`` 不是凭据，
    归别的配置管）。
    """
    source = (REPO / "apps/desktop/electron/main.ts").read_text(encoding="utf-8")
    names = set(re.findall(r"process\.env\.([A-Z0-9_]*BEZEL_API_KEY)", source))
    assert names == set(registry.BEZEL_KEY_ENV_VARS)
