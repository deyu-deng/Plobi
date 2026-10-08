"""WP-AIGW-CRED-SOURCE — the gateway credential has ONE resolution order.

裁定 81: ``plobi_cli.auth`` owns "what did the user give us explicitly", the
provider profile owns "where to fall back when nothing was given", and the chat
path and the model-probing path enter through the same resolver.  Nothing may
``or`` its way to a private answer, and an unresolvable credential must be
visible rather than skipped.

Assertions are relational on purpose: they compare *sources* and *who answered*,
never a key literal — the real values stay in ``.env`` and ``aigw/config.yaml``.
"""

from __future__ import annotations

import importlib.util
import logging
import os
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

plugin = _load_by_path(
    "plugins/model-providers/aigw/__init__.py", "plobi_aigw_cred_plugin"
)
doctor = _load_by_path("scripts/plobi/doctor.py", "plobi_aigw_cred_doctor")


@pytest.fixture(autouse=True)
def clean_gateway_env(monkeypatch):
    """No inherited AIGW_* values, and ``.env`` only mirrors the process env."""
    for name in (*registry.AIGW_KEY_ENV_VARS, "AIGW_KEY", *registry.AIGW_URL_ENV_VARS):
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
    monkeypatch.setattr(registry, "AIGW_GATEWAY_CONFIG_PATH", path)
    return declared


# ─── 必做 1.5: one definition point for the names and the default ──────────


def test_profile_and_agents_layer_share_one_env_var_list():
    """The plugin must import the agents layer's tuples, not restate them."""
    assert plugin._KEY_VARS is registry.AIGW_KEY_ENV_VARS
    assert plugin._URL_VARS is registry.AIGW_URL_ENV_VARS


def test_auth_table_picks_up_the_declared_names():
    """The bridge must hand the resolver exactly the agents layer's names."""
    from plobi_cli.auth import PROVIDER_REGISTRY

    cfg = PROVIDER_REGISTRY["aigw"]
    assert cfg.api_key_env_vars == registry.AIGW_KEY_ENV_VARS
    assert cfg.base_url_env_var in registry.AIGW_URL_ENV_VARS


@pytest.mark.parametrize(
    "relpath",
    ["scripts/plobi/doctor.py", "plugins/model-providers/aigw/__init__.py"],
)
def test_no_second_copy_of_the_credential_lives_elsewhere(relpath):
    """Guard against re-adding a private default or an unlisted env var name.

    ``AIGW_KEY`` is the gateway server's own variable — reading it on the Plobi
    side was one of the two lists this cut collapsed.
    """
    source = (REPO / relpath).read_text(encoding="utf-8")
    assert '"AIGW_KEY"' not in source, f"{relpath} reads a var the list doesn't declare"
    assert registry.AIGW_DEV_DEFAULT_API_KEY not in source, (
        f"{relpath} carries its own copy of the gateway default key"
    )


def test_fallback_value_follows_the_gateway_config(gateway_config):
    """Rotate ``server.api_key`` ⇒ the agents layer sends the new value."""
    assert registry.gateway_declared_api_key() == gateway_config
    assert registry.aigw_api_key() == gateway_config
    assert registry.aigw_credential_source() == "gateway config"


def test_placeholder_form_is_expanded_not_sent_verbatim(tmp_path, monkeypatch):
    """``${VAR:-default}`` is what the gateway itself expands; we must agree."""
    path = tmp_path / "placeholder.yaml"
    path.write_text("server:\n  api_key: ${AIGW_KEY:-sk-inner-default}\n", encoding="utf-8")
    monkeypatch.setattr(registry, "AIGW_GATEWAY_CONFIG_PATH", path)
    assert registry.gateway_declared_api_key() == "sk-inner-default"

    monkeypatch.setenv("AIGW_KEY", "sk-from-the-gateways-own-var")
    assert registry.gateway_declared_api_key() == "sk-from-the-gateways-own-var"


def test_missing_gateway_config_falls_back_to_the_shipped_default(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "AIGW_GATEWAY_CONFIG_PATH", tmp_path / "absent.yaml")
    assert registry.gateway_declared_api_key() == ""
    assert registry.aigw_api_key() == registry.AIGW_DEV_DEFAULT_API_KEY
    assert registry.aigw_credential_source() == "development default"


