"""The cheap tier must name a model we can actually price (裁定 95).

Why this exists: `glm-4-air` is NOT dead — measured on this box it is served by
the same key and really returns `tool_calls`. What it lacks is a row in the
pricing table (`agent/usage_pricing.py:637-640` prices only routes actually seen
in the usage table), so every 早报 / 记账 line built on it reports "unknown".
That is the ONLY reason the default moved to `glm-5.3-flash`, and these two
invariants are what keep it from drifting back to a cheaper-looking but unpriced
id, or to a config whose own example contradicts it.
"""

from __future__ import annotations

import re

from agent.usage_pricing import has_known_pricing
from plobi.quota import config as quota_config


def _cheap_sources() -> dict:
    return {
        name: src
        for name, src in quota_config.DEFAULT_SOURCES.items()
        if str(src.get("kind") or "") == "cheap_api"
    }


def test_every_cheap_source_names_a_model_with_a_known_price():
    sources = _cheap_sources()
    assert sources, "DEFAULT_SOURCES lost its cheap tier — the pool would have no fallback"

    unpriced = [
        f"{name}.model={src.get('model')!r} (provider={src.get('credential_provider')!r})"
        for name, src in sources.items()
        if not has_known_pricing(
            str(src.get("model") or ""), src.get("credential_provider") or None
        )
    ]
    assert not unpriced, (
        "cheap-tier model(s) with no pricing entry would make every cost report "
        f"read unknown: {unpriced}"
    )


def test_the_module_example_does_not_contradict_the_effective_model():
    """The YAML sample in the module docstring is what an operator copies.

    If it drifts from DEFAULT_SOURCES the docs start lying about which model is
    in force, which is exactly the failure 裁定 95 called out when it demanded
    both sites change in one commit.
    """
    documented = re.findall(r"^\s+model:\s*(\S+)\s*(?:#.*)?$", quota_config.__doc__ or "", re.M)
    effective = [str(src.get("model") or "") for src in _cheap_sources().values()]

    assert documented, "the docstring example no longer shows a `model:` line at all"
    assert set(documented) == set(effective), (
        f"docstring advertises {sorted(set(documented))} but the pool runs {sorted(set(effective))}"
    )


def test_load_still_resolves_the_cheap_source(monkeypatch, tmp_path):
    """A rename that broke resolution would look like a dead quota source."""
    monkeypatch.delenv("PLOBI_QUOTA_CONFIG", raising=False)
    monkeypatch.setattr(quota_config, "config_path", lambda: tmp_path / "absent.yaml")

    cfg = quota_config.load()
    source = cfg["sources"]["zhipu-air"]

    assert source["kind"] == "cheap_api"
    assert source["model"] == quota_config.DEFAULT_SOURCES["zhipu-air"]["model"]
    assert cfg["order"] and cfg["order"][0] == "zhipu-air"
