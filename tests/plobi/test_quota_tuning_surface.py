"""The hub's per-model answers ride all the way to the picker — and to the window.

Three hops, one contract (`tuning`, written by the leg that read the vendor's own model
list):

    quota_hub `/v1/models` row  →  plobi.quota.gateway  →  plobi_cli.inventory caps
                                                      ↘  agent.model_metadata window

Two failure shapes this pins, both measured here on 2026-11-01:

* the picker showed five thinking levels for a model the vendor sells at three, because
  nothing about the model ever reached the UI;
* every subscription model resolved to the 256K fallback, because the loopback gateway
  counts as a *known* provider URL, so the generic `/models` probe was skipped — while
  the vendor had already written the real number (Antigravity's Claude 1M, Kiro's
  deepseek 164K). That number decides when Plobi compresses.

No test here reaches the network: the catalog read goes through the `open_url` seam.
"""
from __future__ import annotations

import json
import urllib.error

import pytest

from plobi.quota import gateway
from plobi.quota.gateway import catalog_tuning, sources_from_catalog

CATALOG = {
    "data": [
        {"id": "antigravity/claude-opus-5-5", "provider": "antigravity",
         "capabilities": {"stream": True, "tools": True}, "live": True, "selectable": True,
         "tuning": {"efforts": ["low", "medium", "high"], "default_effort": "medium",
                    "context": {"max": 1000000}, "max_output": 128000}},
        {"id": "antigravity/gpt-oss-120b-medium", "provider": "antigravity",
         "capabilities": {"stream": True, "tools": True}, "live": True, "selectable": True,
         "tuning": {"efforts": ["medium"], "default_effort": "medium",
                    "context": {"max": 131072}, "max_output": 32768}},
        {"id": "kiro_cli/deepseek-3.2", "provider": "kiro_cli",
         "capabilities": {"stream": True, "tools": True}, "live": True, "selectable": True,
         "tuning": {"context": {"max": 164000}, "max_output": 64000}},
        # A leg that publishes no answer for a model: the row stays without `tuning`.
        {"id": "workbuddy/glm-5.3", "provider": "workbuddy",
         "capabilities": {"stream": True, "tools": True}, "live": True, "selectable": True},
    ]
}


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(payload, counter=None):
    def open_url(request, timeout):
        if counter is not None:
            counter.append(1)
        return _Resp(payload)

    return open_url


@pytest.fixture(autouse=True)
def _clean_cache():
    gateway._TUNING_CACHE.clear()
    yield
    gateway._TUNING_CACHE.clear()


def test_the_derived_source_keeps_per_model_answers_separate_from_per_leg_claims():
    sources, seen = sources_from_catalog(CATALOG, base_url="http://127.0.0.1:8000/v1", api_key="k")

    assert seen == 4
    agy = sources["antigravity"]["model_tuning"]
    assert agy["antigravity/claude-opus-5-5"]["efforts"] == ["low", "medium", "high"]
    assert agy["antigravity/gpt-oss-120b-medium"]["efforts"] == ["medium"]
    # A leg with no row-level answer contributes nothing rather than an empty stub.
    assert sources["workbuddy"]["model_tuning"] == {}
    assert "efforts" not in sources["kiro_cli"]["model_tuning"]["kiro_cli/deepseek-3.2"]


def test_catalog_tuning_flattens_the_hub_and_caches_the_read():
    calls: list[int] = []

    tuning = catalog_tuning("http://127.0.0.1:8000/v1", "k", open_url=_opener(CATALOG, calls))

    assert set(tuning) == {"antigravity/claude-opus-5-5", "antigravity/gpt-oss-120b-medium",
                           "kiro_cli/deepseek-3.2"}, sorted(tuning)
    assert tuning["antigravity/claude-opus-5-5"]["context"] == {"max": 1000000}
    assert calls == [1]

    # The cached path is what the picker uses on every open: one read per TTL, not one
    # per keystroke.
    catalog_tuning("http://127.0.0.1:8000/v1", "k")
    assert calls == [1], "the second call should have come from the cache"