def test_doctor_and_agents_layer_cannot_drift_apart(gateway_config, monkeypatch):
    """The invariant 必做 1.5 asked for: one answer, whichever side asks."""
    assert doctor.aigw_api_key() == registry.aigw_api_key() == gateway_config

    # An explicit value outranks the fallback for both, and doctor says so —
    # naming the sources without ever naming either value.
    explicit = registry.AIGW_KEY_ENV_VARS[0]
    monkeypatch.setenv(explicit, "sk-test-explicit-elsewhere")
    assert doctor.aigw_api_key() == "sk-test-explicit-elsewhere"
    conflict = doctor.aigw_key_conflict()
    assert conflict and explicit in conflict
    assert gateway_config not in conflict
    assert "sk-test-explicit-elsewhere" not in conflict

    monkeypatch.delenv(explicit)
    assert doctor.aigw_key_conflict() == ""


# ─── 必做 2: one order, one entry point, chat and probe alike ──────────────


def test_explicit_env_value_outranks_the_declared_fallback(monkeypatch):
    from plobi_cli.auth import resolve_api_key_provider_credentials

    marker = "sk-test-explicit-wins"
    monkeypatch.setenv(registry.AIGW_KEY_ENV_VARS[0], marker)
    creds = resolve_api_key_provider_credentials("aigw")
    assert creds["api_key"] == marker
    # Source names the explicit var, not the fallback: the order is a real order.
    assert creds["source"] == registry.AIGW_KEY_ENV_VARS[0]


def test_fallback_is_observable_when_nothing_explicit_is_set(monkeypatch):
    """Nothing in the table ⇒ the profile's declared fallback answers, and we can
    see it happened. 断言「发生了什么」，不是断言等于哪个字符串。"""
    from plobi_cli.auth import resolve_api_key_provider_credentials

    monkeypatch.setattr("agent.credential_pool.load_pool", lambda provider: None)
    creds = resolve_api_key_provider_credentials("aigw")
    assert creds["source"].startswith("profile_fallback:")
    assert creds["api_key"] == registry.aigw_api_key()
    assert creds["api_key"], "the gateway credential resolved to nothing"


def test_chat_path_and_probe_path_resolve_the_same_answer():
    """``--provider aigw`` and the model picker may not disagree (裁定 81)."""
    from plobi_cli.auth import resolve_api_key_provider_credentials
    from plobi_cli.runtime_provider import resolve_runtime_provider

    probed = resolve_api_key_provider_credentials("aigw")
    chat = resolve_runtime_provider(requested="aigw", target_model="workbuddy/x")
    assert chat["api_key"] == probed["api_key"]
    assert chat["base_url"] == probed["base_url"]
    assert chat["source"] == probed["source"]


def test_profile_no_longer_fills_the_key_at_the_call_site():
    """The second ``or`` is gone: ``fetch_models`` is the base implementation."""
    assert "fetch_models" not in vars(plugin.AigwProfile)
    assert callable(plugin.AigwProfile.fallback_api_key)


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
            "source": "profile_fallback:aigw",
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
    assert doctor.check_aigw("http://127.0.0.1:8000/v1")["color"] == doctor.GREEN

    monkeypatch.setenv(
        registry.AIGW_KEY_ENV_VARS[0], "sk-test-not-what-gateway-expects"
    )
    row = doctor.check_aigw("http://127.0.0.1:8000/v1")
    assert row["color"] != doctor.GREEN
    assert "server.api_key" in row["detail"]
    assert gateway_config not in row["detail"]


def test_doctor_401_says_the_gateway_rejected_our_credential(monkeypatch):
    monkeypatch.setattr(
        doctor, "http_get_json", lambda url, timeout=2.5, headers=None: (401, None)
    )
    row = doctor.check_aigw("http://127.0.0.1:8000/v1")
    assert row["color"] == doctor.RED
    assert "rejected our credential" in row["detail"]
    # The row says *where* the credential came from, never what it is.
    assert row["credential"].startswith(("env:", "gateway config", "development default"))
