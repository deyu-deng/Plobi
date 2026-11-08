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
    # The leg wrote one window, not a menu — so Plobi offers the budget it can honour
    # (用户 11-08 20:4x 拍板「Plobi 侧预算档」): a few rungs under that model's own
    # ceiling, with the ceiling itself as the top rung (so nothing changes until the
    # user picks a smaller one).
    assert caps["antigravity/claude-opus-5-5"]["context"] == {
        "adjustable": True,
        "choices": [32768, 65536, 131072, 262144, 524288, 1000000],
        "default": 1000000,
        "max": 1000000,
    }, caps
    # One level is not a menu, but it is still what the model runs at.
    assert caps["antigravity/gpt-oss-120b-medium"]["efforts"] == ["medium"], caps
    # Kiro published limits, not levels: the generic `reasoning` answer stands.
    assert "efforts" not in caps["kiro_cli/deepseek-3.2"], caps
    assert caps["kiro_cli/deepseek-3.2"]["context"] == {
        "adjustable": True,
        "choices": [32768, 65536, 131072, 164000],
        "default": 164000,
        "max": 164000,
    }, caps
    # A model the hub said nothing about keeps the pre-existing shape for the levels,
    # and gets a budget from Plobi's own table (glm ⇒ 202752) rather than from nobody.
    assert caps["workbuddy/glm-5.3"].get("efforts") is None, caps
    assert caps["workbuddy/glm-5.3"]["context"] == {
        "adjustable": True,
        "choices": [32768, 65536, 131072, 202752],
        "default": 202752,
        "max": 202752,
    }, caps
    # Another provider's rows are untouched by the hub's answers — but they still get
    # a window menu of their own (this model is in nobody's catalog, so the ceiling is
    # Plobi's blanket fallback).
    assert "efforts" not in rows[1]["capabilities"]["anthropic/claude-opus-4.8"]
    assert rows[1]["capabilities"]["anthropic/claude-opus-4.8"]["context"]["adjustable"] is True


def test_a_vendor_menu_goes_through_untouched(monkeypatch):
    """Qoder sells 200K / 400K / 1M for one model: that is the truth, not a budget."""
    from plobi_cli import inventory

    _patch_catalog(monkeypatch, {
        "qoder/qwen3.8-max": {
            "context": {"adjustable": True, "choices": [200000, 400000, 1000000],
                        "default": 200000, "max": 1000000},
        },
    })
    rows = [{"slug": "aigw", "name": "订阅额度", "models": ["qoder/qwen3.8-max"]}]

    inventory._apply_capabilities(rows)

    assert rows[0]["capabilities"]["qoder/qwen3.8-max"]["context"] == {
        "adjustable": True, "choices": [200000, 400000, 1000000],
        "default": 200000, "max": 1000000,
    }


def test_a_ceiling_with_one_rung_left_has_no_menu(monkeypatch):
    """A model whose own window leaves a single rung has nothing to choose — and a
    rung above its real window would be a promise the turn cannot keep."""
    from plobi_cli import inventory

    _patch_catalog(monkeypatch, {"tiny/tiny-1": {"context": {"max": 16384}}})
    rows = [{"slug": "aigw", "name": "订阅额度", "models": ["tiny/tiny-1"]}]

    inventory._apply_capabilities(rows)

    context = rows[0]["capabilities"]["tiny/tiny-1"].get("context")
    assert context is None or len(context.get("choices") or []) < 2, context


def test_the_budget_ceiling_is_asked_without_the_network():
    """The ladder is built on every open of the model menu, for every model — so the
    ceiling comes from the local table (no probe chain: ~1.5 s per model otherwise)."""
    from agent.model_metadata import local_context_length

    assert local_context_length("workbuddy/glm-5.3") == 202752
    assert local_context_length("workbuddy/hy3") == 262144
    # Nobody told us about this one: the blanket fallback, which is the same window
    # the runtime ends up with.
    assert local_context_length("workbuddy/hy4-preview") == 256000


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