def test_an_unreachable_hub_answers_nothing_rather_than_wrong_nothing():
    def boom(request, timeout):
        raise urllib.error.URLError("connection refused")

    assert catalog_tuning("http://127.0.0.1:8000/v1", "k", open_url=boom) == {}


def _patch_catalog(monkeypatch, answer):
    """Patch the seam both consumers import by name."""
    monkeypatch.setattr(gateway, "catalog_tuning", lambda base_url, api_key="": answer)


def test_the_picker_row_carries_the_levels_and_the_window(monkeypatch):
    """`model.options` caps are what the popover gates on: a level the vendor does not
    sell must not be offered, and a leg that said nothing keeps the old behaviour."""
    from plobi_cli import inventory

    _patch_catalog(monkeypatch, catalog_tuning("http://x", open_url=_opener(CATALOG)))
    rows = [
        {"slug": "aigw", "name": "订阅额度", "models": [
            "antigravity/claude-opus-5-5", "antigravity/gpt-oss-120b-medium",
            "kiro_cli/deepseek-3.2", "workbuddy/glm-5.3"]},
        {"slug": "openrouter", "name": "OpenRouter", "models": ["anthropic/claude-opus-4.8"]},
    ]

    inventory._apply_capabilities(rows)

    caps = rows[0]["capabilities"]
    assert caps["antigravity/claude-opus-5-5"]["efforts"] == ["low", "medium", "high"], caps
    assert caps["antigravity/claude-opus-5-5"]["default_effort"] == "medium", caps
    assert caps["antigravity/claude-opus-5-5"]["context"] == {"max": 1000000}, caps
    # One level is not a menu, but it is still what the model runs at.
    assert caps["antigravity/gpt-oss-120b-medium"]["efforts"] == ["medium"], caps
    # Kiro published limits, not levels: the generic `reasoning` answer stands.
    assert "efforts" not in caps["kiro_cli/deepseek-3.2"], caps
    assert caps["kiro_cli/deepseek-3.2"]["context"] == {"max": 164000}, caps
    # A model the hub said nothing about keeps the pre-existing shape.
    assert caps["workbuddy/glm-5.3"].get("efforts") is None, caps
    # Another provider's rows are untouched by the hub's answers.
    assert "efforts" not in rows[1]["capabilities"]["anthropic/claude-opus-4.8"]


def test_the_published_window_is_what_plobi_budgets_the_conversation_against(monkeypatch):
    """The hub's number wins over the 256K fallback — and only for the hub's own rows."""
    from agent.model_metadata import get_model_context_length

    _patch_catalog(monkeypatch, {
        "antigravity/claude-opus-5-5": {"context": {"max": 1000000}},
        "kiro_cli/deepseek-3.2": {"context": {"max": 164000}},
    })

    assert get_model_context_length(
        "antigravity/claude-opus-5-5", base_url="http://127.0.0.1:8000/v1", provider="aigw"
    ) == 1000000
    assert get_model_context_length(
        "kiro_cli/deepseek-3.2", base_url="http://127.0.0.1:8000/v1", provider="aigw"
    ) == 164000
    # A model the hub has no answer for is not a reason to claim a window.
    assert get_model_context_length(
        "antigravity/unknown-9", base_url="http://127.0.0.1:8000/v1", provider="aigw"
    ) != 1000000


def test_a_hub_that_is_down_does_not_change_the_window_resolution(monkeypatch):
    from agent.model_metadata import get_model_context_length

    def raisey(base_url, api_key=""):
        raise RuntimeError("hub is down")

    monkeypatch.setattr(gateway, "catalog_tuning", raisey)

    answered = get_model_context_length(
        "antigravity/claude-opus-5-5", base_url="http://127.0.0.1:8000/v1", provider="aigw"
    )

    assert isinstance(answered, int) and answered > 0, "the fallback chain still answers"
